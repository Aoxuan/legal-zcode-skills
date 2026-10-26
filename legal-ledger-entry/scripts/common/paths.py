# -*- coding: utf-8 -*-
"""技能内路径。运行数据可被 LEDGER_WORK_DIR 覆盖。"""
import os

_THIS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(_THIS)
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)


def data_dir():
    env = (os.getenv("LEDGER_WORK_DIR") or "").strip()
    if env:
        return env
    return os.path.join(SKILL_ROOT, "data")


def env_path():
    return os.path.join(SKILL_ROOT, ".env")


def state_path():
    directory = data_dir()
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "state.json")
