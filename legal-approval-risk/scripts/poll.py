# -*- coding: utf-8 -*-
"""合同审批风险轮询技能入口(zcode / Cursor / Hermes 同一套)。

子命令:
  check                         体检飞书 + 企查查 + MySQL
  poll [--force]                扫描 PENDING 合同审批,取风险,评论回实例(文本+Excel,幂等)
  test-instance <instance_code> 干跑单实例:解析三要素+取风险,不写评论
  comment-instance <code>       补写/改写一条实例的风险评论(始终查企查查最新)

用法:
  py scripts/poll.py check
  py scripts/poll.py poll
  py scripts/poll.py test-instance 81D31358-93AF-92D6-7425-01A5D67C4E71

输出均为 UTF-8 JSON。错误时打印 {"ok": false, "error": "..."} 并以非零码退出。
企业已在表单里选定,本技能不做 886 模糊匹配。每次查询企查查最新,约 7.50 元/家。
"""
from __future__ import annotations

import json
import os
import sys
import time

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.config import check_all, get_feishu  # noqa: E402
from common import paths  # noqa: E402
from common.company_store import store_from_env  # noqa: E402
from common.snapshot import save as save_snapshot  # noqa: E402
from common.state import StateStore  # noqa: E402
from feishu.client import FeishuApiError, FeishuClient, extract_company_fields  # noqa: E402
from qcc.aggregator import default_aggregator  # noqa: E402
from qcc.grading import grade_risk  # noqa: E402
from qcc.report import build_report  # noqa: E402

ACTOR = "legal-approval-risk"
_LEVEL_ZH = {"low": "低", "medium": "中", "high": "高", "unknown": "未知"}
_MYSQL_HINT = (
    "远程 MySQL 为必选:请在技能根目录复制 .env.example 为 .env,"
    "补齐 MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB 后再用"
)


def _emit(obj):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _json_report(rep):
    out = []
    for item in rep:
        out.append({
            "group": item["group"],
            "vars": [{"name": n, "present": p} for n, p in item["vars"]],
            "all_present": item["all_present"],
            "ask": item["ask"],
        })
    return out


def _missing_creds_error(rep):
    missing = []
    asks = []
    for item in rep:
        if item["all_present"]:
            continue
        for name, present in item["vars"]:
            if not present:
                missing.append(name)
        if item.get("ask"):
            asks.append(item["ask"])
    err = "凭据缺失,请补齐: %s" % ", ".join(missing)
    if asks:
        err += "。%s" % "；".join(asks)
    return err


def _require_mysql_store():
    ok, rep = check_all()
    mysql_ok = all(item["all_present"] for item in rep if "MySQL" in item["group"])
    if not mysql_ok:
        _emit({"ok": False, "error": _MYSQL_HINT, "report": _json_report(rep)})
        return None
    try:
        store = store_from_env()
    except Exception as e:
        _emit({"ok": False, "error": "MySQL 连接失败: %s: %s" % (type(e).__name__, e)})
        return None
    if store is None:
        _emit({"ok": False, "error": _MYSQL_HINT, "report": _json_report(rep)})
        return None
    return store


def _request_params(code, name, credit_code="", legal_person=""):
    if code == 856:
        return {"credit_code": credit_code, "company_name": name, "oper_name": legal_person}
    return {"search_key": name}


def fetch_or_cache_profile(store, name, credit_code, legal_person, force_refresh=False):
    """先查 MySQL;未命中再调企查查,落快照+写库+出 Excel。返回 (profile_dict, error)。"""
    if not force_refresh:
        try:
            cached = store.get_latest(credit_code=credit_code or "", name=name or "")
        except Exception as e:
            return None, "MySQL 读取失败: %s: %s" % (type(e).__name__, e)
        if cached is not None:
            try:
                report_file = build_report(
                    cached.name, cached.payloads, paths.reports_dir(),
                    profile={
                        "query_date": cached.query_date,
                        "risk_level": cached.risk_level,
                        "hits": cached.hits,
                        "called_apis": cached.called_apis,
                        "cost": cached.cost,
                    },
                )
            except Exception as e:
                return None, "Excel 生成失败: %s: %s" % (type(e).__name__, e)
            return {
                "name": cached.name,
                "credit_code": cached.credit_code,
                "legal_person": cached.legal_person,
                "status": cached.status,
                "three_element_ok": cached.three_element_ok,
                "risk_level": cached.risk_level,
                "hits": cached.hits,
                "called_apis": cached.called_apis,
                "cost": cached.cost,
                "snapshot_files": [],
                "report_file": report_file,
                "query_date": cached.query_date,
                "source": "cache",
            }, None

    agg = default_aggregator(actor=ACTOR)
    try:
        p = agg.fetch_profile(name, credit_code, legal_person)
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)

    grading = grade_risk(p.payloads)
    snapshot_files = {}
    for code, payload in (p.payloads or {}).items():
        path = save_snapshot(
            int(code), p.name,
            _request_params(int(code), p.name, p.credit_code, p.legal_person),
            payload, actor=ACTOR, grading=grading,
        )
        snapshot_files[int(code)] = path
    p.snapshot_files = snapshot_files

    try:
        store.insert(p, actor=ACTOR)
    except Exception as e:
        report_file = ""
        try:
            report_file = build_report(
                p.name, p.payloads, paths.reports_dir(),
                profile={
                    "query_date": p.query_date,
                    "risk_level": p.risk_level,
                    "hits": p.hits,
                    "called_apis": p.called_apis,
                    "cost": p.cost,
                },
            )
        except Exception:
            pass
        return None, "MySQL 写入失败: %s: %s%s" % (
            type(e).__name__, e,
            (" report_file=" + report_file) if report_file else "",
        )

    try:
        report_file = build_report(
            p.name, p.payloads, paths.reports_dir(),
            profile={
                "query_date": p.query_date,
                "risk_level": p.risk_level,
                "hits": p.hits,
                "called_apis": p.called_apis,
                "cost": p.cost,
            },
        )
    except Exception as e:
        return None, "Excel 生成失败: %s: %s" % (type(e).__name__, e)

    return {
        "name": p.name,
        "credit_code": p.credit_code,
        "legal_person": p.legal_person,
        "status": p.status,
        "three_element_ok": p.three_element_ok,
        "risk_level": p.risk_level,
        "hits": p.hits,
        "called_apis": p.called_apis,
        "cost": p.cost,
        "snapshot_files": list(snapshot_files.keys()),
        "report_file": report_file,
        "query_date": p.query_date,
        "source": p.source,
    }, None


def _usable_comment_id(raw):
    s = str(raw or "").strip()
    if not s or s in ("0", "None"):
        return ""
    return s


def format_risk_comment(profile):
    """审批评论只保留日期、评级、命中项；明细看附件 Excel。"""
    hits = profile.get("hits") or []
    hit_lines = []
    for h in hits[:20]:
        cat = h.get("category") or ""
        reason = h.get("reason") or ""
        count = h.get("count")
        line = "- %s: %s" % (cat, reason)
        if count not in (None, ""):
            line += " (共%s条)" % count
        hit_lines.append(line)
    if not hit_lines:
        hit_lines = ["- 无命中项"]
    level_zh = _LEVEL_ZH.get(profile.get("risk_level"), profile.get("risk_level") or "未知")
    return "\n".join([
        "查询日期：%s" % (profile.get("query_date") or "—"),
        "风险评级：%s" % level_zh,
        "风险命中项：",
        *hit_lines,
        "",
        "完整风险信息见附件 Excel",
    ])


def _comment_text(comment):
    if not isinstance(comment, dict):
        return ""
    content = comment.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return content
    if isinstance(content, dict):
        return content.get("text") or ""
    return ""


def find_existing_risk_comment_id(client, instance_code, cfg, fallback=""):
    cid = _usable_comment_id(fallback)
    if cid:
        return cid
    try:
        comments = client.list_comments(
            instance_code,
            user_id=cfg.get("comment_user_id") or "",
            user_id_type=cfg.get("comment_user_id_type") or "open_id",
        )
    except Exception:
        return ""
    for item in comments:
        text = _comment_text(item)
        if (
            "完整风险信息见附件 Excel" in text
            or "【签约对象风险预警】" in text
            or "contract-risk-poll" in text
        ):
            return _usable_comment_id(item.get("id") or item.get("comment_id"))
    return ""


def post_risk_comment(client, instance_code, profile, cfg, comment_id=""):
    """写文本风险评级总结 + 上传 Excel 完整报告作为评论附件。"""
    report = profile.get("report_file") or ""
    if not report or not os.path.isfile(report):
        raise RuntimeError("风险 Excel 未生成,无法写带附件的评论")
    uploaded = client.upload_approval_file(report)
    files = [{
        "url": uploaded["url"],
        "fileSize": uploaded["fileSize"],
        "title": uploaded["title"],
        "type": "attachment",
    }]
    text = format_risk_comment(profile)
    kwargs = dict(
        user_id=cfg.get("comment_user_id") or "",
        user_id_type=cfg.get("comment_user_id_type") or "open_id",
        comment_id=_usable_comment_id(comment_id),
    )
    try:
        return client.add_comment(instance_code, text, files=files, **kwargs)
    except FeishuApiError:
        text += "\n附件直链（约12小时有效）：%s" % uploaded["url"]
        return client.add_comment(instance_code, text, files=None, **kwargs)


def _window_ms(days):
    days = min(max(int(days or 30), 1), 30)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    return start_ms, end_ms


def _company_from_instance(detail, cfg):
    company = extract_company_fields(
        detail.get("form_items") or [],
        cfg["field_company"],
    )
    return company


def cmd_check():
    ok, rep = check_all()
    payload = {"ok": ok, "report": _json_report(rep)}
    if not ok:
        payload["error"] = _missing_creds_error(rep)
        _emit(payload)
        return 1
    try:
        store = store_from_env()
        if store is None:
            payload["ok"] = False
            payload["error"] = _MYSQL_HINT
            _emit(payload)
            return 1
        payload["mysql_connected"] = True
    except Exception as e:
        payload["ok"] = False
        payload["mysql_connected"] = False
        payload["error"] = "MySQL 连接失败: %s: %s" % (type(e).__name__, e)
        _emit(payload)
        return 1
    try:
        cfg = get_feishu()
        client = FeishuClient(cfg["app_id"], cfg["app_secret"])
        client.tenant_access_token()
        payload["feishu_token"] = True
        payload["approval_codes"] = cfg["approval_codes"]
    except Exception as e:
        payload["ok"] = False
        payload["feishu_token"] = False
        payload["error"] = "飞书取 token 失败: %s: %s" % (type(e).__name__, e)
        _emit(payload)
        return 1
    _emit(payload)
    return 0


def cmd_test_instance(instance_code):
    ok, rep = check_all()
    if not ok:
        _emit({"ok": False, "error": _missing_creds_error(rep), "report": _json_report(rep)})
        return 1
    store = _require_mysql_store()
    if store is None:
        return 1
    cfg = get_feishu()
    client = FeishuClient(cfg["app_id"], cfg["app_secret"])
    try:
        detail = client.get_instance(instance_code)
    except Exception as e:
        _emit({"ok": False, "error": "读取审批实例失败: %s: %s" % (type(e).__name__, e)})
        return 1
    company = _company_from_instance(detail, cfg)
    if company["missing"]:
        _emit({
            "ok": False,
            "error": "表单缺少企业字段: %s" % ", ".join(company["missing"]),
            "instance_code": instance_code,
            "status": detail.get("status"),
            "company": company,
        })
        return 1
    profile, err = fetch_or_cache_profile(
        store, company["name"], company["credit_code"], company["legal_person"],
        force_refresh=True,
    )
    if err:
        _emit({"ok": False, "error": err, "company": company, "instance_code": instance_code})
        return 1
    _emit({
        "ok": True,
        "instance_code": instance_code,
        "status": detail.get("status"),
        "company": company,
        "profile": profile,
        "comment_preview": format_risk_comment(profile),
        "commented": False,
    })
    return 0


def _process_one(client, store, state, instance_code, cfg, dry_run, force=False):
    """处理单实例。返回 (record_or_none, skip_or_none, error_or_none)。"""
    prev = state.get(instance_code) or {}
    if not force and state.is_processed(instance_code):
        return None, {"instance_code": instance_code, "reason": "already_processed"}, None
    try:
        detail = client.get_instance(instance_code)
    except Exception as e:
        return None, None, {"instance_code": instance_code, "error": "%s: %s" % (type(e).__name__, e)}
    status = detail.get("status")
    if status and status != "PENDING":
        return None, {"instance_code": instance_code, "reason": "not_pending", "status": status}, None
    company = _company_from_instance(detail, cfg)
    if company["missing"]:
        return None, None, {
            "instance_code": instance_code,
            "error": "表单缺少企业字段: %s" % ", ".join(company["missing"]),
            "company": company,
        }
    profile, err = fetch_or_cache_profile(
        store, company["name"], company["credit_code"], company["legal_person"],
        force_refresh=True,
    )
    if err:
        return None, None, {"instance_code": instance_code, "error": err, "company": company}
    # 低/中/高均写评论,不因风险等级跳过;评论=文本评级总结+完整风险 Excel
    comment_id = ""
    commented = False
    if not dry_run:
        try:
            existing = find_existing_risk_comment_id(
                client, instance_code, cfg, fallback=prev.get("comment_id") or "",
            )
            comment_id = post_risk_comment(
                client, instance_code, profile, cfg, comment_id=existing,
            ) or existing
            commented = True
        except Exception as e:
            return None, None, {
                "instance_code": instance_code,
                "error": "写评论失败: %s: %s" % (type(e).__name__, e),
                "company": company,
                "risk_level": profile.get("risk_level"),
            }
        state.mark(
            instance_code,
            risk_level=profile.get("risk_level") or "",
            comment_id=comment_id or "",
            company=profile.get("name") or company["name"],
        )
    return {
        "instance_code": instance_code,
        "company": profile.get("name") or company["name"],
        "credit_code": profile.get("credit_code") or company["credit_code"],
        "risk_level": profile.get("risk_level"),
        "source": profile.get("source"),
        "cost": profile.get("cost"),
        "report_file": profile.get("report_file") or "",
        "commented": commented,
        "comment_id": comment_id,
        "dry_run": dry_run,
    }, None, None


def cmd_comment_instance(instance_code):
    """补写/改写一条实例的风险评论(始终查企查查最新)。"""
    ok, rep = check_all()
    if not ok:
        _emit({"ok": False, "error": _missing_creds_error(rep), "report": _json_report(rep)})
        return 1
    store = _require_mysql_store()
    if store is None:
        return 1
    cfg = dict(get_feishu())
    client = FeishuClient(cfg["app_id"], cfg["app_secret"])
    rec, skip, err = _process_one(
        client, store, StateStore(), instance_code, cfg, dry_run=False, force=True,
    )
    if err:
        _emit({"ok": False, **err})
        return 1
    if skip:
        _emit({"ok": False, "error": "无法评论", **skip})
        return 1
    _emit({"ok": True, **rec})
    return 0


def cmd_poll(force=False):
    ok, rep = check_all()
    if not ok:
        _emit({"ok": False, "error": _missing_creds_error(rep), "report": _json_report(rep)})
        return 1
    store = _require_mysql_store()
    if store is None:
        return 1
    cfg = get_feishu()
    if not cfg["approval_codes"]:
        _emit({"ok": False, "error": "FEISHU_APPROVAL_CODES 为空,请填入合同审批定义 code"})
        return 1
    client = FeishuClient(cfg["app_id"], cfg["app_secret"])
    state = StateStore()
    start_ms, end_ms = _window_ms(cfg.get("query_window_days"))
    scanned = []
    errors = []
    for code in cfg["approval_codes"]:
        try:
            found = client.query_pending_instances(code, start_ms, end_ms)
        except Exception as e:
            errors.append({"approval_code": code, "error": "%s: %s" % (type(e).__name__, e)})
            continue
        scanned.extend(found)
    # 去重保持顺序
    seen = set()
    unique = []
    for inst in scanned:
        if inst in seen:
            continue
        seen.add(inst)
        unique.append(inst)

    processed, skipped = [], []
    dry_run = bool(cfg.get("comment_dry_run"))
    for inst in unique:
        rec, skip, err = _process_one(client, store, state, inst, cfg, dry_run, force=force)
        if rec:
            processed.append(rec)
        elif skip:
            skipped.append(skip)
        elif err:
            errors.append(err)

    _emit({
        "ok": len(errors) == 0,
        "scanned": len(unique),
        "new": len(processed),
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
        "approval_codes": cfg["approval_codes"],
    })
    return 0 if not errors else 1


def main(argv):
    if len(argv) < 2:
        _emit({
            "ok": False,
            "error": "用法: poll.py check | poll [--force] | test-instance <instance_code> | comment-instance <instance_code>",
        })
        return 1
    sub = argv[1]
    if sub == "check":
        return cmd_check()
    if sub == "poll":
        return cmd_poll(force="--force" in argv[2:])
    if sub == "test-instance":
        if len(argv) < 3:
            _emit({"ok": False, "error": "test-instance 需要 <instance_code>"})
            return 1
        return cmd_test_instance(argv[2])
    if sub == "comment-instance":
        if len(argv) < 3:
            _emit({"ok": False, "error": "comment-instance 需要 <instance_code>"})
            return 1
        return cmd_comment_instance(argv[2])
    _emit({"ok": False, "error": "未知子命令: %s" % sub})
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
