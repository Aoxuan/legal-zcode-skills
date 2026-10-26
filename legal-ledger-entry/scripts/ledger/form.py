# -*- coding: utf-8 -*-
"""审批表单 → 台账字段。只读，不写审批。"""
from ledger.widgets import SUMMARY_FIELD, WIDGETS


def _by_widget(form, widget_id):
    """按控件 id 取值。fieldList 的子控件在 value 的行/单元格里。"""
    for item in form or []:
        if not isinstance(item, dict):
            continue
        if item.get("id") == widget_id:
            return item.get("value")
        rows = item.get("value")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, list):
                    for cell in row:
                        if isinstance(cell, dict) and cell.get("id") == widget_id:
                            return cell.get("value")
                elif isinstance(row, dict) and row.get("id") == widget_id:
                    return row.get("value")
    return None


def _plain(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(_plain(x) for x in value)
    if isinstance(value, dict):
        return str(value.get("value") or value.get("text") or "")
    return str(value).strip()


def _company(raw):
    """去掉外部选项里「｜信用代码」后缀。"""
    text = (raw or "").strip()
    if not text:
        return ""
    return text.split("｜")[0].split("|")[0].strip()


def extract_ledger_fields(form, fallback_base=""):
    """相对方组装与旧链路一致：

    基底 = 签约类型 / 客户 / 供应商，取先出现者，并剥掉信用代码；
    三者全空时用 fallback_base（合同 Word 甄别出的签约对方）做基底——
    项目名称绝不能单独充当相对方（2026-10-26 生产案例：正式定义控件
    id 与副本不一致，三个企业控件全读空，商单关联把项目名称兜成了主体）；
    达人真实姓名有值则追加，达人场景再追加达人企业名称；
    已有基底或达人时，达人或商单非空再追加项目名称。用 / 拼接。
    """
    def text(key):
        return _plain(_by_widget(form, WIDGETS[key]))

    base = ""
    if text("counterparty"):
        base = _company(text("counterparty"))
    elif text("customer"):
        base = _company(text("customer"))
    elif text("supplier"):
        base = _company(text("supplier"))
    elif fallback_base:
        base = fallback_base

    parts = []
    talent = text("talent_name")
    biz_order_linked = bool(_by_widget(form, WIDGETS["biz_order"]))
    if base:
        parts.append(base)
    if talent:
        parts.append(talent)
        company = text("talent_company")
        if company:
            parts.append(company)
    if parts and (talent or biz_order_linked) and text("project"):
        parts.append(text("project"))

    return {
        "签约主体": text("our_entity"),
        "相对方主体（全称）": "/".join(p for p in parts if p),
        "合同金额": text("amount"),
        SUMMARY_FIELD: text("summary"),
    }


def find_attachment(form):
    """只取合同附件 URL。不取风险报告附件。字面量 \\u0026 还原成 &。"""
    for item in form or []:
        if not isinstance(item, dict):
            continue
        if item.get("id") != WIDGETS["contract_attach"]:
            continue
        value = item.get("value")
        urls = value if isinstance(value, list) else [value]
        for url in urls:
            if isinstance(url, str) and url.strip():
                return url.strip().replace("\\u0026", "&")
    return None
