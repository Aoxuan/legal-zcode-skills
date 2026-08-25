# -*- coding: utf-8 -*-
"""企查查开放平台客户端(模块一:签约对象信息 + 风险预警)。

鉴权:请求头 Token = MD5(appkey + Timespan + secretKey) 大写,Timespan 为秒级时间戳。
参考: https://openapi.qcc.com/DataApi

接口权威标识 = apiCode(见 ../../企查查api接口.md);HTTP 请求走 DataApi 命名路径。
本文件用 apiCode 常量标注接口语义与单价,DataApi 命名路径为参考实现;
真实凭据到位后需对照厂商最新文档复核命名路径与响应字段。
"""
import hashlib
import time
import requests

from common.audit import audited

BASE = "https://api.qichacha.com"

# ---- apiCode 常量(权威标识,单价见《企查查api接口.md》) ----
API_FUZZY_SEARCH = 886       # 企业模糊搜索        0.10元/次  需求书3.1 候选企业选择
API_PARTNER_RISK = 2006      # 合作风险排查        6.00元/次  risk_scan 主接口,维度最全
API_RISK_SCAN = 736          # 企业风险扫描        6.00元/次  备选/预留(维度略少于2006)
API_EXCEPTION_CHECK = 739    # 经营异常核查        0.50元/次  字段更细的单项接口
API_THREE_ELEMENT = 856      # 企业三要素核验      0.20元/次  需求书3.1 回填后一致性校验
API_JUDGMENT_DOC = 887       # 裁判文书核查        0.30元/次  补 2006 涉诉明细缺口
API_COURT_ANNO = 888         # 开庭公告核查        0.50元/次  同上
API_CASE_FILING = 889        # 立案信息核查        0.50元/次  同上

# DataApi 命名路径(已按 openapi.qcc.com/dataApi/{apiCode} 逐个核实)
PATH_FUZZY_SEARCH = "/FuzzySearch/GetList"
PATH_EXCEPTION_CHECK = "/ExceptionCheck/GetList"
PATH_PARTNER_RISK = "/RiskControl/Scan"      # apiCode 2006 合作风险排查
PATH_THREE_ELEMENT = "/ECIThreeElVerify/GetInfo"
PATH_JUDGMENT_DOC = "/JudgmentDocCheck/GetList"
PATH_COURT_ANNO = "/CourtAnnoCheck/GetList"
PATH_CASE_FILING = "/CaseFilingCheck/GetList"


class QccClient:
    def __init__(self, app_key, secret_key):
        self.app_key = app_key
        self.secret_key = secret_key

    def _headers(self):
        timespan = str(int(time.time()))
        token = hashlib.md5((self.app_key + timespan + self.secret_key).encode("utf-8")).hexdigest().upper()
        return {"Token": token, "Timespan": timespan}

    def _get(self, path, params):
        url = BASE + path
        params = dict(params, key=self.app_key)
        resp = requests.get(url, params=params, headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    @audited("qcc.fuzzy_search", api_code=886, cost=0.10)
    def fuzzy_search(self, keyword):
        """企业模糊搜索(apiCode 886,0.10元/次,端点 /FuzzySearch/GetList)。
        返回候选企业列表(最多5条),用于业务输入名称后选择目标企业。对应需求书3.1。"""
        return self._get(PATH_FUZZY_SEARCH, {"searchKey": keyword})

    @audited("qcc.risk_scan", api_code=2006, cost=6.00)
    def risk_scan(self, search_key):
        """合作风险排查(apiCode 2006,6.00元/次,主接口)。端点 /RiskControl/Scan。
        一次调用覆盖需求书3.2风险表绝大多数类别:失信被执行人/被执行人/限制高消费/
        破产重整/欠税公告/税务非正常户/环保处罚/股权冻结与出质/动产抵押/清算/
        经营异常/行政处罚,以及工商照面(含注销吊销)。

        返回结构(经真实响应确认,与官方文档略有出入):
          顶层 = {Status, Message, OrderNumber, Result}
            Status: "200" 成功;Message 含"查询成功"等提示
            Result.VerifyResult: 1=企业数据存在, 0=不存在
            Result.Data: 业务数据对象
              工商照面: Name/CreditCode/OperName/Status/RegistCapi/...
              风险字段(无风险时为 null, 有风险时含 TotalCount+DataList,仅前3条):
                ShiXin(失信)/ZhiXing(被执行)/Sumptuary(限高)/Bankruptcy(破产)/
                EquityFreeze(股权冻结)/JudicialSale(司法拍卖)/AdminPenalty(行政处罚)/
                EnvPunishment(环保处罚)/Exception(经营异常)/SeriousIllegal(严重违法)/
                ChattelMortgage(动产抵押)/EquityPledge(股权出质)/Liquidation(清算)/
                TaxOweNotice(欠税)/TaxAbnormal(税务非正常户)/TaxIllegal(税收违法)/
                PublicSecurityNotice(公安通告)
        由上层映射为风险分级(分级引擎见 A3 grading.py)。字段映射详见 RISK_MAPPING.md。"""
        return self._get(PATH_PARTNER_RISK, {"searchKey": search_key})

    @audited("qcc.exception_check", api_code=739, cost=0.50)
    def exception_check(self, search_key):
        """经营异常核查(apiCode 739,0.50元/次,端点 /ExceptionCheck/GetList)。
        单项接口,返回列入原因/列入日期/作出决定机关等,字段比 2006 的 Exception 更细。"""
        return self._get(PATH_EXCEPTION_CHECK, {"searchKey": search_key})

    @audited("qcc.three_element_verify", api_code=856, cost=0.20)
    def three_element_verify(self, credit_code, company_name, oper_name):
        """企业三要素核验(apiCode 856,0.20元/次,端点 /ECIThreeElVerify/GetInfo)。
        校验统一社会信用代码、企业名称、法定代表人三者是否匹配一致。
        返回 VerifyResult: 0=公司编号有误, 1=一致, 2=企业名称不一致, 3=法定代表人不一致。
        对应需求书3.1 回填后的一致性校验。"""
        return self._get(PATH_THREE_ELEMENT, {
            "creditCode": credit_code,
            "companyName": company_name,
            "operName": oper_name,
        })

    @audited("qcc.judgment_doc", api_code=887, cost=0.30)
    def judgment_doc(self, search_key):
        """裁判文书核查(apiCode 887,0.30元/次,端点 /JudgmentDocCheck/GetList)。
        返回文书标题/案由/案号/案件金额/案件类型/原告被告/裁判结果等。
        补 2006 未单列的涉诉明细,对应需求书3.2 司法风险-涉诉信息。"""
        return self._get(PATH_JUDGMENT_DOC, {"searchKey": search_key})

    @audited("qcc.court_anno", api_code=888, cost=0.50)
    def court_anno(self, search_key):
        """开庭公告核查(apiCode 888,0.50元/次,端点 /CourtAnnoCheck/GetList)。
        返回案由/案号/法院/开庭时间/当事人等。补 2006 涉诉明细缺口。"""
        return self._get(PATH_COURT_ANNO, {"searchKey": search_key})

    @audited("qcc.case_filing", api_code=889, cost=0.50)
    def case_filing(self, search_key):
        """立案信息核查(apiCode 889,0.50元/次,端点 /CaseFilingCheck/GetList)。
        返回案号/公诉人/被告人/案件状态/法院/案件类型等。补 2006 涉诉明细缺口。"""
        return self._get(PATH_CASE_FILING, {"searchKey": search_key})
