# 企查查风险字段 → 需求书 3.2 风险表映射

> 本表用于模块一（TODO A2）。对照《合同在线协作与电子签约一体化需求说明书》3.2 的六类风险，
> 标注企查查用哪个 apiCode 覆盖、2006 返回的**真实字段名**、字段结构、是否有缺口。
> **主接口**：`risk_scan` 使用 **apiCode 2006 合作风险排查（6.00元/次，端点 `/RiskControl/Scan`）**，
> 一次调用覆盖下表绝大多数维度。apiCode 与单价来自《企查查api接口.md》，字段结构经真实响应确认。

## 0. 2006 返回结构（经真实响应确认，与官方文档略有出入）

```
{
  "Status": "200",                  # "200"=成功
  "Message": "【有效请求】查询成功",
  "OrderNumber": "RISKCONTROL...",  # 计费订单号
  "Result": {
    "VerifyResult": 1,              # 1=企业数据存在, 0=不存在
    "Data": {
      # 工商照面
      Name / CreditCode / OperName(法人) / Status(登记状态) / RegistCapi(注册资本) ...
      # 风险字段：无风险 = null；有风险 = { TotalCount, DataList(仅前3条) }
      ShiXin / ZhiXing / Sumptuary / ... (见下表)
    }
  }
}
```

> 注意：官方文档把 `VerifyResult`+`Data` 描述为顶层，**实际嵌套在 `Result` 内**。下游解析统一从
> `result["Result"]["Data"]` 取业务数据。这是本次联调发现的文档与实现差异。

## 一、映射总表（需求书 3.2 六类风险 → 2006 真实字段）

| 需求书3.2 风险类别 | 2006 真实字段名 | 字段结构 / 说明 | 缺口 |
|---|---|---|---|
| 经营状态（存续/注销/吊销/迁出） | `Status`（工商照面）；`RevokeInfo`（吊销注销信息） | `Status` 为字符串状态；`RevokeInfo` 无则 null | 无 |
| 工商异常（经营异常名录、严重违法失信名单） | `Exception`（经营异常）；`SeriousIllegal`（严重违法） | 无则 null；有则 `{TotalCount, DataList}` | 无 |
| 司法风险（涉诉、被执行人、失信、限高） | `ShiXin`(失信) / `ZhiXing`(被执行) / `Sumptuary`(限高) / `Bankruptcy`(破产) / `EquityFreeze`(股权冻结) / `JudicialSale`(司法拍卖) | 涉诉明细（裁判文书/开庭/立案）在 2006 中未单独列出，可按需补 apiCode 887/888/889；核心被执行/失信/限高已覆盖 | 涉诉明细为次要缺口，不影响分级主链路 |
| 行政处罚 | `AdminPenalty`（行政处罚）；`EnvPunishment`（环保处罚） | 无则 null；有则 `{TotalCount, DataList}` | 无 |
| 经营风险（股权出质、动产抵押、欠税公告、清算） | `ChattelMortgage`(动产抵押) / `EquityPledge`(股权出质) / `Liquidation`(清算) / `TaxOweNotice`(欠税) / `TaxAbnormal`(税务非正常户) / `TaxIllegal`(税收违法) | 无则 null；有则 `{TotalCount, DataList}` | 无 |
| 变更风险（法人/股东/注册资本变更） | `ChangeList`（变更记录数组） | 元素含 `ProjectName`/`ChangeDate`/`BeforeList`/`AfterList` | 无结构性缺口；"近期变更"判定窗口**待法务定** |

## 二、2006 其他有用字段（供审批表单回填 / 实控人识别，非风险）

- 主体回填（需求书 3.1）：`Name`、`CreditCode`（统一社会信用代码）、`OperName`（法定代表人）、`Address`、`ContactInfo`。
- 实控人/受益人：`ActualControllerList`（实际控制人，含 `Name`/`FinalBenefitPercent`/`ControlPercent`）、`BeneficiaryList`（受益所有人）。
- 股东/人员：`PartnerList`（股东，含 `StockName`/`StockPercent`）、`EmployeeList`（主要人员）。
- 信用参考：`TaxCreditList`（纳税信用等级，按年）、`TagList`（含"小微企业"等标签）。
- 计费溯源：`OrderNumber`（建议随快照留存，便于后续对账）。

## 三、单点接口（2006 之外，按需补充）

- **apiCode 739 经营异常核查（0.50元/次）**：`QccClient.exception_check()`，字段比 2006 的 `Exception` 更细（列入原因/列入日期/作出决定机关），可用于对中/高风险企业做专项复核。
- **apiCode 736 企业风险扫描（6.00元/次）**：作为 2006 的备选（维度略少：无欠税公告、税务非正常户、限高、破产重整等）。已定义为常量 `API_RISK_SCAN`，暂不作为主接口。
- **涉诉明细（若法务需要）**：apiCode 887 裁判文书 / 888 开庭公告 / 889 立案信息（各 0.30–0.50元/次），2006 不含，按需单独调。

## 四、待确认事项（不阻塞 A2 梳理，但影响 A3 分级引擎）

1. **变更风险的判定窗口**：需求书要求"近期"法定代表人/股东/注册资本变更，"近期"阈值（如近 6 个月/1 年）**待法务答复**（问题清单 2.2）。
2. **风险分级阈值**：已按 `05-企查查接口数据法务确认表.xlsx`「风险分级规则建议稿」落入 `grading.py`（`RULES_VERSION=legal-draft-2026-08-13`）。法务裁定栏目前仅「行政处罚=高」有值并已覆盖建议稿；其余按建议阈值执行。被执行人金额分档、变更风险窗口仍待法务定。
