# -*- coding: utf-8 -*-
"""从 .docx 抽首页标题、合同种类和父合同编号。不转 PDF。"""
import re
from io import BytesIO

from docx import Document

from ledger.errors import LedgerStop

_DOCX_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"

# 先标题，没有再看正文前部。后缀 -BC/-ZZ/-JS/-zx 不计入父号。
_PARENT = re.compile(r"BTSP(\d{9,10})(?!\d)")

_ROUTES = (
    (("补充协议", "补充合同"), "补充协议"),
    (("终止协议", "解除协议", "解约协议"), "终止协议"),
    (("结算对账单", "结算协议", "结算单"), "结算单"),
    (("执行确认单", "执行单"), "执行单"),
)


def assert_docx_bytes(data):
    blob = bytes(data or b"")
    if blob.startswith(_OLE2_MAGIC):
        raise LedgerStop("附件是旧版 Word（.doc），无法解析，已停住。请另存为 .docx 后再跑")
    if not blob.startswith(_DOCX_MAGIC):
        raise LedgerStop("合同附件不是 .docx，已停住")


def route_kind(title):
    text = title or ""
    for keys, kind in _ROUTES:
        if any(key in text for key in keys):
            return kind
    return "主合同"


def find_parent_no(title, body=""):
    """标题里唯一一个父号就用它；标题没有才看正文前部。零个或多个都返回 None。"""
    in_title = _unique_parents(title)
    if len(in_title) == 1:
        return in_title[0]
    if len(in_title) > 1:
        return None
    in_body = _unique_parents(body)
    if len(in_body) == 1:
        return in_body[0]
    return None


def extract_title(source):
    doc = _open(source) if isinstance(source, (bytes, bytearray)) else source
    paragraphs = list(_first_page(doc))
    sized = []
    any_size = False
    for paragraph in paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        size = _paragraph_size(paragraph)
        if size is not None:
            any_size = True
        if _is_label(text):
            continue
        if size is not None and len(text) >= 4:
            sized.append((size, text))
    if sized:
        best = max(size for size, _text in sized)
        for size, text in sized:
            if size == best:
                return text
    if any_size:
        return ""
    for paragraph in paragraphs:
        text = paragraph.text.strip()
        if text and not _is_label(text):
            return text
    return ""


def parse_docx(data):
    assert_docx_bytes(data)
    doc = _open(data)
    title = extract_title(doc)
    if not title:
        raise LedgerStop("抽不出合同标题，已停住")
    kind = route_kind(title)
    body = _first_page_body(doc, title)
    parent = find_parent_no(title, body) if kind != "主合同" else None
    return {"title": title, "kind": kind, "parent_no": parent}


def _unique_parents(text):
    found = []
    for match in _PARENT.finditer(text or ""):
        parent = "BTSP" + match.group(1)
        if parent not in found:
            found.append(parent)
    return found


def _is_label(text):
    return text.endswith("：") or text.endswith(":")


def _paragraph_size(paragraph):
    sizes = []
    for run in paragraph.runs:
        if run.font.size is not None:
            sizes.append(run.font.size.pt)
    if not sizes:
        return None
    return max(sizes)


def _first_page(doc):
    for paragraph in doc.paragraphs:
        xml = paragraph._p.xml
        if "lastRenderedPageBreak" in xml:
            return
        yield paragraph
        if 'w:type="page"' in xml or "w:type='page'" in xml:
            return


def _first_page_body(doc, title):
    parts = []
    skipped_title = False
    for paragraph in _first_page(doc):
        text = paragraph.text.strip()
        if not skipped_title and text == title:
            skipped_title = True
            continue
        if text:
            parts.append(text)
    return "\n".join(parts)


def _open(data):
    try:
        return Document(BytesIO(data))
    except Exception:
        raise LedgerStop("合同附件不是可解析的 .docx，已停住") from None
