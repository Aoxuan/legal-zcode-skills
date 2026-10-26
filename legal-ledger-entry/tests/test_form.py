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
    项目名称单独兜成相对方主体。"""
    form = [
        _w("biz_order", [{"instance_code": "SO-9"}]),
        _w("project", "平齿大鲨鱼"),
    ]
    assert extract_ledger_fields(form)["相对方主体（全称）"] == ""


def test_fallback_base_used_when_form_base_empty():
    form = [
        _w("biz_order", [{"instance_code": "SO-9"}]),
        _w("project", "平齿大鲨鱼"),
    ]
    got = extract_ledger_fields(form, fallback_base="思搜网络（上海）有限公司")
    assert got["相对方主体（全称）"] == "思搜网络（上海）有限公司/平齿大鲨鱼"


def test_fallback_base_ignored_when_form_base_present():
    form = [_w("customer", "甲公司｜91ABC")]
    got = extract_ledger_fields(form, fallback_base="乙公司")
    assert got["相对方主体（全称）"] == "甲公司"


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
