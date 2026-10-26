# -*- coding: utf-8 -*-
"""幂等状态 mark/get/load/save。"""
import os

from common.state import StateStore


def test_mark_get_load_save(tmp_path):
    path = os.path.join(str(tmp_path), "state.json")
    store = StateStore(path)
    assert store.get("INST-1") is None
    assert store.load() == {}

    saved = store.mark(
        "INST-1",
        table_id="tblMain",
        record_id="rec1",
        contract_no="BTSP202610001",
        kind="主合同",
    )
    assert saved["table_id"] == "tblMain"
    assert saved["record_id"] == "rec1"
    assert saved["contract_no"] == "BTSP202610001"
    assert saved["kind"] == "主合同"
    assert saved["updated_at"]

    got = store.get("INST-1")
    assert got["record_id"] == "rec1"
    assert store.load()["INST-1"]["contract_no"] == "BTSP202610001"

    again = StateStore(path)
    assert again.get("INST-1")["kind"] == "主合同"
    assert again.load()["INST-1"]["table_id"] == "tblMain"


def test_update_keeps_contract_no_and_marks_released(tmp_path):
    path = os.path.join(str(tmp_path), "state.json")
    store = StateStore(path)
    store.mark(
        "INST-1", table_id="tblMain", record_id="rec1",
        kind="主合同", contract_no="BTSP202610099",
    )
    saved = store.update("INST-1", released=True, task_id="task-1")
    assert saved["contract_no"] == "BTSP202610099"
    assert saved["released"] is True
    assert saved["task_id"] == "task-1"
    assert StateStore(path).get("INST-1")["record_id"] == "rec1"


def test_save_persists_mutation(tmp_path):
    path = os.path.join(str(tmp_path), "nested", "state.json")
    store = StateStore(path)
    store.mark("INST-2", table_id="tblSub", record_id="rec2", kind="补充协议")
    data = store.load()
    data["INST-2"]["kind"] = "终止协议"
    store.save(data)
    assert StateStore(path).get("INST-2")["kind"] == "终止协议"
    assert "contract_no" not in StateStore(path).get("INST-2")
