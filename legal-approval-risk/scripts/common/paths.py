# -*- coding: utf-8 -*-
"""技能内路径约定(自包含,不依赖 01-poc)。

技能目录布局:
  legal-approval-risk/          <- skill_root
  ├── SKILL.md
  ├── .env                      <- 用户安装后填写 FEISHU_* + QCC_* + MYSQL_*
  ├── scripts/
  │   ├── poll.py
  │   ├── common/paths.py       <- 本文件
  │   ├── feishu/
  │   └── qcc/...
  └── data/                     <- 运行时写入(状态/快照/审计),可被 QCC_DATA_DIR 覆盖

本文件位于 scripts/common/paths.py,故:
  skill_root = dirname(dirname(dirname(__file__)))  # common -> scripts -> skill_root
"""
import os

_THIS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(_THIS)
SKILL_ROOT = os.path.dirname(SCRIPTS_DIR)


def data_dir():
    """运行时数据目录(快照/审计)。优先用环境变量 QCC_DATA_DIR,否则技能内 data/。"""
    env = os.getenv("QCC_DATA_DIR")
    if env:
        return env
    return os.path.join(SKILL_ROOT, "data")


def env_path():
    """.env 路径(技能根)。可被环境变量 QCC_ENV_FILE 覆盖。"""
    return os.getenv("QCC_ENV_FILE") or os.path.join(SKILL_ROOT, ".env")


def snapshots_dir():
    return os.path.join(data_dir(), "snapshots")


def snapshots_json_dir():
    """JSON 快照子目录(snapshots/json)。Excel 汇总与 _manifest.jsonl 仍存 snapshots 根。"""
    return os.path.join(snapshots_dir(), "json")


def reports_dir():
    """Excel 汇总落盘目录(与快照同级,便于一并交给用户)。"""
    d = snapshots_dir()
    os.makedirs(d, exist_ok=True)
    return d


def audit_log():
    d = data_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "audit.jsonl")


def state_path():
    """已处理审批实例的幂等状态文件。"""
    d = data_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "state.json")
