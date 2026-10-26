# -*- coding: utf-8 -*-
"""副本审批控件与台账坐标（2026-10-13）。

后台页面 id 7680902134576975065 只在审批后台 URL 里出现，不传任何 OpenAPI。
"""

# 审批定义 code（正式「合同审批单」，2026-10-26 起交付默认；API 用这个，不用后台页面 id）。
APPROVAL_CODE = "DB2F122F-9823-4D8E-9D01-7CFC068472FE"

# 正式合同台账坐标（2026-10-26 决策直接走正式；测试副本坐标见 .env.example 注释）。
APP_TOKEN = "LmVHbDXpdaNTK1sTDbjceczrnkh"
MAIN_TABLE = "tbln45WEECZDdHC3"
SUB_TABLE = "tbl67Sg7imRboxAG"
CATEGORY_TABLE = "tblFJBtFnuEr8xE0"

# 控件 id 为权威。WIDGETS 默认是副本定义的 id。
# 2026-10-26 并集比对（副本 28 张 / 正式 60 张表单）：12 个技能控件里只有
# 供应商名称、风险报告附件两个 id 随定义不同（见 _ENV_WIDGETS；企业风险评级
# 控件也随定义不同，但那是 legal-approval-risk 预审技能读的，本技能不用）。
# 其余 10 个控件 id 两边完全一致。
# widgets_for() 按 .env 的审批 code 自动切换这两个 id，切环境无需手改本文件。
WIDGETS = {
    "our_entity": "widget16860337013420001",       # 签约主体
    "counterparty": "widget17262110497010001",     # 签约类型（相对方，名称｜信用代码）
    "customer": "widget17877458508650001",         # 客户名称
    "supplier": "widget17885010789340001",         # 供应商名称（副本 id）
    "talent_name": "widget17161762744400001",      # 达人真实姓名
    "talent_company": "widget17882317569520001",   # 达人企业名称
    "project": "widget17767575024460001",          # 项目名称
    "biz_order": "widget17120254454310001",        # 商单提报/变更（非空则追加项目名称）
    "amount": "widget16860341040970001",           # 合同金额
    "summary": "widget16860344257800001",          # 概述
    "contract_attach": "widget16860351486670001",  # 合同附件（只下这个）
    "risk_attach": "widget17885104796480001",      # 风险报告附件（副本 id，只排除）
}

# 正式审批定义 code（API 用；后台页面 id 7241484543487049731 不得传给任何 API）
PROD_APPROVAL_CODE = "DB2F122F-9823-4D8E-9D01-7CFC068472FE"

# 随审批定义不同的控件 id（2026-10-26 实测）。副本差异项即 WIDGETS 默认值。
_ENV_WIDGETS = {
    PROD_APPROVAL_CODE: {
        "supplier": "widget17885056795820001",     # 供应商名称（正式 id）
        "risk_attach": "widget17887789271910001",  # 风险报告附件（正式 id，只排除）
    },
}


def widgets_for(approval_code):
    """按审批定义返回控件映射：supplier/risk_attach 随定义切换，
    其余 id 两边一致。未知的 code 用副本默认值（读不到会停住报错，安全）。"""
    merged = dict(WIDGETS)
    merged.update(_ENV_WIDGETS.get((approval_code or "").strip(), {}))
    return merged

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
