# -*- coding: utf-8 -*-
"""风险分级引擎(模块一核心,TODO A3 + B-2 升级)。

输入:多个企查查接口的原始响应。两种调用形式:
  - grade_risk(raw_2006)              # 兼容旧调用,单 dict 视为 2006
  - grade_risk({2006: r2006, 887: r887, ...})  # 推荐:多接口 payloads

输出:{level, hits, snapshot},level ∈ {"high","medium","low","unknown"}。

规则来源:`00-对齐/05-企查查接口数据法务确认表.xlsx` sheet「风险分级规则建议稿」。
法务裁定栏有值则覆盖建议等级(目前仅「行政处罚」裁定为高);其余按建议阈值落地。
改阈值优先改 DEFAULT_RULES;涉诉时间窗/角色/经营异常「未移出」由引擎的
window_months / item_filter 支持。

字段映射依据 qcc/RISK_MAPPING.md(经真实响应确认,2006 从 Result.Data 取风险字段,
无风险=null, 有风险={TotalCount, DataList(仅前3条)};887/888/889 从 Result.Data 取列表,
Paging.TotalRecords 为总条数)。
"""
from __future__ import annotations
import datetime
from typing import Any, Dict, List, Union

LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2}

# 规则版本号:规则替换后递增,快照永久留存需能追溯"当时用的哪版规则"
RULES_VERSION = "legal-draft-2026-08-13"

# apiCode 常量(与 qcc/client.py 对齐)
API_PARTNER_RISK = 2006
API_EXCEPTION_CHECK = 739
API_JUDGMENT_DOC = 887
API_COURT_ANNO = 888
API_CASE_FILING = 889
API_THREE_ELEMENT = 856

# ---- 建议稿规则(05 表「风险分级规则建议稿」,2026-08-13 落地) ----
# 每条规则:
#   source        = apiCode,默认 2006
#   field         = 该接口响应中用于判定的字段名/键
#   category      = 需求书3.2风险类别
#   level         = 命中即定到该级别
#   reason        = 命中说明
#   threshold     = 命中条数下限,默认 1
#   window_months = 时间窗(月);有则只计近 N 月(依赖 DataList 日期,分页截断可能欠计)
#   item_filter   = "defendant" 只计相对方为被告; "active" 只计未移出的经营异常
DEFAULT_RULES: List[Dict[str, Any]] = [
    # —— 高风险:命中任一即拦截 ——
    {"source": API_PARTNER_RISK, "field": "ShiXin", "category": "司法风险-失信被执行人", "level": "high",
     "reason": "存在失信被执行人记录"},
    {"source": API_PARTNER_RISK, "field": "ZhiXing", "category": "司法风险-被执行人", "level": "high",
     "reason": "存在被执行人记录"},  # 金额阈值建议稿待法务定;2006 无金额时按命中即高
    {"source": API_PARTNER_RISK, "field": "Bankruptcy", "category": "司法风险-破产重整", "level": "high",
     "reason": "存在破产重整记录"},
    {"source": API_PARTNER_RISK, "field": "Sumptuary", "category": "司法风险-限制高消费", "level": "high",
     "reason": "存在限制高消费记录"},
    {"source": API_PARTNER_RISK, "field": "EquityFreeze", "category": "司法风险-股权冻结", "level": "high",
     "reason": "存在股权冻结记录"},
    {"source": API_PARTNER_RISK, "field": "SeriousIllegal", "category": "工商异常-严重违法", "level": "high",
     "reason": "存在严重违法记录"},
    {"source": API_PARTNER_RISK, "field": "AdminPenalty", "category": "行政处罚", "level": "high",
     "reason": "存在行政处罚记录"},  # 法务裁定覆盖建议稿「中」→「高」
    # —— 中风险 ——
    {"source": API_PARTNER_RISK, "field": "Exception", "category": "工商异常-经营异常", "level": "medium",
     "reason": "存在未移出的经营异常记录", "item_filter": "active"},
    {"source": API_EXCEPTION_CHECK, "field": "DataList", "category": "工商异常-经营异常明细", "level": "medium",
     "reason": "存在未移出的经营异常明细", "threshold": 1, "item_filter": "active"},
    {"source": API_PARTNER_RISK, "field": "EnvPunishment", "category": "行政处罚-环保", "level": "medium",
     "reason": "存在环保处罚记录"},
    {"source": API_PARTNER_RISK, "field": "ChattelMortgage", "category": "经营风险-动产抵押", "level": "medium",
     "reason": "存在动产抵押记录"},
    {"source": API_PARTNER_RISK, "field": "EquityPledge", "category": "经营风险-股权出质", "level": "medium",
     "reason": "存在股权出质记录"},
    {"source": API_PARTNER_RISK, "field": "Liquidation", "category": "经营风险-清算", "level": "medium",
     "reason": "存在清算记录"},
    {"source": API_PARTNER_RISK, "field": "TaxOweNotice", "category": "经营风险-欠税", "level": "medium",
     "reason": "存在欠税公告"},
    {"source": API_PARTNER_RISK, "field": "TaxAbnormal", "category": "经营风险-税务非正常户", "level": "medium",
     "reason": "被列为税务非正常户"},
    {"source": API_PARTNER_RISK, "field": "TaxIllegal", "category": "经营风险-税收违法", "level": "medium",
     "reason": "存在税收违法记录"},
    {"source": API_PARTNER_RISK, "field": "JudicialSale", "category": "司法风险-司法拍卖", "level": "medium",
     "reason": "存在司法拍卖记录"},
    {"source": API_PARTNER_RISK, "field": "PublicSecurityNotice", "category": "其他-公安通告", "level": "medium",
     "reason": "存在公安通告"},
    # —— 涉诉明细(建议稿:时间窗 + 角色 + 条数;同一来源可多条规则,取最高级) ——
    {"source": API_JUDGMENT_DOC, "field": "DataList", "category": "司法风险-涉诉(裁判文书)", "level": "medium",
     "reason": "近24个月存在裁判文书且为被告", "threshold": 1, "window_months": 24,
     "item_filter": "defendant"},
    {"source": API_JUDGMENT_DOC, "field": "DataList", "category": "司法风险-涉诉(裁判文书)", "level": "high",
     "reason": "裁判文书累计≥10条", "threshold": 10},
    {"source": API_JUDGMENT_DOC, "field": "DataList", "category": "司法风险-涉诉(裁判文书)", "level": "high",
     "reason": "近12个月作为被告的裁判文书≥3条", "threshold": 3, "window_months": 12,
     "item_filter": "defendant"},
    {"source": API_COURT_ANNO, "field": "DataList", "category": "司法风险-涉诉(开庭公告)", "level": "medium",
     "reason": "近24个月存在开庭公告", "threshold": 1, "window_months": 24},
    {"source": API_COURT_ANNO, "field": "DataList", "category": "司法风险-涉诉(开庭公告)", "level": "high",
     "reason": "开庭公告累计≥10条", "threshold": 10},
    {"source": API_CASE_FILING, "field": "DataList", "category": "司法风险-涉诉(立案信息)", "level": "medium",
     "reason": "近24个月存在立案信息且为被告", "threshold": 1, "window_months": 24,
     "item_filter": "defendant"},
    {"source": API_CASE_FILING, "field": "DataList", "category": "司法风险-涉诉(立案信息)", "level": "high",
     "reason": "立案信息累计≥5条", "threshold": 5},
    # —— 三要素不一致(856) ——
    {"source": API_THREE_ELEMENT, "field": "VerifyResult", "category": "一致性校验-三要素", "level": "high",
     "reason": "三要素(代码+名称+法人)不一致", "threshold": None,
     "match": lambda v: v not in (1, "1", None)},  # VerifyResult≠1 即命中
]

# 经营状态高危关键词(吊销/注销/停业/撤销 等)——Status 字段命中即高风险
HIGH_RISK_STATUS_KEYWORDS = ["吊销", "注销", "停业", "撤销", "清算中"]


def _extract_2006_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    """从 2006 原始响应取 Result.Data。返回 {} 表示查无此企业或结构异常。"""
    result = (raw or {}).get("Result") or {}
    if result.get("VerifyResult") != 1:
        return {}
    return result.get("Data") or {}


def _risk_hit(value: Any) -> bool:
    """判断一个 2006 风险字段是否"命中":null/空为无风险,有结构(含 TotalCount 或非空)为命中。"""
    if value is None:
        return False
    if isinstance(value, dict):
        tc = value.get("TotalCount")
        if tc is not None:
            try:
                return int(tc) > 0
            except (TypeError, ValueError):
                return bool(value.get("DataList"))
        return bool(value.get("DataList"))
    return bool(value)


_REMOVE_KEYS = ("RemoveDate", "OutDate", "RemoveReason", "OutReason", "RemoveOffice")


def _is_active_exception(item: Dict[str, Any]) -> bool:
    """经营异常条目是否仍在名录(未移出)。有移出日期/原因则视为已移出。"""
    for k in _REMOVE_KEYS:
        v = item.get(k)
        if v not in (None, "", []):
            return False
    return True


def _is_defendant(item: Dict[str, Any], source: int = None, company_name: str = None) -> bool:
    """相对方是否为被告。887 用 IsDefendant;889 用 DefendantList / RoleList。"""
    if source == API_JUDGMENT_DOC:
        return item.get("IsDefendant") in (True, "true", "True", 1, "1")
    if source == API_CASE_FILING:
        name = company_name or ""
        for d in item.get("DefendantList") or []:
            if isinstance(d, dict) and name and name in str(d.get("Name") or ""):
                return True
        for role in item.get("RoleList") or []:
            if str(role.get("RoleName") or "") != "被告":
                continue
            for it in role.get("RoleItemList") or []:
                if isinstance(it, dict) and name and name in str(it.get("Name") or ""):
                    return True
        return False
    return False


def _item_kept(item: Any, window_months: int = None, date_field: str = None,
               item_filter: str = None, company_name: str = None, source: int = None) -> bool:
    if not isinstance(item, dict):
        return False
    if window_months and date_field:
        if str(item.get(date_field, "") or "")[:10] < _cutoff_date(window_months):
            return False
    if item_filter == "defendant" and not _is_defendant(item, source, company_name):
        return False
    if item_filter == "active" and not _is_active_exception(item):
        return False
    return True


def _count_hits(value: Any, window_months: int = None, date_field: str = None,
                item_filter: str = None, company_name: str = None, source: int = None) -> int:
    """从 {TotalCount, DataList} 或列表结构取命中条数。

    window_months / item_filter 指定时,按 DataList 过滤(分页截断可能欠计)。
    无过滤时优先用 TotalCount(准确)。
    """
    if value is None:
        return 0
    items = None
    total_count = None
    if isinstance(value, dict):
        tc = value.get("TotalCount")
        items = value.get("DataList")
        if tc is not None:
            try:
                total_count = int(tc)
            except (TypeError, ValueError):
                total_count = None
        if items is None:
            items = []
    elif isinstance(value, list):
        items = value
    else:
        return 0
    filtered = bool((window_months and date_field) or item_filter)
    if not items:
        # 有过滤但无明细:active 且 TotalCount>0 视为仍在名录;其余无法核验计 0
        if filtered:
            if item_filter == "active" and not window_months and total_count:
                return total_count
            return 0
        return total_count if total_count is not None else 0
    if filtered:
        return sum(1 for it in items if _item_kept(
            it, window_months, date_field, item_filter, company_name, source))
    return total_count if total_count is not None else len(items)


def _cutoff_date(window_months: int) -> str:
    """返回近 window_months 个月的截止日期(YYYY-MM-DD)。"""
    today = datetime.date.today()
    # 近似:按日历月回退
    y, m = today.year, today.month - window_months
    while m <= 0:
        m += 12
        y -= 1
    try:
        return datetime.date(y, m, today.day).isoformat()
    except ValueError:
        # day 溢出(如 31 日回退到 2 月),取该月最后一天
        import calendar
        last = calendar.monthrange(y, m)[1]
        return datetime.date(y, m, last).isoformat()


def _list_field_2006_data(data: Dict[str, Any], field: str) -> Any:
    """从 2006 Data 取字段值。"""
    return data.get(field)


def _list_field_supp(payloads: Dict[int, Any], api_code: int, field: str) -> Any:
    """从补充接口(887/888/889/856/739)响应取业务数据。

    887/888/889:Result.Data(列表) + Paging.TotalRecords
    856:Result.VerifyResult
    739:Result.Data(列表)
    返回 {TotalCount, DataList} 统一结构,便于 _count_hits 复用。
    """
    resp = (payloads or {}).get(api_code) or {}
    if str(resp.get("Status", "")) != "200":
        return None
    result = resp.get("Result") or {}
    if api_code == API_THREE_ELEMENT:
        return result.get("VerifyResult")
    if result.get("VerifyResult") != 1:
        return None
    data = result.get("Data") or []
    total = (resp.get("Paging") or {}).get("TotalRecords")
    if total is None:
        total = len(data) if isinstance(data, list) else 0
    return {"TotalCount": total, "DataList": data}


def grade_risk(payloads: Union[Dict[str, Any], Dict[int, Any]],
               rules: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """对多个企查查接口原始响应做风险分级。

    payloads:
      - 单个 dict:视为 2006 响应(兼容旧调用)
      - {apiCode: raw_json} 字典:推荐形式,支持多接口联合判定
    返回:
      level:   "high" / "medium" / "low"(取所有命中规则中的最高级)
      hits:    命中项列表 [{category, source, field, level, reason, count?}]
      snapshot:{主体信息 + 命中的风险字段原始值 + graded_at + rules_version, 用于留档}
    """
    rules = rules if rules is not None else DEFAULT_RULES

    # 兼容:单个 dict 视为 2006
    if not payloads or not any(isinstance(k, int) for k in payloads.keys()):
        # 全是字符串键(如 2006 的字段)或空 → 视为 2006 响应
        payloads = {API_PARTNER_RISK: payloads} if payloads else {}

    raw_2006 = payloads.get(API_PARTNER_RISK) or {}
    data_2006 = _extract_2006_data(raw_2006)

    if not data_2006:
        return {"level": "unknown", "hits": [],
                "snapshot": {"note": "查无此企业或数据不存在",
                             "graded_at": _now(), "rules_version": RULES_VERSION}}

    company_name = data_2006.get("Name") or ""
    hits: List[Dict[str, Any]] = []
    for rule in rules:
        source = rule.get("source", API_PARTNER_RISK)
        field = rule["field"]
        threshold = rule.get("threshold", 1)
        window = rule.get("window_months")
        match_fn = rule.get("match")
        item_filter = rule.get("item_filter")
        needs_count = bool(window or item_filter or (threshold and threshold > 1))

        if source == API_PARTNER_RISK:
            value = data_2006.get(field)
            if match_fn is not None:
                if match_fn(value):
                    hits.append(_hit(rule, source, field, value))
            elif needs_count:
                count = _count_hits(value, window, date_field=rule.get("date_field"),
                                    item_filter=item_filter, company_name=company_name,
                                    source=source)
                if count >= (threshold or 1):
                    hits.append(_hit(rule, source, field, value, count))
            else:
                if _risk_hit(value):
                    hits.append(_hit(rule, source, field, value))
        else:
            # 补充接口(887/888/889/856/739)
            value = _list_field_supp(payloads, source, field)
            if match_fn is not None:
                if match_fn(value):
                    hits.append(_hit(rule, source, field, value))
            else:
                count = _count_hits(value, window,
                                    date_field=_date_field_for(source),
                                    item_filter=item_filter, company_name=company_name,
                                    source=source)
                if count >= (threshold or 1):
                    hits.append(_hit(rule, source, field, value, count))

    # 经营状态高危关键词检查(单独处理 Status 字符串字段)
    status = data_2006.get("Status") or ""
    for kw in HIGH_RISK_STATUS_KEYWORDS:
        if kw in status:
            hits.append({"category": "经营状态", "source": API_PARTNER_RISK,
                         "field": "Status", "level": "high",
                         "reason": "登记状态含'%s'(当前:%s)" % (kw, status)})
            break

    # 取最高级别
    level = "low"
    if hits:
        level = max((h["level"] for h in hits), key=lambda lv: LEVEL_ORDER.get(lv, 0))

    snapshot = {
        "name": data_2006.get("Name"),
        "credit_code": data_2006.get("CreditCode"),
        "legal_person": data_2006.get("OperName"),
        "status": status,
        "risk_fields_raw": {h["field"]: _raw_for_snapshot(h, payloads, data_2006) for h in hits if "field" in h},
        "graded_at": _now(),
        "rules_version": RULES_VERSION,
    }
    return {"level": level, "hits": hits, "snapshot": snapshot}


def _hit(rule, source, field, value, count=None) -> Dict[str, Any]:
    hit = {
        "category": rule["category"],
        "source": source,
        "field": field,
        "level": rule["level"],
        "reason": rule["reason"],
    }
    if count is not None:
        hit["count"] = count
    elif isinstance(value, dict) and value.get("TotalCount") is not None:
        hit["count"] = value.get("TotalCount")
    return hit


def _raw_for_snapshot(hit, payloads, data_2006):
    source = hit.get("source", API_PARTNER_RISK)
    field = hit.get("field")
    if source == API_PARTNER_RISK:
        return data_2006.get(field)
    return (payloads or {}).get(source)


def _date_field_for(api_code: int) -> str:
    """补充接口列表项的日期字段名(用于时间窗过滤)。"""
    return {
        API_JUDGMENT_DOC: "JudgeDate",
        API_COURT_ANNO: "CourtTime",
        API_CASE_FILING: "PunishDate",
        API_EXCEPTION_CHECK: "AddDate",
    }.get(api_code, "")


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    # 自测:用 snapshots/ 下最近一份真实 2006 响应验证(快照在 01-poc/snapshots/)
    import glob
    import json
    import os

    _poc_root = os.path.dirname(os.path.dirname(__file__))
    snaps = sorted(glob.glob(os.path.join(_poc_root, "snapshots", "qcc_2006_*.json")))
    if not snaps:
        print("无快照可测,请先运行 risk_scan")
    else:
        with open(snaps[-1], encoding="utf-8") as f:
            raw = json.load(f)
        r = grade_risk(raw)
        print("样本:", snaps[-1])
        print("分级:", r["level"])
        print("规则版本:", r["snapshot"]["rules_version"])
        print("命中:", json.dumps(r["hits"], ensure_ascii=False, indent=2))
