# -*- coding: utf-8 -*-
"""统一从 .env 读取企查查 + 远程 MySQL 凭据。凭据缺失时给出明确提示。

本技能自包含:.env 位于技能根目录(由 common.paths.env_path() 给出),
不依赖 01-poc。飞书/电子签凭据不属于本技能,不读取、不校验。
平台环境变量同样生效(.env 为本地兜底,不覆盖已存在的环境变量)。
"""
import os
from dotenv import load_dotenv

from common import paths

load_dotenv(paths.env_path())

_QCC_REQUIRED = ["QCC_APP_KEY", "QCC_SECRET_KEY"]
_MYSQL_REQUIRED = ["MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB"]


def _require(name):
    v = os.getenv(name)
    if not v:
        raise RuntimeError("缺少环境变量 %s,请先复制 .env.example 为 .env 并填写(技能根目录)" % name)
    return v


def get_qcc():
    """企查查客户账号凭据。全部接口走这一套。"""
    return {
        "app_key": _require("QCC_APP_KEY"),
        "secret_key": _require("QCC_SECRET_KEY"),
    }


def get_mysql():
    """远程 MySQL 落库配置。任一必填项缺失则返回 None(调用方必须判失败,不回退)。"""
    missing = [n for n in _MYSQL_REQUIRED if not (os.getenv(n) or "").strip()]
    if missing:
        return None
    port_raw = (os.getenv("MYSQL_PORT") or "3306").strip()
    try:
        port = int(port_raw)
    except ValueError:
        return None
    return {
        "host": (os.getenv("MYSQL_HOST") or "").strip(),
        "port": port,
        "user": (os.getenv("MYSQL_USER") or "").strip(),
        "password": os.getenv("MYSQL_PASSWORD") or "",
        "database": (os.getenv("MYSQL_DB") or "").strip(),
        "charset": os.getenv("MYSQL_CHARSET") or "utf8mb4",
        "table": os.getenv("MYSQL_TABLE") or "legal_company_query",
    }


# ---- 凭据体检:企查查 + MySQL 两组均必填 ----

_CRED_GROUPS = [
    ("企查查(必填,全部接口走客户账号)", _QCC_REQUIRED,
     "技能根目录复制 .env.example 为 .env,填写 QCC_APP_KEY / QCC_SECRET_KEY(企查查开放平台)"),
    ("远程 MySQL(必填,缺则无法使用本技能)", _MYSQL_REQUIRED,
     "技能根目录 .env 补齐 MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB"),
]


def check_all():
    """一次性体检企查查 + MySQL。返回 (ok: bool, report: list[dict])。

    缺任一必填即 ok=False,不抛异常。
    """
    report = []
    all_ok = True
    for group, names, ask in _CRED_GROUPS:
        present = [(n, bool((os.getenv(n) or "").strip())) for n in names]
        group_ok = all(p for _, p in present)
        if not group_ok:
            all_ok = False
        report.append({
            "group": group,
            "vars": present,
            "all_present": group_ok,
            "ask": ask if not group_ok else "",
        })
    return all_ok, report


def print_report(report):
    print("=" * 60)
    print("凭据体检")
    print("=" * 60)
    for item in report:
        flag = "[OK]" if item["all_present"] else "[缺失]"
        print("%s %s" % (flag, item["group"]))
        for name, present in item["vars"]:
            print("    %s %s" % ("[x]" if present else "[ ]", name))
        if not item["all_present"]:
            print("    -> 去哪配: %s" % item["ask"])
    print("=" * 60)


if __name__ == "__main__":
    ok, rep = check_all()
    print_report(rep)
    print("凭据就绪" if ok else "凭据缺失,请按上方提示补齐 .env")
