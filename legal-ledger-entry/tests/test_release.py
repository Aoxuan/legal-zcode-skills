# -*- coding: utf-8 -*-
"""放行预览、绑定使用人、确认后同意。离线 fake client。"""
import os

import pytest

from common.state import StateStore
from entry import run_bind, run_entry, run_release
from ledger.errors import LedgerStop
from ledger.release import preview
from ledger.widgets import MAIN_TABLE
from test_writer import CFG, FakeClient, _docx, _form


def _cfg(user_id="e9e179ff"):
    cfg = dict(CFG)
    cfg["approver_user_id"] = user_id
    return cfg


class ReleaseClient(FakeClient):
    def __init__(self, *args, tasks=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.tasks = list(tasks or [])
        self.approved = []
        self.lookups = []

    def get_instance(self, instance_code):
        detail = super().get_instance(instance_code)
        detail["task_list"] = self.tasks
        return detail

    def approve_task(self, approval_code, instance_code, task_id, user_id, comment):
        self.approved.append({
            "approval_code": approval_code,
            "instance_code": instance_code,
            "task_id": task_id,
            "user_id": user_id,
            "comment": comment,
        })
        return {}

    def lookup_user_id(self, mobile="", email=""):
        self.lookups.append({"mobile": mobile, "email": email})
        return "e9e179ff"


def _pending(user_id="e9e179ff", task_id="task-1", node_name="法务审批"):
    return {
        "id": task_id,
        "user_id": user_id,
        "open_id": "ou_other",
        "status": "PENDING",
        "node_name": node_name,
    }


def _main_client(**kwargs):
    return ReleaseClient(
        _form(
            "成都抖咖文化传播有限公司",
            customer="甲公司",
            amount="12000",
            summary="概述甲",
        ),
        _docx("直播带货合作协议"),
        **kwargs,
    )


def test_preview_asks_confirm_only_for_one_pending_task():
    one = preview("e9e179ff", "BTSP202610099", [_pending()], False, False)
    assert one["release"] == "pending_confirm"
    assert one["task_id"] == "task-1"
    assert one["node_name"] == "法务审批"
    assert one["proposed_comment"] == "合同编号：BTSP202610099"

    none = preview("e9e179ff", "BTSP202610099", [_pending("someone-else")], False, False)
    assert none["release"] == "skipped"
    assert "不是这张单的待办审批人" in none["release_reason"]

    many = preview(
        "e9e179ff", "BTSP202610099",
        [_pending(task_id="a"), _pending(task_id="b")], False, False,
    )
    assert many["release"] == "skipped"
    assert "多条待办" in many["release_reason"]

    done = preview("e9e179ff", "BTSP202610099", [_pending()], already_released=True)
    assert done == {"release": "done"}


def test_entry_does_not_approve_before_confirm(tmp_path):
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    result = run_entry("202609090050", client=fake, state=state, cfg=_cfg())
    assert result["ok"] is True
    assert result["contract_no"] == "BTSP202610099"
    assert result["release"] == "pending_confirm"
    assert result["proposed_comment"] == "合同编号：BTSP202610099"
    assert fake.approved == []
    assert state.get("INST-1").get("released") is not True


def test_entry_skips_release_when_operator_is_not_approver(tmp_path):
    fake = _main_client(tasks=[_pending("someone-else")])
    state = StateStore(str(tmp_path / "state.json"))
    result = run_entry("202609090050", client=fake, state=state, cfg=_cfg())
    assert result["ok"] is True
    assert result["record_id"] == "recNEW"
    assert result["release"] == "skipped"
    assert "不是这张单的待办审批人" in result["release_reason"]
    assert fake.approved == []


def test_release_approves_with_comment_and_is_idempotent(tmp_path):
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    run_entry("202609090050", client=fake, state=state, cfg=_cfg())
    comment = "合同编号：BTSP202610099\n同意，按此编号归档"
    result = run_release("202609090050", comment, client=fake, state=state, cfg=_cfg())
    assert result["ok"] is True
    assert result["released"] is True
    assert result["task_id"] == "task-1"
    assert len(fake.approved) == 1
    assert fake.approved[0]["comment"] == comment
    assert fake.approved[0]["user_id"] == "e9e179ff"
    assert "form" not in fake.approved[0]
    assert state.get("INST-1")["released"] is True

    again = run_release("202609090050", comment, client=fake, state=state, cfg=_cfg())
    assert again["idempotent"] is True
    assert len(fake.approved) == 1


def test_release_refuses_comment_without_contract_no(tmp_path):
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    run_entry("202609090050", client=fake, state=state, cfg=_cfg())
    with pytest.raises(LedgerStop, match="没有合同编号"):
        run_release("202609090050", "同意", client=fake, state=state, cfg=_cfg())
    assert fake.approved == []


def test_release_refuses_when_ledger_missing(tmp_path):
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    with pytest.raises(LedgerStop, match="还没有写入台账"):
        run_release("202609090050", "合同编号：BTSP202610099", client=fake, state=state, cfg=_cfg())
    assert fake.approved == []


def test_bind_writes_user_id_not_phone(tmp_path, monkeypatch):
    for key in (
        "FEISHU_APPROVER_USER_ID",
        "FEISHU_APPROVER_USER_ID_TYPE",
        "FEISHU_APPROVER_WHITELIST",
    ):
        monkeypatch.setenv(key, "")
        os.environ.pop(key, None)
    path = tmp_path / ".env"
    path.write_text("FEISHU_APP_ID=cli_x\n# keep\n", encoding="utf-8")
    fake = ReleaseClient(_form("成都抖咖文化传播有限公司"), _docx("直播带货合作协议"))
    result = run_bind(
        mobile="+86 138-0013-8000", client=fake, cfg=_cfg(""), env_file=str(path),
    )
    assert result == {"ok": True, "bound": True}
    text = path.read_text(encoding="utf-8")
    assert "13800138000" not in text
    assert "+86" not in text
    assert "FEISHU_APP_ID=cli_x" in text
    assert "FEISHU_APPROVER_USER_ID=e9e179ff" in text
    assert "FEISHU_APPROVER_USER_ID_TYPE=user_id" in text
    assert "FEISHU_APPROVER_WHITELIST=e9e179ff" in text
    assert fake.lookups == [{"mobile": "13800138000", "email": ""}]
    assert os.environ["FEISHU_APPROVER_USER_ID"] == "e9e179ff"


def test_open_id_on_task_still_matches():
    task = _pending(user_id="other", task_id="task-9")
    task["open_id"] = "ou_me"
    viewed = preview("ou_me", "BTSP202610099", [task])
    assert viewed["release"] == "pending_confirm"
    assert viewed["task_id"] == "task-9"


def test_idempotent_entry_after_release_does_not_ask_again(tmp_path):
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    state.mark(
        "INST-1", table_id=MAIN_TABLE, record_id="recOLD",
        kind="主合同", contract_no="BTSP202610099",
    )
    state.update("INST-1", released=True, task_id="task-1")
    result = run_entry("202609090050", client=fake, state=state, cfg=_cfg())
    assert result["idempotent"] is True
    assert result["release"] == "done"
    assert "proposed_comment" not in result
    assert fake.approved == []
    assert fake.instance_reads == []
