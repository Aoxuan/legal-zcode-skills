# -*- coding: utf-8 -*-
"""绑定放行白名单与本地审计日志。离线 fake client，不访问网络。"""
import json
import os

import pytest

from common import paths
from common.state import StateStore
from entry import run_bind, run_entry, run_release
from ledger.errors import LedgerStop
from test_release import _main_client, _pending, ReleaseClient
from test_writer import CFG, _docx, _form


def _clear(monkeypatch):
    """让三个绑定变量在测试内不存在，且无论测试中谁写入了都能在收尾清掉。"""
    for key in (
        "FEISHU_APPROVER_USER_ID",
        "FEISHU_APPROVER_USER_ID_TYPE",
        "FEISHU_APPROVER_WHITELIST",
    ):
        monkeypatch.setenv(key, "")
        os.environ.pop(key, None)


class LookupClient(ReleaseClient):
    def __init__(self, resolved, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._resolved = resolved

    def lookup_user_id(self, mobile="", email=""):
        self.lookups.append({"mobile": mobile, "email": email})
        return self._resolved


def _bind_client(resolved):
    return LookupClient(resolved, _form("成都抖咖文化传播有限公司"), _docx("直播带货合作协议"))


def _audit_lines(directory=None):
    path = os.path.join(directory or paths.data_dir(), "bind_audit.jsonl")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_first_bind_initializes_whitelist(tmp_path, monkeypatch):
    _clear(monkeypatch)
    path = tmp_path / ".env"
    path.write_text("FEISHU_APP_ID=cli_x\n", encoding="utf-8")
    fake = _bind_client("u_new")
    result = run_bind(mobile="13800138000", client=fake, cfg=CFG, env_file=str(path))
    assert result == {"ok": True, "bound": True}
    text = path.read_text(encoding="utf-8")
    assert "FEISHU_APPROVER_USER_ID=u_new" in text
    assert "FEISHU_APPROVER_WHITELIST=u_new" in text
    assert [e["action"] for e in _audit_lines()][-1] == "bind"


def test_preset_whitelist_allows_first_bind_of_member(tmp_path, monkeypatch):
    """交付包预置白名单后，新机器（未绑过人）首绑白名单内用户正常。"""
    _clear(monkeypatch)
    monkeypatch.setenv("FEISHU_APPROVER_WHITELIST", "u_owner")
    path = tmp_path / ".env"
    path.write_text(
        "FEISHU_APP_ID=cli_x\nFEISHU_APPROVER_WHITELIST=u_owner\n", encoding="utf-8",
    )
    fake = _bind_client("u_owner")
    result = run_bind(mobile="13800138000", client=fake, cfg=CFG, env_file=str(path))
    assert result == {"ok": True, "bound": True}
    text = path.read_text(encoding="utf-8")
    assert "FEISHU_APPROVER_USER_ID=u_owner" in text


def test_preset_whitelist_blocks_first_bind_of_other_user(tmp_path, monkeypatch):
    """预置白名单后，首绑信任不再对白名单外用户生效（堵新装机绑任意范围内账号）。"""
    _clear(monkeypatch)
    monkeypatch.setenv("FEISHU_APPROVER_WHITELIST", "u_owner")
    path = tmp_path / ".env"
    path.write_text(
        "FEISHU_APP_ID=cli_x\nFEISHU_APPROVER_WHITELIST=u_owner\n", encoding="utf-8",
    )
    fake = _bind_client("u_leader")
    with pytest.raises(LedgerStop, match="白名单"):
        run_bind(mobile="13900139000", client=fake, cfg=CFG, env_file=str(path))
    text = path.read_text(encoding="utf-8")
    assert "u_leader" not in text
    assert "FEISHU_APPROVER_USER_ID" not in text


def test_rebind_outside_whitelist_refused(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FEISHU_APPROVER_USER_ID", "u_owner")
    monkeypatch.setenv("FEISHU_APPROVER_WHITELIST", "u_owner")
    path = tmp_path / ".env"
    path.write_text("FEISHU_APP_ID=cli_x\nFEISHU_APPROVER_USER_ID=u_owner\n", encoding="utf-8")
    fake = _bind_client("u_leader")
    with pytest.raises(LedgerStop, match="白名单"):
        run_bind(mobile="13900139000", client=fake, cfg=CFG, env_file=str(path))
    text = path.read_text(encoding="utf-8")
    assert "u_leader" not in text
    assert "FEISHU_APPROVER_USER_ID=u_owner" in text
    refused = [e for e in _audit_lines() if e["action"] == "bind_refused"]
    assert refused and refused[-1]["ok"] is False
    assert refused[-1]["user_id"] == "u_leader"


def test_bind_add_appends_whitelist(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FEISHU_APPROVER_USER_ID", "u_owner")
    monkeypatch.setenv("FEISHU_APPROVER_WHITELIST", "u_owner")
    path = tmp_path / ".env"
    path.write_text("FEISHU_APP_ID=cli_x\n", encoding="utf-8")
    fake = _bind_client("u_leader")
    result = run_bind(
        mobile="13900139000", client=fake, cfg=CFG, env_file=str(path), add=True,
    )
    assert result == {"ok": True, "bound": True}
    text = path.read_text(encoding="utf-8")
    assert "FEISHU_APPROVER_WHITELIST=u_owner,u_leader" in text
    assert [e["action"] for e in _audit_lines()][-1] == "bind_add"


def test_rebind_inside_whitelist_allowed(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FEISHU_APPROVER_USER_ID", "u_a")
    monkeypatch.setenv("FEISHU_APPROVER_WHITELIST", "u_a,u_b")
    path = tmp_path / ".env"
    path.write_text("FEISHU_APP_ID=cli_x\n", encoding="utf-8")
    fake = _bind_client("u_b")
    result = run_bind(mobile="13700137000", client=fake, cfg=CFG, env_file=str(path))
    assert result == {"ok": True, "bound": True}
    text = path.read_text(encoding="utf-8")
    assert "FEISHU_APPROVER_USER_ID=u_b" in text
    assert "FEISHU_APPROVER_WHITELIST=u_a,u_b" in text
    assert "bind_refused" not in [e["action"] for e in _audit_lines()[-1:]]


def test_release_refuses_operator_outside_whitelist(tmp_path, monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FEISHU_APPROVER_WHITELIST", "u_owner")
    cfg = dict(CFG)
    cfg["approver_user_id"] = "e9e179ff"
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    entered = run_entry("202609090050", client=fake, state=state, cfg=cfg)
    assert entered["release"] == "pending_confirm"
    with pytest.raises(LedgerStop, match="白名单"):
        run_release("202609090050", "合同编号：BTSP202610099", client=fake, state=state, cfg=cfg)
    assert fake.approved == []
    refused = [e for e in _audit_lines() if e["action"] == "release_refused"]
    assert refused and refused[-1]["user_id"] == "e9e179ff"


def test_release_allows_when_whitelist_unset(tmp_path, monkeypatch):
    _clear(monkeypatch)
    cfg = dict(CFG)
    cfg["approver_user_id"] = "e9e179ff"
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    run_entry("202609090050", client=fake, state=state, cfg=cfg)
    result = run_release(
        "202609090050", "合同编号：BTSP202610099", client=fake, state=state, cfg=cfg,
    )
    assert result["released"] is True
    assert len(fake.approved) == 1


def test_audit_masks_phone_and_email(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_WORK_DIR", str(tmp_path))
    _clear(monkeypatch)
    path = tmp_path / ".env"
    path.write_text("FEISHU_APP_ID=cli_x\n", encoding="utf-8")
    run_bind(mobile="13800138000", client=_bind_client("u_x"), cfg=CFG, env_file=str(path))
    run_bind(email="legal@example.com", client=_bind_client("u_x"), cfg=CFG, env_file=str(path))
    raw = (tmp_path / "bind_audit.jsonl").read_text(encoding="utf-8")
    assert "13800138000" not in raw
    assert "legal@example.com" not in raw
    assert "138****00" in raw
    assert "le***@example.com" in raw


def test_release_success_writes_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_WORK_DIR", str(tmp_path))
    _clear(monkeypatch)
    cfg = dict(CFG)
    cfg["approver_user_id"] = "e9e179ff"
    fake = _main_client(tasks=[_pending()])
    state = StateStore(str(tmp_path / "state.json"))
    run_entry("202609090050", client=fake, state=state, cfg=cfg)
    run_release("202609090050", "合同编号：BTSP202610099", client=fake, state=state, cfg=cfg)
    events = _audit_lines(str(tmp_path))
    assert events
    last = events[-1]
    assert last["action"] == "release"
    assert last["ok"] is True
    assert last["user_id"] == "e9e179ff"
    assert "202609090050" in last["detail"]
