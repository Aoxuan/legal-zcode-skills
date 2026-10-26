# -*- coding: utf-8 -*-
"""副本审批控件与台账坐标（2026-10-13）。

后台页面 id 7680902134576975065 只在审批后台 URL 里出现，不传任何 OpenAPI。
"""

# 审批定义 code（合同审批副本）。API 用这个，不用后台页面 id。
APPROVAL_CODE = "5D8E981F-1D5E-4C8E-9EA4-5E146B26F890"

APP_TOKEN = "REDACTED_LEDGER_APP_TOKEN"
MAIN_TABLE = "tblTkvIvviA93PYY"
SUB_TABLE = "tblmEFt5GivFeTRU"
CATEGORY_TABLE = "tblozjjQ8oKZ45Zi"

# 控件 id 为权威。供应商、风险报告附件是副本新 id。
# 2026-10-26 实测（单 202610210074）：正式定义供应商名称=widget17885056795820001，
# 与副本不一致；项目名称/商单/金额/概述/签约主体/合同附件 id 两边相同。
# 切正式定义时须逐控件重拉回填（见使用说明 §6），否则相对方会读空走 Word 兜底。
WIDGETS = {
    "our_entity": "widget16860337013420001",       # 签约主体
    "counterparty": "widget17262110497010001",     # 签约类型（相对方，名称｜信用代码）
    "customer": "widget17877458508650001",         # 客户名称
    "supplier": "widget17885010789340001",         # 供应商名称（副本新 id）
    "talent_name": "widget17161762744400001",      # 达人真实姓名
    "talent_company": "widget17882317569520001",   # 达人企业名称
    "project": "widget17767575024460001",          # 项目名称
    "biz_order": "widget17120254454310001",        # 商单提报/变更（非空则追加项目名称）
    "amount": "widget16860341040970001",           # 合同金额
    "summary": "widget16860344257800001",          # 概述
    "contract_attach": "widget16860351486670001",  # 合同附件（只下这个）
    "risk_attach": "widget17885104796480001",      # 风险报告附件（副本新 id，只排除）
}

SUMMARY_FIELD = "合同摘要（如：张喜喜&麦当劳23-6-6短视频）"
NO_FIELD = "合同编号（以此为准）"
SUB_NO_FIELD = "子合同编号"
# 公式列 = 合同名称 + "-" + 合同编号。自动编号列不能筛选，父合同用这列 contains 编号。
PARENT_LOOKUP_FIELD = "合同名称唯一值"

# 主表写入列（fld）。自动编号 fld0d3jpbw 只回读，不写。
MAIN_FIELDS = {
    "合同名称": "fldFd8UJnr",
    "合同类型": "fldIVZl1p5",
    "我方主体": "fldXdDUQ0G",
    "相对方主体（全称）": "fldnbErTyY",
    "合同金额": "flds4OkTlh",
    SUMMARY_FIELD: "fldDw0Jv62",
}
MAIN_NO_FIELD_ID = "fld0d3jpbw"

# 子表写入列。子合同编号等公式列不写。
SUB_FIELDS = {
    "主合同": "fld994q9Hh",
    "子合同类型": "fldrpAc34P",
    "我方主体": "fldxX1CGKP",
    "相对方主体（全称）": "fldXkO6UF0",
    "金额": "fldvKTX9Yj",
    "摘要（概括子合同内容）": "fldV06QBpy",
}
