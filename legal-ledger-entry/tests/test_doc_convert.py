# -*- coding: utf-8 -*-
"""ensure_docx 魔数分流与降级提示。真实 Word COM 转换走真机验收，不做离线模拟。"""
import sys

import pytest

from common.state import StateStore
from entry import run_entry
from ledger.doc_convert import ensure_docx, _convert_via_word
from ledger.errors import LedgerStop
from test_writer import CFG, FakeClient, _docx, _form

_OLE2 = b"\xd0\xcf\x11\xe0\x1a\xb1\x1a\xe1fake-doc-body"


def test_docx_passthrough_without_converter():
    blob = b"PK\x03\x04rest-of-docx"
    assert ensure_docx(blob, convert=lambda data: pytest.fail("不应触发转换")) == blob


def test_other_formats_passthrough_to_parser_error():
    blob = b"%PDF-1.7 not-a-word"
    assert ensure_docx(blob, convert=lambda data: pytest.fail("不应触发转换")) == blob


def test_ole2_goes_to_converter():
    converted = _docx("直播带货合作协议")
    seen = []

    def fake_convert(data):
        seen.append(data)
        return converted

    assert ensure_docx(_OLE2, convert=fake_convert) == converted
    assert seen == [_OLE2]


def test_converter_stop_propagates():
    def refuse(data):
        raise LedgerStop("本机未检测到 Word，无法自动转换 .doc")

    with pytest.raises(LedgerStop, match="未检测到 Word"):
        ensure_docx(_OLE2, convert=refuse)


def test_word_convert_refuses_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(LedgerStop, match="不是 Windows"):
        _convert_via_word(_OLE2)


def test_entry_converts_doc_before_parse(tmp_path, monkeypatch):
    """全链路：下载到 .doc → ensure_docx 转成 docx → 正常建行。"""
    import entry as entry_module

    doc_doc_bytes = _OLE2 + b"ignored"
    fake = FakeClient(
        _form("成都抖咖文化传播有限公司", customer="甲公司", amount="1", summary="s"),
        doc_doc_bytes,
    )
    monkeypatch.setattr(
        entry_module, "ensure_docx",
        lambda data, convert=None: _docx("直播带货合作协议") if data == doc_doc_bytes else data,
    )
    state = StateStore(str(tmp_path / "state.json"))
    result = run_entry("202609090050", client=fake, state=state, cfg=CFG)
    assert result["ok"] is True
    assert result["kind"] == "主合同"
    table_id, fields = fake.created[0]
    assert fields["合同名称"] == "直播带货合作协议"
