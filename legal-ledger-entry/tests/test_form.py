# -*- coding: utf-8 -*-
"""表单提取：相对方组装、金额、摘要、合同附件定位。离线。"""
from ledger.form import _by_widget, extract_ledger_fields, find_attachment
from ledger.widgets import WIDGETS


def _w(key, value):
    return {"id": WIDGETS[key], "value": value}


def test_customer_strips_credit_code_and_skips_project():
    form = [
        _w("customer", "甲公司｜91ABC"),
        _w("project", "不该出现"),
    ]
    got = extract_ledger_fields(form)
    assert got["相对方主体（全称）"] == "甲公司"
    assert got["合同金额"] == ""
    assert got["合同摘要（如：张喜喜&麦当劳23-6-6短视频）"] == ""


def test_supplier_when_no_customer():
    form = [_w("supplier", "乙供应商｜CODE9")]
    assert extract_ledger_fields(form)["相对方主体（全称）"] == "乙供应商"


def test_counterparty_beats_customer():
    form = [
        _w("counterparty", "签约公司｜9151"),
        _w("customer", "客户公司｜0000"),
    ]
    assert extract_ledger_fields(form)["相对方主体（全称）"] == "签约公司"


def test_talent_appends_company_and_project():
    form = [
        _w("talent_name", "张三"),
        _w("talent_company", "某达人公司"),
        _w("project", "秋季投放"),
    ]
    assert extract_ledger_fields(form)["相对方主体（全称）"] == "张三/某达人公司/秋季投放"


def test_biz_order_appends_project_without_talent():
    form = [
        _w("customer", "甲公司｜91ABC"),
        _w("biz_order", [{"instance_code": "SO-1"}]),
        _w("project", "项目甲"),
    ]
    assert extract_ledger_fields(form)["相对方主体（全称）"] == "甲公司/项目甲"


def test_project_never_alone_even_with_biz_order():
    """2026-10-26 生产案例回归：三个企业控件全空时，商单关联不得把
    项目名称单独兜成相对方主体（相对方应为空串，由调用方停住）。"""
    form = [
        _w("biz_order", [{"instance_code": "SO-9"}]),
        _w("project", "平齿大鲨鱼"),
    ]
    assert extract_ledger_fields(form)["相对方主体（全称）"] == ""


def test_widgets_for_switches_supplier_by_approval_code():
    """切正式审批定义时 supplier/risk_attach 自动切换，其余控件不变。"""
    from ledger.widgets import PROD_APPROVAL_CODE, WIDGETS, widgets_for

    prod = widgets_for(PROD_APPROVAL_CODE)
    assert prod["supplier"] == "widget17885056795820001"
    assert prod["risk_attach"] == "widget17887789271910001"
    assert prod["customer"] == WIDGETS["customer"]
    assert widgets_for("")["supplier"] == WIDGETS["supplier"]
    assert widgets_for("unknown-code")["supplier"] == WIDGETS["supplier"]


def test_extract_reads_production_supplier_widget():
    """正式表单（供应商控件是正式 id）+ 正式控件映射 → 正常取出基底。"""
    from ledger.widgets import PROD_APPROVAL_CODE, widgets_for

    prod_widgets = widgets_for(PROD_APPROVAL_CODE)
    form = [
        {"id": prod_widgets["supplier"], "value": "思搜网络（上海）有限公司｜91310104MA1FR3NP9J｜刘川球"},
        {"id": prod_widgets["biz_order"], "value": [{"instance_code": "SO-9"}]},
        {"id": prod_widgets["project"], "value": "平齿大鲨鱼"},
    ]
    got = extract_ledger_fields(form, widgets=prod_widgets)
    assert got["相对方主体（全称）"] == "思搜网络（上海）有限公司/平齿大鲨鱼"
    # 同一张正式表单用副本 id 去读 → 基底为空（触发停住的前提）
    assert extract_ledger_fields(form)["相对方主体（全称）"] == ""


def test_amount_and_summary():
    form = [
        _w("amount", "8800.50"),
        _w("summary", "张喜喜合作概述"),
    ]
    got = extract_ledger_fields(form)
    assert got["合同金额"] == "8800.50"
    assert got["合同摘要（如：张喜喜&麦当劳23-6-6短视频）"] == "张喜喜合作概述"


def test_find_attachment_only_contract_and_restores_amp():
    form = [
        _w("risk_attach", ["https://risk.example/a"]),
        _w("contract_attach", ["https://file.example/c?a=1\\u0026b=2"]),
    ]
    assert find_attachment(form) == "https://file.example/c?a=1&b=2"


def test_find_attachment_ignores_risk_only():
    form = [_w("risk_attach", ["https://risk.example/only"])]
    assert find_attachment(form) is None


def test_by_widget_reads_field_list_child():
    form = [{
        "id": "fieldlist-parent",
        "type": "fieldList",
        "value": [[
            {"id": WIDGETS["summary"], "type": "textarea", "value": "子控件概述"},
        ]],
    }]
    assert _by_widget(form, WIDGETS["summary"]) == "子控件概述"
    got = extract_ledger_fields(form)
    assert got["合同摘要（如：张喜喜&麦当劳23-6-6短视频）"] == "子控件概述"
