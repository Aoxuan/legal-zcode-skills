# -*- coding: utf-8 -*-
"""签名算法与服务端(test_approval_options.py TestOverseasEntry)同向量锁定。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from entry import sign  # noqa: E402


def test_sign_vector():
    assert sign("test-key-1234567890abcdef", "1761200000",
                '{"name":"My Commerce Co.,Ltd","actor":"tester"}') == \
        "d05c4863026f6e7f190263ad84b68d727d0149ca4ae8cf75a6bee53dc4bb6b2e"
