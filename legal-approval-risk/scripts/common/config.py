# -*- coding: utf-8 -*-
"""统一从 .env 读取飞书 + 企查查 + 远程 MySQL 凭据。凭据缺失时给出明确提示。

本技能自包含:.env 位于技能根目录(由 common.paths.env_path() 给出),
不依赖 01-poc。平台环境变量同样生效(.env 为本地兜底,不覆盖已存在的环境变量)。
"""
import os
from dotenv import load_dotenv

from common import paths

load_dotenv(paths.env_path())

_QCC_REQUIRED = ["QCC_APP_KEY", "QCC_SECRET_KEY"]
_QCC_MONICA_REQUIRED = ["QCC_APP_KEY_MONICA", "QCC_SECRET_KEY_MONICA"]
_MYSQL_REQUIRED = ["MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB"]
_FEISHU_REQUIRED = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APPROVAL_CODES"]

# 固定使用 Monica 账号的接口(权限受限,主账号无权调用)
QCC_MONICA_APIS = (886, 2006, 887, 888, 889)


def _require(name):
    v = os.getenv(name)
    if not v:
        raise RuntimeError("缺少环境变量 %s,请先复制 .env.example 为 .env 并填写(技能根目录)" % name)
    return v


def get_qcc():
    """主账号凭据,用于 856/739。"""
    return {
        "app_key": _require("QCC_APP_KEY"),
        "secret_key": _require("QCC_SECRET_KEY"),
    }


def get_qcc_monica():
    """Monica 凭据,固定用于 886/2006/887/888/889。

    可选:无 QCC_APP_KEY_MONICA 时回退主账号(生产账号已开通全部接口,只需填两项)。
    半套(有 AppKey 无 Secret)仍报错,不拿主账号 Secret 拼 Monica Key。
    """
    monica_key = os.getenv("QCC_APP_KEY_MONICA")
    if not monica_key:
        return get_qcc()
    return {
        "app_key": monica_key,
        "secret_key": _require("QCC_SECRET_KEY_MONICA"),
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


def _company_widget():
    """对方企业那一个表单控件(名称或 widget id)。新名 FEISHU_FIELD_COMPANY,旧名 COMPANY_NAME 仍认。"""
    v = (os.getenv("FEISHU_FIELD_COMPANY") or os.getenv("FEISHU_FIELD_COMPANY_NAME") or "").strip()
    if not v:
        raise RuntimeError(
            "缺少环境变量 FEISHU_FIELD_COMPANY,请填写审批表单里「对方单位名称」控件名"
            "(或 widget id)。旧名 FEISHU_FIELD_COMPANY_NAME 仍可用"
        )
    return v


def get_feishu():
    """飞书自建应用凭据 + 审批配置。缺任一必填项抛 RuntimeError。"""
    return {
        "app_id": _require("FEISHU_APP_ID"),
        "app_secret": _require("FEISHU_APP_SECRET"),
        "approval_codes": [c.strip() for c in (_require("FEISHU_APPROVAL_CODES") or "").split(",") if c.strip()],
        "field_company": _company_widget(),
        "query_window_days": int(os.getenv("FEISHU_QUERY_WINDOW_DAYS") or "30"),
        "comment_dry_run": (os.getenv("FEISHU_COMMENT_DRY_RUN") or "").lower() in ("1", "true", "yes"),
        "comment_user_id": (os.getenv("FEISHU_COMMENT_USER_ID") or "").strip(),
        "comment_user_id_type": (os.getenv("FEISHU_COMMENT_USER_ID_TYPE") or "open_id").strip(),
    }


# ---- 凭据体检:飞书 + 企查查 + MySQL 三组均必填 ----

_CRED_GROUPS = [
    ("飞书自建应用(必填,审批查询/详情/评论)", _FEISHU_REQUIRED,
     "技能根目录复制 .env.example 为 .env,填写 FEISHU_APP_ID / FEISHU_APP_SECRET(自建应用凭证),"
     "FEISHU_APPROVAL_CODES(合同审批定义 code,逗号分隔),以及表单对方企业控件 "
     "FEISHU_FIELD_COMPANY(当前是「对方单位名称」)"),
    ("企查查-主账号(必填,用于 2006/856/739/887,以及缺 Monica 时回退 886/888/889)", _QCC_REQUIRED,
     "技能根目录复制 .env.example 为 .env,填写 QCC_APP_KEY / QCC_SECRET_KEY(企查查开放平台)"),
    ("企查查-Monica 账号(可选,用于 886/888/889;缺则回退主账号,半套则失败)", _QCC_MONICA_REQUIRED,
     "技能根目录 .env 补齐 QCC_APP_KEY_MONICA / QCC_SECRET_KEY_MONICA(测试账号,固定用于 886/888/889);生产账号已开通全部接口时可留空"),
    ("远程 MySQL(必填,缺则无法使用本技能)", _MYSQL_REQUIRED,
     "技能根目录 .env 补齐 MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB"),
]

# 可选凭据组:全部缺失视为通过(回退主账号),半套缺失才失败
_OPTIONAL_GROUPS = {"企查查-Monica 账号(可选,用于 886/888/889;缺则回退主账号,半套则失败)"}


def check_all():
    """一次性体检飞书 + 企查查 + MySQL。返回 (ok: bool, report: list[dict])。

    缺任一必填即 ok=False,不抛异常。可选组(Monica)全缺视为通过,半套才失败。
    """
    report = []
    all_ok = True
    for group, names, ask in _CRED_GROUPS:
        present = [(n, bool((os.getenv(n) or "").strip())) for n in names]
        if group.startswith("飞书"):
            present.append((
                "FEISHU_FIELD_COMPANY",
                bool((os.getenv("FEISHU_FIELD_COMPANY") or os.getenv("FEISHU_FIELD_COMPANY_NAME") or "").strip()),
            ))
        present_count = sum(1 for _, p in present if p)
        if group in _OPTIONAL_GROUPS:
            # 全缺=通过(回退主账号);半套=失败;全齐=通过
            group_ok = (present_count == 0) or (present_count == len(names))
        else:
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
