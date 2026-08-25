# -*- coding: utf-8 -*-
"""企业信息查询技能入口(zcode / Cursor / Hermes 同一套)。

两阶段,智能体负责"多候选让用户选"的对话编排:
  1) search <keyword>  -> 886 模糊匹配,打印候选列表 JSON
  2) profile <name> <credit_code> <legal_person> -> 取详情+风险,落盘+MySQL+Excel
  3) export <name>  -> 从已落盘快照重生成 Excel(脱机,不计费)
  4) check -> 体检企查查 + MySQL 是否就绪

用法:
  py scripts/query.py check
  py scripts/query.py search 讯格得
  py scripts/query.py profile "成都讯格得信息科技有限公司" 91MA00XXXXX 张三
  py scripts/query.py export "成都讯格得信息科技有限公司"

输出均为 UTF-8 JSON,直接打印到 stdout(智能体读取后向用户展示)。
错误时打印 {"ok": false, "error": "..."} 到 stdout 并以非零码退出。

真实调用企查查:search ≈0.10 元/次;profile ≈7.50 元/次。
"""
import json
import os
import sys

# 让本脚本能 import 同级包 common / qcc(无论从哪启动)
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.config import check_all  # noqa: E402
from common import paths  # noqa: E402
from common.company_store import store_from_env  # noqa: E402
from common.snapshot import save as save_snapshot  # noqa: E402
from qcc.aggregator import default_aggregator, find_exact_match  # noqa: E402
from qcc.grading import grade_risk  # noqa: E402
from qcc.report import build_report, load_payloads  # noqa: E402

ACTOR = "legal-company-query"
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


def _require_qcc():
    ok, rep = check_all()
    qcc_ok = all(item["all_present"] for item in rep if "企查查" in item["group"])
    if not qcc_ok:
        _emit({"ok": False, "error": _missing_creds_error(rep), "report": _json_report(rep)})
        return False
    return True


def _require_mysql_store():
    """profile/check 用:缺 MYSQL_* 或连不上则失败,不静默回退。"""
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
    _emit(payload)
    return 0


def cmd_search(keyword):
    if not _require_qcc():
        return 1
    agg = default_aggregator(actor=ACTOR)
    try:
        candidates = agg.search_candidates(keyword)
    except Exception as e:
        _emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        return 1
    cand_json = [
        {"index": i, "name": c.name, "credit_code": c.credit_code,
         "legal_person": c.legal_person, "status": c.status}
        for i, c in enumerate(candidates)
    ]
    match = find_exact_match(candidates, keyword)
    match_json = None
    if match is not None:
        for cj in cand_json:
            if cj["name"] == match.name and cj["credit_code"] == match.credit_code:
                match_json = cj
                break
    _emit({
        "ok": True,
        "keyword": keyword,
        "count": len(candidates),
        "candidates": cand_json,
        "exact_match": match is not None,
        "match": match_json,
    })
    return 0


def cmd_profile(name, credit_code, legal_person, force_refresh=False):
    if not _require_qcc():
        return 1
    store = _require_mysql_store()
    if store is None:
        return 1

    # (2) 先查 MySQL:命中则返回库内已存档结果(不调企查查,零成本)
    #     force_refresh(--refresh / 用户说"查最新")时跳过缓存,强制拉最新并写回新版本
    if not force_refresh:
        try:
            cached = store.get_latest(credit_code=credit_code or "", name=name or "")
        except Exception as e:
            _emit({"ok": False, "error": "MySQL 读取失败: %s: %s" % (type(e).__name__, e)})
            return 1
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
                _emit({"ok": False, "error": "Excel 生成失败: %s: %s" % (type(e).__name__, e)})
                return 1
            _emit({
                "ok": True,
                "profile": {
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
                },
            })
            return 0

    # (3) 库中无该企业,真调企查查详情接口
    agg = default_aggregator(actor=ACTOR)
    try:
        p = agg.fetch_profile(name, credit_code, legal_person)
    except Exception as e:
        _emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        return 1

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
        # 已计费:快照先落盘,再报 MySQL 失败,避免证据丢失
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
        _emit({
            "ok": False,
            "error": "MySQL 写入失败: %s: %s" % (type(e).__name__, e),
            "snapshot_files": list(snapshot_files.keys()),
            "report_file": report_file,
        })
        return 1

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
        _emit({
            "ok": False,
            "error": "Excel 生成失败: %s: %s" % (type(e).__name__, e),
            "snapshot_files": list(snapshot_files.keys()),
        })
        return 1

    _emit({
        "ok": True,
        "profile": {
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
        },
    })
    return 0


def cmd_export(name):
    """脱机从已落盘快照重生成 Excel,不调任何企查查接口(零成本)。"""
    payloads = load_payloads(name, paths.snapshots_dir())
    if not payloads:
        _emit({"ok": False, "error": "未找到该企业的快照: %s" % name})
        return 1
    try:
        path = build_report(name, payloads, paths.reports_dir())
    except Exception as e:
        _emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        return 1
    _emit({"ok": True, "report_file": path, "apis": sorted(payloads.keys())})
    return 0


def main(argv):
    if len(argv) < 2:
        _emit({
            "ok": False,
            "error": "用法: query.py check | search <keyword> | profile <name> <credit_code> <legal_person> | export <name>",
        })
        return 1
    sub = argv[1]
    if sub == "check":
        return cmd_check()
    if sub == "search":
        if len(argv) < 3:
            _emit({"ok": False, "error": "search 需要企业名参数"})
            return 1
        return cmd_search(argv[2])
    if sub == "profile":
        # 支持 --refresh / --force / --latest 标志(可出现在任意位置),触发强制查最新绕过缓存
        rest = argv[2:]
        force_refresh = False
        positional = []
        for a in rest:
            if a in ("--refresh", "--force", "--latest"):
                force_refresh = True
            else:
                positional.append(a)
        if len(positional) < 3:
            _emit({"ok": False, "error": "profile 需要 <name> <credit_code> <legal_person> 三个参数(legal_person/credit_code 无可传空串);可选 --refresh 强制查最新绕过缓存"})
            return 1
        return cmd_profile(positional[0], positional[1], positional[2], force_refresh=force_refresh)
    if sub == "export":
        if len(argv) < 3:
            _emit({"ok": False, "error": "export 需要 <name> 企业名参数(用于从已落盘快照重生成 Excel,不计费)"})
            return 1
        return cmd_export(argv[2])
    _emit({"ok": False, "error": "未知子命令: %s" % sub})
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
