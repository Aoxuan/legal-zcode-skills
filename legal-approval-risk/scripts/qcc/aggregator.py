# -*- coding: utf-8 -*-
"""企业信息一次性聚合(模块一面向业务流程的入口)。

将 query_companies.py 的调研脚本逻辑抽为可复用能力:
  - search_candidates(keyword):886 模糊搜索,返回候选企业列表(需求书3.1)
  - fetch_profile(company):按分级调用策略取数,控制成本,输出统一 CompanyProfile

分级调用策略(成本控制):
  第一档(必调):2006 合作风险排查(6.00) + 856 三要素核验(0.20) = 6.20
  第二档(涉诉明细 887/888/889,1.30):**始终调用**——实测发现 2006 的涉诉字段
    (ShiXin/ZhiXing/Sumptuary/Bankruptcy/EquityFreeze/JudicialSale)对两家公司均为 null,
    而 887/888/889 对顶呱呱分别返回 21/14/3 条,证明 2006 不暴露涉诉明细是否存在,
    无法用 2006 命中作为门控(原计划"仅当 2006 命中司法字段才调"的假设不成立)。
    故涉诉明细为必查项,基础成本 7.50 元/家。
  第三档(仅当 2006 命中 Exception):739(0.50)——Exception 字段在 2006 中有值,可门控。

全部接口走同一套客户账号凭据(QCC_APP_KEY / QCC_SECRET_KEY)。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time

from common.audit import trace_context
from common.config import get_qcc
from qcc.client import QccClient
from qcc.grading import grade_risk

# 接口单价(元/次),与 qcc/client.py 注释一致
PRICE = {886: 0.10, 2006: 6.00, 856: 0.20, 739: 0.50, 887: 0.30, 888: 0.50, 889: 0.50}


class QccSearchError(Exception):
    """企查查 886 调用失败(非「查无此企业」)。"""

    def __init__(self, status, message=""):
        self.status = status
        self.message = message or ""
        Exception.__init__(self, "企查查搜索失败 Status=%s %s" % (status, self.message))


@dataclass
class Candidate:
    name: str
    credit_code: str = ""
    legal_person: str = ""
    status: str = ""


def find_exact_match(candidates: List[Candidate], keyword: str) -> Optional[Candidate]:
    """在 886 候选中找企业名与输入完全一致者(去首尾空白后精确相等)。

    用于 search 后自动选定:命中则直接进入 profile,无需让用户再选。
    """
    kw = (keyword or "").strip()
    for c in candidates or []:
        if (c.name or "").strip() == kw:
            return c
    return None


@dataclass
class CompanyProfile:
    name: str
    credit_code: str
    legal_person: str
    status: str
    three_element_ok: Optional[bool]  # 856 三要素是否一致(None=未调/未开通)
    risk_level: str  # low/medium/high/unknown
    hits: List[dict] = field(default_factory=list)
    snapshot_files: Dict[int, str] = field(default_factory=dict)  # 兼容字段,不再落盘;原始响应在 payloads / MySQL payloads_json
    cost: float = 0.0
    called_apis: List[int] = field(default_factory=list)
    payloads: Dict[int, Any] = field(default_factory=dict)
    query_date: str = ""
    source: str = ""  # cache / qcc_live


class CompanyAggregator:
    def __init__(self, client: QccClient, actor: str = "", contract_id: str = ""):
        self.client = client
        self.actor = actor
        self.contract_id = contract_id

    def _call(self, api_code: int, **params) -> Any:
        client = self.client
        if api_code == 886:
            return client.fuzzy_search(params["search_key"])
        if api_code == 2006:
            return client.risk_scan(params["search_key"])
        if api_code == 856:
            return client.three_element_verify(params["credit_code"], params["company_name"], params["oper_name"])
        if api_code == 739:
            return client.exception_check(params["search_key"])
        if api_code == 887:
            return client.judgment_doc(params["search_key"])
        if api_code == 888:
            return client.court_anno(params["search_key"])
        if api_code == 889:
            return client.case_filing(params["search_key"])
        raise ValueError("未知 apiCode: %s" % api_code)

    def search_candidates(self, keyword: str, limit: int = 5) -> List[Candidate]:
        """886 模糊搜索,返回候选企业列表(对应需求书3.1 业务人员选择环节)。"""
        with trace_context(actor=self.actor, contract_id=self.contract_id):
            resp = self._call(886, search_key=keyword)
        status = str((resp or {}).get("Status", ""))
        if status != "200":
            raise QccSearchError(status, (resp or {}).get("Message") or "")
        result = resp.get("Result")
        # 886 的 Result 是候选列表(每项 Name/CreditCode/OperName/Status);兼容旧 dict 结构
        data = result if isinstance(result, list) else (result.get("Data") or [])
        out = []
        for it in data[:limit]:
            if not isinstance(it, dict):
                continue
            out.append(Candidate(
                name=it.get("Name") or "",
                credit_code=it.get("CreditCode") or "",
                legal_person=it.get("OperName") or "",
                status=it.get("Status") or "",
            ))
        return out

    def fetch_profile(self, company: str, credit_code: str = "",
                      oper_name: str = "") -> CompanyProfile:
        """按分级调用策略取数,输出统一 CompanyProfile。

        company:工商全称(或与 886 候选一致的企业名)
        credit_code/oper_name:已知则传入用于 856;未知则从 2006 取后回填。
        """
        cost = 0.0
        called: List[int] = []
        payloads: Dict[int, Any] = {}

        with trace_context(actor=self.actor, contract_id=self.contract_id):
            # 第一档:2006 + 856
            r2006 = self._call(2006, search_key=company)
        cost += PRICE[2006]
        called.append(2006)
        payloads[2006] = r2006

        result = (r2006 or {}).get("Result") or {}
        data = result.get("Data") or {}
        name = data.get("Name") or company
        cc = credit_code or data.get("CreditCode") or ""
        op = oper_name or data.get("OperName") or ""
        status = data.get("Status") or ""

        three_element_ok: Optional[bool] = None
        if cc and op:
            with trace_context(actor=self.actor, contract_id=self.contract_id):
                r856 = self._call(856, credit_code=cc, company_name=name, oper_name=op)
            cost += PRICE[856]
            called.append(856)
            payloads[856] = r856
            vr = (r856 or {}).get("Result") or {}
            three_element_ok = (vr.get("VerifyResult") == 1) if str((r856 or {}).get("Status", "")) == "200" else None

        # 分级:基于 2006 命中决定是否调第三档(Exception 字段在 2006 中有值,可门控)
        grading_2006 = grade_risk(r2006)
        hit_fields = {h.get("field") for h in grading_2006.get("hits", [])}

        # 第二档:涉诉明细(始终调用,见模块说明:2006 不暴露涉诉明细是否存在)
        with trace_context(actor=self.actor, contract_id=self.contract_id):
            for code in (887, 888, 889):
                r = self._call(code, search_key=name)
                cost += PRICE[code]
                called.append(code)
                payloads[code] = r

        # 第三档:经营异常明细(仅当 2006 命中 Exception)
        if "Exception" in hit_fields:
            with trace_context(actor=self.actor, contract_id=self.contract_id):
                r739 = self._call(739, search_key=name)
            cost += PRICE[739]
            called.append(739)
            payloads[739] = r739

        # 联合分级(含补充接口)
        grading = grade_risk(payloads)

        return CompanyProfile(
            name=name, credit_code=cc, legal_person=op, status=status,
            three_element_ok=three_element_ok,
            risk_level=grading.get("level", "unknown"),
            hits=grading.get("hits", []),
            snapshot_files={},
            cost=round(cost, 2),
            called_apis=called,
            payloads=payloads,
            query_date=time.strftime("%Y-%m-%d %H:%M:%S"),
            source="qcc_live",
        )


def default_aggregator(actor: str = "", contract_id: str = "") -> CompanyAggregator:
    """用 .env 中客户企查查账号构造聚合器,全部接口走这一套。"""
    return CompanyAggregator(QccClient(**get_qcc()), actor=actor, contract_id=contract_id)


if __name__ == "__main__":
    # 自测:对两家已知公司跑 fetch_profile
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    agg = default_aggregator(actor="self-test")
    for comp in ["成都讯格得信息科技有限公司", "顶呱呱科技股份有限公司"]:
        print("\n" + "=" * 60)
        print("公司:", comp)
        try:
            p = agg.fetch_profile(comp)
            print("名称:", p.name)
            print("信用代码:", p.credit_code)
            print("法人:", p.legal_person)
            print("状态:", p.status)
            print("三要素一致:", p.three_element_ok)
            print("风险等级:", p.risk_level)
            print("命中项数:", len(p.hits))
            print("调用接口:", p.called_apis)
            print("本次成本:", p.cost, "元")
        except Exception as e:
            print("  [x] 异常:", type(e).__name__, str(e))
