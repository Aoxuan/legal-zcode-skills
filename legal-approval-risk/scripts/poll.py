# -*- coding: utf-8 -*-
"""合同审批风险预审技能入口(zcode / Cursor / Hermes 同一套)。

本技能作为审批流里「系统风险预审」节点的审批人:扫描该员工账号在合同审批定义下
的待办任务,从表单企业控件(外部选项,值「名称｜信用代码｜法人」)取对方企业三要素,
先查 MySQL 近 N 天(默认 90)缓存,未命中再调企查查风险接口并落库,生成 Excel 风险
报告,随后用 tasks/approve 的 form 回填【企业风险评级】文本字段(低/中/高均把 Excel
报告写入「风险报告附件」专用控件,整体设值只保留报告,申请人误传文件一并删除;
无报告场景若该控件被误传过文件则清空),并同意放行给法务。
对方是自然人时(缓存未命中后查 dim_company 判定)不查企查查,回填
「自然人无须评估风险」。实例表单无任何企业控件(如达人解约且不涉及达人公司,
企业控件为条件显示)时免扫描直接放行:【企业风险评级】回填
「无须风险扫描（无合作企业）」,实例与定义表单都定位不到评级控件时仅评论放行
(2026-09-08 口径)。

合同主体一致性校验(2026-10-19 口径,三次修正):企业实例先下载「合同附件」Word,
解析签约抬头(甲/乙/丙多方,含表格内段落与修订插入;旧版 .doc 走 UTF-16LE 扫描),
**统一按「签约主体」排除法**甄别对方——排除我方签约主体后:剩唯一公司主体→与表单
企业控件归一化比对;仅剩自然人(合同对方是个人而表单填了企业)→形态不符直接拒绝;
剩 0 或多个公司主体→无法唯一确定。比对不一致或形态不符均直接 tasks/reject 拒绝
退回(先于缓存与企查查,零计费;reject 不支持 form 回填,原因写审批意见);仅附件
缺失/解析失败(PDF、图片等)/无法唯一确定时放行并回填「合同主体未能自动校验，请
法务人工核对」。FEISHU_SUBJECT_CHECK=0 可关闭回到旧行为。

子命令:
  check                         体检飞书 + 企查查 + MySQL + 预审账号
  poll [--force]                扫描预审账号待办任务,取风险,回填评级(低/中/高附报告)并同意(幂等)
  test-instance <instance_code> 干跑单实例:解析三要素+定位回填控件+取风险,不 approve
  process-instance <code>       补处理单条:在预审账号待办里找到该实例并 approve 回填

用法:
  py scripts/poll.py check
  py scripts/poll.py poll
  py scripts/poll.py test-instance 81D31358-93AF-92D6-7425-01A5D67C4E71

输出均为 UTF-8 JSON。错误时打印 {"ok": false, "error": "..."} 并以非零码退出。
企业三要素从表单外部选项控件取出;字段名不固定(对方单位名称/相对方企业名称/
签约企业名称/达人公司名称),由 FEISHU_FIELD_COMPANY 配置。风险信息库缓存优先
(默认 90 天),未命中才查企查查最新(约 7.50 元/家)。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.config import check_all, get_feishu  # noqa: E402
from common import paths  # noqa: E402
from common.company_store import store_from_env  # noqa: E402
from common.contract_subject import (  # noqa: E402
    ContractParseError, names_match, parse_contract_subjects, resolve_counterparty,
)
from common.snapshot import save as save_snapshot  # noqa: E402
from common.state import StateStore  # noqa: E402
from feishu.client import FeishuClient, extract_companies, find_control  # noqa: E402
from qcc.aggregator import default_aggregator  # noqa: E402
from qcc.grading import grade_risk  # noqa: E402
from qcc.report import build_report  # noqa: E402

ACTOR = "legal-approval-risk"
_LEVEL_ZH = {"low": "低风险", "medium": "中风险", "high": "高风险"}
# 低/中/高风险都把 Excel 报告写入「风险报告附件」控件;未知/自然人/兜底不附
_ATTACH_LEVELS = ("low", "medium", "high")
_REPORT_HINT = "，详见风险报告附件"
_UNKNOWN_TEXT = "未知（未查得风险数据）"
# 对方为自然人(dim_company 判定)时回填的固定文案,不查企查查、不附报告
_NATURAL_PERSON_RATING = "自然人无须评估风险"
_FALLBACK_RATING = "企业名称为空,风险查询接口不适用"
# 实例表单无任何企业控件(如达人解约且不涉及达人公司):免扫描直接放行的回填文案
# (2026-09-08 口径);定位不到评级控件时不回填,仅评论放行
_EXEMPT_RATING = "无须风险扫描（无合作企业）"
_EXEMPT_COMMENT = "风险预审完成：无合作企业，无须风险扫描，自动放行"
# ---- 合同主体一致性校验(2026-10-19)----
# 不一致直接拒绝退回(reject 不支持 form 回填,原因全写审批意见),零计费
_SUBJECT_MISMATCH_RISK = "subject_mismatch"
# 附件缺失/解析失败/无法唯一确定对方主体:放行交法务人工核对的回填文案
_SUBJECT_SKIP_RATING = "合同主体未能自动校验，请法务人工核对"
_SUBJECT_SKIP_COMMENT = "风险预审完成：合同主体未能自动校验，请法务人工核对，自动放行"
_MYSQL_HINT = (
    "远程 MySQL 为必选:请在技能根目录复制 .env.example 为 .env,"
    "补齐 MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB 后再用"
)
_APPROVER_HINT = (
    "预审审批账号缺失:请在审批流新增「系统风险预审」节点(审批人=该员工账号),"
    "并在 .env 填写 FEISHU_APPROVER_USER_ID(其 open_id,或复用 FEISHU_COMMENT_USER_ID)"
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


def fetch_or_cache_profile(store, name, credit_code, legal_person,
                            force_refresh=False, cache_days=90):
    """先查 MySQL 近 cache_days 天缓存;未命中再调企查查,落快照+写库+出 Excel。

    缓存未命中时先判自然人(dim_company):对方为个人则不查企查查,
    返回 risk_level=natural_person 的 profile(无报告无附件)。
    返回 (profile_dict, error)。profile_dict 含 report_file(Excel 路径)与 source。
    """
    if not force_refresh:
        try:
            cached = store.get_latest(
                credit_code=credit_code or "", name=name or "",
                max_age_days=int(cache_days or 0),
            )
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

    # 缓存未命中:先判自然人(dim_company),对方为个人不查企查查
    try:
        if store.is_natural_person(name, credit_code):
            return {
                "name": (name or ""), "credit_code": (credit_code or ""),
                "legal_person": (legal_person or ""), "status": "",
                "three_element_ok": None, "risk_level": "natural_person",
                "hits": [], "called_apis": [], "cost": 0,
                "snapshot_files": [], "report_file": "",
                "query_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "natural_person",
            }, None
    except Exception as e:
        return None, "dim_company 自然人判断失败: %s: %s" % (type(e).__name__, e)

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


def _rating_label(profile):
    """短评级标签(审批评论用):低风险/中风险/高风险/未知（未查得风险数据）/
    自然人无须评估风险。profile 为 None(无企业/缺三要素)时返回 ""。"""
    if profile is None:
        return ""
    level = profile.get("risk_level")
    if level == "natural_person":
        return _NATURAL_PERSON_RATING
    return _LEVEL_ZH.get(level, _UNKNOWN_TEXT)


def _rating_text(profile):
    """评级文案:高/中风险带「详见风险报告附件」提示;低风险/未知仅评级;
    自然人→固定文案。profile 为 None(无企业/缺三要素)时返回兜底文案。"""
    label = _rating_label(profile)
    if not label:
        return _FALLBACK_RATING
    if (profile or {}).get("risk_level") in ("medium", "high"):
        return label + _REPORT_HINT
    return label


def _find_control_with_fallback(form_items, name_keys, fallback_items=None):
    """先在实例表单定位控件;未命中再查审批定义表单(空值/后加控件常不出现在实例 form)。"""
    found = find_control(form_items, name_keys)
    if found:
        return found
    if fallback_items:
        return find_control(fallback_items, name_keys)
    return None


def _build_approve_form(form_items, cfg, rating_text, file_code, fallback_items=None,
                        clear_attachment=False):
    """组装 approve 用的 form:仅含本节点要回填的【企业风险评级】文本控件与「附件」控件。

    两个控件均整体设值,只保留本技能写入的内容(申请人误写/误传一并删除):
      - 评级控件:value=评级文案(始终写入,覆写申请人误写内容)
      - 附件控件 attachmentV2:value=[file_code](仅风险报告) ;
        file_code 为空且 clear_attachment 时 value=[](清空申请人误传文件)。
    返回 (form_list, located:{rating, attachment})。
    """
    form = []
    located = {}
    rating_ctl = _find_control_with_fallback(
        form_items, cfg.get("field_rating") or "企业风险评级", fallback_items,
    )
    located["rating"] = rating_ctl
    if not rating_ctl:
        raise RuntimeError(
            "表单未找到【企业风险评级】控件(配置名: %s)" % (cfg.get("field_rating") or "企业风险评级")
        )
    form.append({
        "id": rating_ctl["id"],
        "type": rating_ctl["type"] or "input",
        "value": rating_text,
    })
    if file_code or clear_attachment:
        att_ctl = _find_control_with_fallback(
            form_items, cfg.get("field_attachment") or "附件", fallback_items,
        )
        located["attachment"] = att_ctl
        if att_ctl:
            form.append({
                "id": att_ctl["id"],
                "type": att_ctl["type"] or "attachmentV2",
                "value": [file_code] if file_code else [],
            })
        else:
            located["attachment_warning"] = (
                "表单未找到【附件】控件(配置名: %s),已跳过附件回填"
                % (cfg.get("field_attachment") or "附件")
            )
    return form, located


def _attachment_has_value(form_items, cfg):
    """风险报告附件控件里是否有申请人误传的文件(实例表单值非空)。"""
    ctl = find_control(form_items, cfg.get("field_attachment") or "附件")
    if not ctl:
        return False
    raw = ctl.get("value")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = [raw] if raw.strip() else []
    return any(isinstance(u, str) and u.strip() for u in (raw or []))


def _attachment_urls(ctl):
    """附件控件的下载直链列表(attachmentV2 的 value 为 URL 数组;兼容字符串 JSON)。"""
    if not ctl:
        return []
    raw = ctl.get("value")
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (ValueError, TypeError):
            raw = [text]
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    out = []
    for item in raw:
        if isinstance(item, dict):
            item = item.get("url") or item.get("id") or ""
        if isinstance(item, str) and item.strip():
            out.append(item.strip().replace("\\u0026", "&"))
    return out


def _control_text(ctl):
    """控件单值文本(radioV2 固定选项的 value 为所选选项全称;list/dict 取首个标量)。"""
    if not ctl:
        return ""
    value = ctl.get("value")
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        for key in ("text", "value", "name"):
            if value.get(key) not in (None, ""):
                value = value[key]
                break
        else:
            value = ""
    return str(value or "").replace("\\u0026", "&").strip()


def _check_contract_subject(client, form_items, fallback_items, cfg, company):
    """校验合同 Word 里签约对方主体与表单企业控件一致(2026-10-19 口径)。

    在查 MySQL 缓存/企查查之前执行:不一致由调用方拒绝退回,零计费。
    返回 {status: match|mismatch|skip, contract_subject, our_entity, reason};
    skip = 附件缺失/下载失败/解析失败/无法唯一确定对方 → 调用方放行+人工核对提醒。
    """
    result = {"status": "skip", "contract_subject": "", "our_entity": "", "reason": ""}
    attach_ctl = _find_control_with_fallback(
        form_items, cfg.get("field_contract_attach") or "合同附件", fallback_items,
    )
    urls = _attachment_urls(attach_ctl)
    if not urls:
        result["reason"] = "no_attachment"
        return result
    result["our_entity"] = _control_text(_find_control_with_fallback(
        form_items, cfg.get("field_our_entity") or "签约主体", fallback_items,
    ))
    try:
        blob = client.download(urls[0])
    except Exception as e:
        result["reason"] = "download_error:%s: %s" % (type(e).__name__, e)
        return result
    try:
        subjects = parse_contract_subjects(blob)
    except ContractParseError as e:
        result["reason"] = "parse_error:%s" % e.code
        return result
    kind, subject = resolve_counterparty(
        subjects, result["our_entity"], company.get("field") or "")
    if kind == "unresolved":
        result["reason"] = "unresolved_counterparty"
        return result
    result["contract_subject"] = subject
    result["subject_kind"] = kind
    if kind == "natural_person":
        # 合同对方是自然人、表单企业控件填了企业 → 形态不符直接拒绝
        # (2026-10-19 三次口径:此前误入「无法校验」兜底放行导致漏拒)
        result["status"] = "mismatch"
        result["reason"] = "counterparty_is_person"
        return result
    result["status"] = "match" if names_match(subject, company.get("name")) else "mismatch"
    return result


def _run_subject_check(client, store, form_items, fallback_items, cfg, companies):
    """合同主体校验门:开关关/控件缺三要素→off;自然人→person(维持原放行口径)。

    返回 (subject_check, error);error 仅在 dim_company 判定失败时非空
    (与 fetch_or_cache_profile 同口径,不静默跳过)。
    """
    if not cfg.get("subject_check_enabled", True):
        return {"status": "off"}, None
    if not companies or companies[0].get("missing"):
        return {"status": "off"}, None
    company = companies[0]
    try:
        if store.is_natural_person(company["name"], company["credit_code"]):
            return {"status": "person"}, None
    except Exception as e:
        return None, "dim_company 自然人判断失败: %s: %s" % (type(e).__name__, e)
    return _check_contract_subject(client, form_items, fallback_items, cfg, company), None


def _reject_subject_mismatch(client, state, task, cfg, dry_run, company, check):
    """主体不一致:直接拒绝退回业务修改,不查企查查/不附报告(2026-10-19)。

    reject 不支持 form 回填,原因全部写审批意见;飞书自动向发起人推拒绝通知;
    拒绝后任务从待办消失(幂等)。
    """
    instance_code = task.get("instance_code") or ""
    task_id = task.get("task_id") or ""
    approval_code = task.get("approval_code") or ""
    subject = check.get("contract_subject") or "?"
    if check.get("subject_kind") == "natural_person":
        subject = "「%s」（自然人）" % subject
    else:
        subject = "「%s」" % subject
    comment = (
        "风险预审拒绝：合同签约对方为%s，与表单%s「%s」不一致，"
        "请核对修改后重新提交（未查询企业风险）"
        % (subject, company.get("field") or "企业", company.get("name") or "")
    )
    rejected = False
    if not dry_run:
        try:
            client.reject_task(
                approval_code, instance_code, task_id,
                cfg.get("approver_user_id") or "",
                user_id_type=cfg.get("approver_user_id_type") or "open_id",
                comment=comment,
            )
            rejected = True
        except Exception as e:
            return None, {"instance_code": instance_code,
                          "error": "拒绝审批任务失败: %s: %s" % (type(e).__name__, e)}
    rec = {
        "instance_code": instance_code,
        "task_id": task_id,
        "approval_code": approval_code,
        "field": company.get("field") or "",
        "company": company.get("name") or "",
        "credit_code": company.get("credit_code") or "",
        "risk_level": _SUBJECT_MISMATCH_RISK,
        "rating_text": "",
        "source": "subject_check",
        "cost": 0,
        "report_file": "",
        "file_code": "",
        "approved": False,
        "rejected": rejected,
        "dry_run": dry_run,
        "subject_check": {
            "status": check.get("status"),
            "contract_subject": check.get("contract_subject"),
            "our_entity": check.get("our_entity"),
        },
    }
    if not dry_run and rejected:
        state.mark(instance_code, risk_level=_SUBJECT_MISMATCH_RISK,
                   company=rec.get("company") or "", reason="rejected_subject_mismatch")
    return rec, None


def _companies_from_instance(detail, cfg):
    """按候选字段(如 对方单位名称/相对方企业名称/签约企业名称/达人公司名称)收集表单里已填的企业控件。"""
    return extract_companies(
        detail.get("form_items") or [],
        cfg.get("field_companies") or [],
    )


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
        if cfg.get("approval_codes_test"):
            payload["approval_codes_test"] = cfg["approval_codes_test"]
    except Exception as e:
        payload["ok"] = False
        payload["feishu_token"] = False
        payload["error"] = "飞书取 token 失败: %s: %s" % (type(e).__name__, e)
        _emit(payload)
        return 1
    _emit(payload)
    return 0


def cmd_test_instance(instance_code):
    """干跑单实例:解析三要素+定位回填控件+取风险(缓存优先),不 approve、不上传附件。"""
    ok, rep = check_all(require_approver=False)
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
    form_items = detail.get("form_items") or []
    companies = _companies_from_instance(detail, cfg)
    fallback_items = []
    approval_code = detail.get("approval_code") or ""
    if approval_code:
        try:
            fallback_items = client.get_approval_form(approval_code)
        except Exception:
            fallback_items = []
    rating_ctl = _find_control_with_fallback(
        form_items, cfg.get("field_rating") or "企业风险评级", fallback_items,
    )
    att_ctl = _find_control_with_fallback(
        form_items, cfg.get("field_attachment") or "附件", fallback_items,
    )
    located = {"rating": rating_ctl, "attachment": att_ctl}

    subject_check, serr = _run_subject_check(
        client, store, form_items, fallback_items, cfg, companies)
    if serr:
        _emit({"ok": False, "error": serr, "instance_code": instance_code})
        return 1

    profile = None
    company = None
    exempt = not companies
    if exempt:
        # 实例表单无任何企业控件(如达人解约):免扫描直接放行,poll 将回填固定文案
        rating_text = _EXEMPT_RATING
    elif companies[0].get("missing"):
        rating_text = _FALLBACK_RATING
        company = companies[0]
    elif subject_check.get("status") == "mismatch":
        # 干跑提示:正式 poll 将拒绝退回,不查企查查、不回填评级字段
        company = companies[0]
        rating_text = "主体不一致，将拒绝退回（不查企查查）"
    elif subject_check.get("status") == "skip":
        company = companies[0]
        rating_text = _SUBJECT_SKIP_RATING
    else:
        company = companies[0]
        profile, err = fetch_or_cache_profile(
            store, company["name"], company["credit_code"], company["legal_person"],
            force_refresh=False, cache_days=cfg.get("cache_days", 90),
        )
        if err:
            _emit({"ok": False, "error": err, "company": company, "instance_code": instance_code})
            return 1
        rating_text = _rating_text(profile)

    # 干跑不上传附件,form 预览只含评级文本;附件展示其定位与「将写入 [file_code]」
    if rating_ctl or not exempt:
        form_preview, _ = _build_approve_form(
            form_items, cfg, rating_text, file_code="", fallback_items=fallback_items,
        )
    else:
        # 免扫描实例且实例/定义表单都无【企业风险评级】控件:放行时仅评论,不回填字段
        form_preview = []
    _emit({
        "ok": True,
        "instance_code": instance_code,
        "status": detail.get("status"),
        "exempt": exempt,
        "companies": [
            {"field": c.get("field"), "name": c.get("name"), "credit_code": c.get("credit_code")}
            for c in companies
        ],
        "company": company,
        "profile": profile,
        "rating_text": rating_text,
        "subject_check": subject_check,
        "located_controls": {
            "rating": {k: rating_ctl.get(k) for k in ("id", "type", "name")} if rating_ctl else None,
            "attachment": {k: att_ctl.get(k) for k in ("id", "type", "name")} if att_ctl else None,
        },
        "form_preview": form_preview,
        "approve": False,
    })
    return 0


def _resolve_risk(store, companies, cfg, force):
    """从表单企业控件取三要素→缓存优先取风险→生成 Excel。返回 (profile, rating_text, file_code, err)。

    无企业或缺三要素:profile=None、rating=兜底、file_code=""(不附 Excel)。
    对方为自然人(缓存未命中,dim_company 判定):risk_level=natural_person、rating=固定文案。
    是否把 Excel 附到「附件」控件由 _process_one 按 _ATTACH_LEVELS 决定(Excel 总会生成)。
    """
    if not companies or companies[0].get("missing"):
        return None, _FALLBACK_RATING, "", None
    company = companies[0]
    profile, err = fetch_or_cache_profile(
        store, company["name"], company["credit_code"], company["legal_person"],
        force_refresh=force, cache_days=cfg.get("cache_days", 90),
    )
    if err:
        return None, "", "", err
    return profile, _rating_text(profile), "", None


def _approve_task_with_retry(client, approval_code, instance_code, task_id, cfg, form, comment):
    """同意审批任务;若 form 含空附件清空条目且被飞书拒绝,去掉该条目重试一次:
    放行优先,清空降级为提醒。返回 (approved, attach_note, error)。"""
    def _approve(f):
        client.approve_task(
            approval_code, instance_code, task_id,
            cfg.get("approver_user_id") or "",
            f, user_id_type=cfg.get("approver_user_id_type") or "open_id",
            comment=comment,
        )

    try:
        _approve(form)
        return True, "", None
    except Exception as e:
        error = e
        # 飞书若不接受空附件值,去掉清空条目重试一次:放行优先,清空降级为提醒
        retry_form = [f for f in form
                      if not (f.get("type") == "attachmentV2" and f.get("value") == [])]
        if len(retry_form) != len(form):
            try:
                _approve(retry_form)
                return True, "申请人误传文件未能清空(飞书拒绝空附件值),已放行,请人工删除", None
            except Exception as e2:
                error = e2
        return False, "", error


def _release_without_scan(client, state, task, cfg, dry_run, form_items, fallback_items):
    """免扫描直接放行:实例表单无任何企业控件(如达人解约且不涉及达人公司),
    不查企查查/不判自然人。【企业风险评级】回填固定文案;实例与定义表单都
    定位不到评级控件时仅评论放行;申请人误传过文件则清空附件控件。"""
    instance_code = task.get("instance_code") or ""
    task_id = task.get("task_id") or ""
    approval_code = task.get("approval_code") or ""
    located = {}
    form = []
    rating_ctl = _find_control_with_fallback(
        form_items, cfg.get("field_rating") or "企业风险评级", fallback_items,
    )
    if rating_ctl:
        form.append({
            "id": rating_ctl["id"],
            "type": rating_ctl["type"] or "input",
            "value": _EXEMPT_RATING,
        })
    if _attachment_has_value(form_items, cfg):
        att_ctl = _find_control_with_fallback(
            form_items, cfg.get("field_attachment") or "附件", fallback_items,
        )
        located["attachment"] = att_ctl
        if att_ctl:
            form.append({
                "id": att_ctl["id"],
                "type": att_ctl["type"] or "attachmentV2",
                "value": [],
            })
        else:
            located["attachment_warning"] = (
                "表单未找到【附件】控件(配置名: %s),已跳过附件清空"
                % (cfg.get("field_attachment") or "附件")
            )
    approved = False
    attach_note = ""
    if not dry_run:
        approved, attach_note, error = _approve_task_with_retry(
            client, approval_code, instance_code, task_id, cfg, form,
            comment=_EXEMPT_COMMENT,
        )
        if not approved:
            return None, {"instance_code": instance_code,
                          "error": "同意审批任务失败: %s: %s" % (type(error).__name__, error)}
    rec = {
        "instance_code": instance_code,
        "task_id": task_id,
        "approval_code": approval_code,
        "field": "",
        "company": "",
        "credit_code": "",
        "risk_level": "exempt",
        "rating_text": _EXEMPT_RATING if rating_ctl else "",
        "source": "exempt",
        "cost": 0,
        "report_file": "",
        "file_code": "",
        "approved": approved,
        "dry_run": dry_run,
    }
    if located.get("attachment_warning"):
        rec["attachment_warning"] = located["attachment_warning"]
    if attach_note:
        rec["attachment_warning"] = attach_note
    if not dry_run and approved:
        state.mark(instance_code, risk_level="exempt", company="", reason="approved_exempt")
    return rec, None


def _process_one(client, store, state, task, cfg, dry_run, force=False):
    """处理单条预审任务:取风险→回填评级文本(低/中/高风险附 Excel 报告)→同意放行。
    表单无任何企业控件(如达人解约)时走 _release_without_scan 免扫描直接放行。

    task: {task_id, approval_code, instance_code}。返回 (record_or_none, error_or_none)。
    两个回填控件只保留本技能写入的内容:评级控件始终覆写;附件控件低/中/高只放报告
    (申请人误传文件一并删除),未知/自然人/兜底不附报告、但若申请人误传过文件则清空
    (清空被飞书拒绝时降级放行并记录提醒,不阻塞审批)。
    """
    instance_code = task.get("instance_code") or ""
    task_id = task.get("task_id") or ""
    approval_code = task.get("approval_code") or ""
    try:
        detail = client.get_instance(instance_code)
    except Exception as e:
        return None, {"instance_code": instance_code,
                      "error": "读取审批实例失败: %s: %s" % (type(e).__name__, e)}
    form_items = detail.get("form_items") or []
    companies = _companies_from_instance(detail, cfg)
    fallback_items = []
    if approval_code:
        try:
            fallback_items = client.get_approval_form(approval_code)
        except Exception:
            fallback_items = []
    # 实例表单无任何企业控件(如达人解约且不涉及达人公司):免扫描直接放行(2026-09-08)
    if not companies:
        return _release_without_scan(
            client, state, task, cfg, dry_run, form_items, fallback_items,
        )
    # 合同主体一致性校验(2026-10-19):先于缓存/企查查执行,不一致零计费直接拒绝退回;
    # 附件缺失/解析失败/无法唯一确定对方→skip,放行并回填「请法务人工核对」
    subject_check, serr = _run_subject_check(
        client, store, form_items, fallback_items, cfg, companies)
    if serr:
        return None, {"instance_code": instance_code, "error": serr,
                      "company": companies[0]}
    if subject_check.get("status") == "mismatch":
        return _reject_subject_mismatch(
            client, state, task, cfg, dry_run, companies[0], subject_check)
    if subject_check.get("status") == "skip":
        # 不查企查查/不附报告,approve 放行交法务人工核对
        profile = None
        rating_text = _SUBJECT_SKIP_RATING
        file_code = ""
    else:
        profile, rating_text, file_code, err = _resolve_risk(store, companies, cfg, force)
        if err:
            return None, {"instance_code": instance_code, "error": err,
                          "company": (companies[0] if companies else None)}

    # 低/中/高风险都上传 Excel 报告,附件控件整体设值只保留报告(申请人误传文件一并删除);
    # 未知/自然人/兜底不附报告,但申请人若误传过文件则清空该控件
    attach_note = ""
    if (profile and profile.get("risk_level") in _ATTACH_LEVELS
            and profile.get("report_file") and os.path.isfile(profile["report_file"])):
        try:
            uploaded = client.upload_approval_file(profile["report_file"])
            file_code = uploaded.get("code") or ""
        except Exception as e:
            return None, {"instance_code": instance_code,
                          "error": "上传风险 Excel 失败: %s: %s" % (type(e).__name__, e),
                          "company": (companies[0] if companies else None)}

    clear_attachment = not file_code and _attachment_has_value(form_items, cfg)

    try:
        form, located = _build_approve_form(
            form_items, cfg, rating_text, file_code,
            fallback_items=fallback_items, clear_attachment=clear_attachment,
        )
    except Exception as e:
        return None, {"instance_code": instance_code,
                      "error": "%s: %s" % (type(e).__name__, e),
                      "company": (companies[0] if companies else None)}

    approved = False
    if not dry_run:
        if subject_check.get("status") == "skip":
            comment = _SUBJECT_SKIP_COMMENT
        else:
            label = _rating_label(profile)
            comment = ("风险预审完成：%s，自动放行" % label) if label else "风险预审完成，自动放行"
        approved, attach_note, error = _approve_task_with_retry(
            client, approval_code, instance_code, task_id, cfg, form, comment,
        )
        if not approved:
            return None, {"instance_code": instance_code,
                          "error": "同意审批任务失败: %s: %s" % (type(error).__name__, error),
                          "company": (companies[0] if companies else None),
                          "risk_level": (profile or {}).get("risk_level")}

    rec = {
        "instance_code": instance_code,
        "task_id": task_id,
        "approval_code": approval_code,
        "field": (companies[0].get("field") if companies else ""),
        "company": (profile.get("name") if profile else (companies[0].get("name") if companies else "")),
        "credit_code": (profile.get("credit_code") if profile else (companies[0].get("credit_code") if companies else "")),
        "risk_level": (profile.get("risk_level") if profile else None),
        "rating_text": rating_text,
        "source": (profile.get("source") if profile else ""),
        "cost": (profile.get("cost") if profile else 0),
        "report_file": (profile.get("report_file") if profile else ""),
        "file_code": file_code,
        "approved": approved,
        "dry_run": dry_run,
        "subject_check": {
            "status": subject_check.get("status"),
            "contract_subject": subject_check.get("contract_subject", ""),
        },
    }
    if located.get("attachment_warning"):
        rec["attachment_warning"] = located["attachment_warning"]
    if attach_note:
        rec["attachment_warning"] = attach_note

    if not dry_run and approved:
        if subject_check.get("status") == "skip":
            state.mark(
                instance_code, risk_level="subject_unverified",
                company=rec.get("company") or "", reason="approved_subject_unverified",
            )
        else:
            state.mark(
                instance_code,
                risk_level=(profile.get("risk_level") if profile else "unknown"),
                company=rec.get("company") or "",
                reason="approved",
            )
    return rec, None


def cmd_process_instance(instance_code):
    """补处理单条:在预审账号待办里找到该实例并 approve 回填(始终缓存优先,--force 强制重查)。"""
    ok, rep = check_all()
    if not ok:
        _emit({"ok": False, "error": _missing_creds_error(rep), "report": _json_report(rep)})
        return 1
    store = _require_mysql_store()
    if store is None:
        return 1
    cfg = dict(get_feishu())
    if not cfg.get("approver_user_id"):
        _emit({"ok": False, "error": _APPROVER_HINT})
        return 1
    client = FeishuClient(cfg["app_id"], cfg["app_secret"])
    try:
        tasks = client.query_user_tasks(
            cfg["approver_user_id"], cfg.get("approver_user_id_type") or "open_id", topic="1",
        )
    except Exception as e:
        _emit({"ok": False, "error": "查询预审账号待办失败: %s: %s" % (type(e).__name__, e)})
        return 1
    task = next((t for t in tasks if t.get("instance_code") == instance_code), None)
    if not task:
        _emit({"ok": False, "error": "该实例不在预审账号的待办任务中(可能已处理或不属于本账号)",
               "instance_code": instance_code})
        return 1
    state = StateStore()
    if state.is_processed(instance_code):
        # 预审已完成(state 有记录):当前待办是后续人工审批节点,技能不代签
        _emit({
            "ok": True, "already_processed": True, "instance_code": instance_code,
            "note": "该实例的系统风险预审已完成并记录在 state.json;"
                    "当前待办为后续人工审批节点(预审账号在会签名单),请人工在飞书审批",
        })
        return 0
    rec, err = _process_one(
        client, store, state, task, cfg, dry_run=False, force=("--force" in sys.argv[2:]),
    )
    if err:
        _emit({"ok": False, **err})
        return 1
    _emit({"ok": True, **rec})
    return 0


def cmd_poll(force=False, use_test=False):
    ok, rep = check_all()
    if not ok:
        _emit({"ok": False, "error": _missing_creds_error(rep), "report": _json_report(rep)})
        return 1
    store = _require_mysql_store()
    if store is None:
        return 1
    cfg = get_feishu()
    # --test 只扫测试流程码(FEISHU_APPROVAL_CODES_TEST),未配置直接报错,不回退正式码
    if use_test:
        codes_list = cfg.get("approval_codes_test") or []
        codes_env = "FEISHU_APPROVAL_CODES_TEST"
    else:
        codes_list = cfg["approval_codes"]
        codes_env = "FEISHU_APPROVAL_CODES"
    if not codes_list:
        _emit({"ok": False, "error": codes_env + " 为空,请填入合同审批定义 code"})
        return 1
    if not cfg.get("approver_user_id"):
        _emit({"ok": False, "error": _APPROVER_HINT})
        return 1
    client = FeishuClient(cfg["app_id"], cfg["app_secret"])
    state = StateStore()
    try:
        tasks = client.query_user_tasks(
            cfg["approver_user_id"], cfg.get("approver_user_id_type") or "open_id", topic="1",
        )
    except Exception as e:
        _emit({"ok": False, "error": "查询预审账号待办失败: %s: %s" % (type(e).__name__, e)})
        return 1
    # 只处理我们关注的合同审批定义,且该任务支持 API 操作
    codes = set(codes_list)
    filtered = [
        t for t in tasks
        if t.get("approval_code") in codes and t.get("support_api_operate") is not False
    ]
    # 同一实例可能多节点都派给该账号;按 instance_code 去重,每趟只处理首个待办
    seen = set()
    unique = []
    for t in filtered:
        ic = t.get("instance_code")
        if ic and ic not in seen:
            seen.add(ic)
            unique.append(t)

    processed, errors, skipped_processed = [], [], []
    dry_run = bool(cfg.get("comment_dry_run"))
    for task in unique:
        ic = task.get("instance_code")
        # 预审已完成的实例:剩余待办属后续人工审批节点(预审账号可能在会签
        # 名单里),技能不代签,跳过(2026-10-19:多节点流程下任务不会从待办
        # 消失,单靠任务消失幂等会让技能误审批人工节点)
        if ic and state.is_processed(ic):
            skipped_processed.append(ic)
            continue
        rec, err = _process_one(client, store, state, task, cfg, dry_run, force=force)
        if rec:
            processed.append(rec)
        elif err:
            errors.append(err)

    _emit({
        "ok": len(errors) == 0,
        "scanned": len(unique),
        "new": len(processed),
        "processed": processed,
        "skipped_processed": skipped_processed,
        "errors": errors,
        "dry_run": dry_run,
        "approval_codes": codes_list,
        "approver_user_id": cfg.get("approver_user_id"),
    })
    return 0 if not errors else 1


def main(argv):
    if len(argv) < 2:
        _emit({
            "ok": False,
            "error": "用法: poll.py check | poll [--force] [--test] | test-instance <instance_code> | process-instance <instance_code>",
        })
        return 1
    sub = argv[1]
    if sub == "check":
        return cmd_check()
    if sub == "poll":
        return cmd_poll(force="--force" in argv[2:], use_test="--test" in argv[2:])
    if sub == "test-instance":
        if len(argv) < 3:
            _emit({"ok": False, "error": "test-instance 需要 <instance_code>"})
            return 1
        return cmd_test_instance(argv[2])
    if sub == "process-instance":
        if len(argv) < 3:
            _emit({"ok": False, "error": "process-instance 需要 <instance_code>"})
            return 1
        return cmd_process_instance(argv[2])
    _emit({"ok": False, "error": "未知子命令: %s" % sub})
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
