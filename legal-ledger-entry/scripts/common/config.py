# -*- coding: utf-8 -*-
"""从技能根目录 .env 读取飞书与台账坐标。不打印密钥。"""
import os

from dotenv import load_dotenv

from common import paths

load_dotenv(paths.env_path())

_REQUIRED = (
    ("app_id", "FEISHU_APP_ID"),
    ("app_secret", "FEISHU_APP_SECRET"),
    ("approval_code", "FEISHU_APPROVAL_CODE"),
    ("app_token", "FEISHU_LEDGER_APP_TOKEN"),
    ("main_table", "FEISHU_LEDGER_MAIN_TABLE"),
    ("sub_table", "FEISHU_LEDGER_SUB_TABLE"),
    ("category_table", "FEISHU_LEDGER_CATEGORY_TABLE"),
)


def get_config():
    missing = [env for _key, env in _REQUIRED if not (os.getenv(env) or "").strip()]
    if missing:
        raise RuntimeError("缺少环境变量: " + ", ".join(missing))
    return {key: os.getenv(env).strip() for key, env in _REQUIRED}


def present(name):
    return bool((os.getenv(name) or "").strip())


def approver_user_id():
    return (os.getenv("FEISHU_APPROVER_USER_ID") or "").strip()


def approver_whitelist():
    """放行白名单。空时回退为当前绑定人，兼容未写白名单的旧安装。"""
    raw = (os.getenv("FEISHU_APPROVER_WHITELIST") or "").strip()
    ids = [item.strip() for item in raw.split(",") if item.strip()]
    if ids:
        return ids
    bound = approver_user_id()
    return [bound] if bound else []
