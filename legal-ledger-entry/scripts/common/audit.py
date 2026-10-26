# -*- coding: utf-8 -*-
"""绑定与放行的本地审计日志。手机号/邮箱只落掩码，完整凭据不落盘。"""
import json
import os
import time

from common import paths


def mask_credential(text):
    """手机号留前 3 后 2，邮箱留前 2 位与域名。"""
    text = (text or "").strip()
    if not text:
        return ""
    if "@" in text:
        local, _, domain = text.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return "%s***@%s" % (head, domain)
    if len(text) > 5:
        return "%s****%s" % (text[:3], text[-2:])
    return "***"


def log_event(action, credential="", user_id="", ok=True, detail=""):
    """追加一行 JSON 到 data/bind_audit.jsonl。失败静默，不影响主流程。"""
    try:
        directory = paths.data_dir()
        os.makedirs(directory, exist_ok=True)
        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "credential": mask_credential(credential),
            "user_id": (user_id or "").strip(),
            "ok": bool(ok),
        }
        if detail:
            entry["detail"] = str(detail)
        with open(os.path.join(directory, "bind_audit.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
