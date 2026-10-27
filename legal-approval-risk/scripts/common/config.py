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
_MYSQL_REQUIRED = ["MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB"]
_FEISHU_REQUIRED = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_APPROVAL_CODES"]


def _require(name):
    v = os.getenv(name)
    if not v:
        raise RuntimeError("缺少环境变量 %s,请先复制 .env.example 为 .env 并填写(技能根目录)" % name)
    return v


def _approver_user_id():
    """预审节点审批人账号(本技能以该账号同意任务并回填评级/附件)。

    优先 FEISHU_APPROVER_USER_ID;未填则回退到 FEISHU_COMMENT_USER_ID(同一员工账号)。
    """
    return (os.getenv("FEISHU_APPROVER_USER_ID") or os.getenv("FEISHU_COMMENT_USER_ID") or "").strip()


def _approver_user_id_type():
    return (os.getenv("FEISHU_APPROVER_USER_ID_TYPE")
            or os.getenv("FEISHU_COMMENT_USER_ID_TYPE") or "open_id").strip()


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


def _company_fields():
    """表单里的候选企业控件(名称或 widget id),多个用逗号分隔。

    新名 FEISHU_FIELD_COMPANY,旧名 FEISHU_FIELD_COMPANY_NAME 仍认。
    字段名不固定:可能是「对方单位名称」「相对方企业名称」「签约企业名称」「达人公司名称」,
    都是外部选项单选,值形如「名称｜信用代码｜法人」。表单里命中的字段都没出现则跳过该实例。
    """
    v = (os.getenv("FEISHU_FIELD_COMPANY") or os.getenv("FEISHU_FIELD_COMPANY_NAME") or "").strip()
    fields = []
    for part in v.replace("，", ",").split(","):
        part = part.strip()
        if part and part not in fields:
            fields.append(part)
    if not fields:
        raise RuntimeError(
            "缺少环境变量 FEISHU_FIELD_COMPANY,请填写审批表单里的企业控件名,"
            "多个用逗号分隔(如 对方单位名称,相对方企业名称,签约企业名称,达人公司名称;"
            "也可以是 widget id)。旧名 FEISHU_FIELD_COMPANY_NAME 仍可用"
        )
    return fields


def _rating_field():
    """回填目标:【企业风险评级】文本控件名(或 widget id)。默认「企业风险评级」。"""
    return (os.getenv("FEISHU_FIELD_RATING") or "企业风险评级").strip()


def _attachment_field():
    """回填目标:【附件】附件控件名(或 widget id)。默认「附件」。"""
    return (os.getenv("FEISHU_FIELD_ATTACHMENT") or "附件").strip()


def _field_contract_attach():
    """合同 Word 附件控件名(或 widget id)。默认「合同附件」,
    模糊命中「合同附件(请勿上传PDF、图片)」(与「风险报告附件」无子串冲突)。"""
    return (os.getenv("FEISHU_FIELD_CONTRACT_ATTACH") or "合同附件").strip()


def _field_our_entity():
    """我方「签约主体」控件名(或 widget id),合同主体校验的排除法基准。默认「签约主体」。"""
    return (os.getenv("FEISHU_FIELD_OUR_ENTITY") or "签约主体").strip()


def _subject_check_enabled():
    """合同主体一致性校验开关(2026-10-19):默认开;置 0/false 关闭回到旧行为。"""
    return (os.getenv("FEISHU_SUBJECT_CHECK") or "1").strip().lower() not in ("0", "false", "no", "off")


def _cache_days():
    """企业风险信息库缓存有效天数;超过则重查企查查。默认 90(近 3 个月)。"""
    raw = (os.getenv("FEISHU_CACHE_DAYS") or "90").strip()
    try:
        days = int(raw)
    except ValueError:
        days = 90
    return max(days, 1)


def get_feishu():
    """飞书自建应用凭据 + 审批配置。缺任一必填项抛 RuntimeError。

    approver_user_id 可空(仅 test-instance 干跑允许);poll/check 必须配齐。
    """
    return {
        "app_id": _require("FEISHU_APP_ID"),
        "app_secret": _require("FEISHU_APP_SECRET"),
        "approval_codes": [c.strip() for c in (_require("FEISHU_APPROVAL_CODES") or "").split(",") if c.strip()],
        # 测试流程码(可选):仅 poll --test 时扫描,不配置则 --test 直接报错(不回退正式码)
        "approval_codes_test": [c.strip() for c in (os.getenv("FEISHU_APPROVAL_CODES_TEST") or "").split(",") if c.strip()],
        "field_companies": _company_fields(),
        "query_window_days": int(os.getenv("FEISHU_QUERY_WINDOW_DAYS") or "30"),
        "comment_dry_run": (os.getenv("FEISHU_COMMENT_DRY_RUN") or "").lower() in ("1", "true", "yes"),
        "comment_user_id": (os.getenv("FEISHU_COMMENT_USER_ID") or "").strip(),
        "comment_user_id_type": (os.getenv("FEISHU_COMMENT_USER_ID_TYPE") or "open_id").strip(),
        "approver_user_id": _approver_user_id(),
        "approver_user_id_type": _approver_user_id_type(),
        "field_rating": _rating_field(),
        "field_attachment": _attachment_field(),
        "field_contract_attach": _field_contract_attach(),
        "field_our_entity": _field_our_entity(),
        "subject_check_enabled": _subject_check_enabled(),
        "cache_days": _cache_days(),
    }


# ---- 凭据体检:飞书 + 企查查 + MySQL 三组均必填 ----

_CRED_GROUPS = [
    ("飞书自建应用(必填,审批查询/详情/同意任务)", _FEISHU_REQUIRED,
     "技能根目录复制 .env.example 为 .env,填写 FEISHU_APP_ID / FEISHU_APP_SECRET(自建应用凭证),"
     "FEISHU_APPROVAL_CODES(合同审批定义 code,逗号分隔),以及表单企业控件 "
     "FEISHU_FIELD_COMPANY(多个逗号分隔,如 对方单位名称,相对方企业名称,签约企业名称,达人公司名称;"
     "命中的字段都没出现则跳过该实例)。应用需补开 approval:task(同意任务)权限"),
    ("企查查(必填,全部接口走客户账号)", _QCC_REQUIRED,
     "技能根目录复制 .env.example 为 .env,填写 QCC_APP_KEY / QCC_SECRET_KEY(企查查开放平台)"),
    ("远程 MySQL(必填,缺则无法使用本技能)", _MYSQL_REQUIRED,
     "技能根目录 .env 补齐 MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB"),
]


def check_all(require_approver=True):
    """一次性体检飞书 + 企查查 + MySQL(+ 预审账号)。返回 (ok: bool, report: list[dict])。

    缺任一必填即 ok=False,不抛异常。require_approver=False 时预审账号缺失不拉低 ok
    (供 test-instance 干跑核实控件用);poll/check 默认要求预审账号。
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
        group_ok = all(p for _, p in present)
        if not group_ok:
            all_ok = False
        report.append({
            "group": group,
            "vars": present,
            "all_present": group_ok,
            "ask": ask if not group_ok else "",
        })

    # 预审审批账号(poll/check 必填;test-instance 干跑可缺)
    approver_id = _approver_user_id()
    approver_present = bool(approver_id)
    approver_ok = True if not require_approver else approver_present
    if not approver_ok:
        all_ok = False
    report.append({
        "group": "预审审批账号(poll 必填,FEISHU_APPROVER_USER_ID;未填可回退 FEISHU_COMMENT_USER_ID)",
        "vars": [("FEISHU_APPROVER_USER_ID", approver_present)],
        "all_present": approver_present,
        "ask": ("在审批流新增「系统风险预审」节点,审批人=该员工账号;在 .env 填写 "
                "FEISHU_APPROVER_USER_ID(其 open_id,或复用 FEISHU_COMMENT_USER_ID)。"
                "用手机号/邮箱反查 open_id 见 SKILL.md") if not approver_present else "",
        "required": require_approver,
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
