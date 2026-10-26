# -*- coding: utf-8 -*-
import os
import sys
import tempfile

import pytest

_SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# 测试期间的状态与审计写临时目录，不落真实 data/
os.environ.setdefault("LEDGER_WORK_DIR", tempfile.mkdtemp(prefix="ledger-test-data-"))


@pytest.fixture(autouse=True)
def _hermetic_approver_env(monkeypatch):
    """屏蔽真实 .env 载入的绑定变量，测试不随安装机的绑定状态变化。"""
    for key in (
        "FEISHU_APPROVER_USER_ID",
        "FEISHU_APPROVER_USER_ID_TYPE",
        "FEISHU_APPROVER_WHITELIST",
    ):
        monkeypatch.setenv(key, "")
        os.environ.pop(key, None)
