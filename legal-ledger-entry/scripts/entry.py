# -*- coding: utf-8 -*-
"""按审批单号把合同写入台账副本。确认审批意见后，可用绑定的使用人身份放行当前待办。

用法:
  py scripts/entry.py check
  py scripts/entry.py bind-user --mobile 13800138000
  py scripts/entry.py bind-user --email name@example.com
  py scripts/entry.py bind-user --add --mobile 13800138000
  py scripts/entry.py entry 202609090050
  py scripts/entry.py entry 202609090050 --dry-run
  py scripts/entry.py release 202609090050 --comment "合同编号：BTSP202610099"
"""
import argparse
import json
import os
import re
import sys

from common import audit, paths
from common.config import approver_user_id, approver_whitelist, get_config, present
from common.envfile import upsert_env
from common.state import StateStore
from feishu.client import FeishuApiError, FeishuClient, parse_form
from ledger.doc_convert import ensure_docx
from ledger.docx_parse import parse_docx
from ledger.errors import LedgerStop
from ledger.form import extract_ledger_fields, find_attachment
from ledger.release import comment_has_number, pending_tasks, preview, task_id_of
from ledger.widgets import NO_FIELD, PARENT_LOOKUP_FIELD, SUB_NO_FIELD, widgets_for
from ledger.writer import (
    build_main_fields,
    build_sub_fields,
    category_record_id,
    map_our_entity,
    maps_ok,
    read_contract_no,
    validate_schema,
)


def _operator_id(cfg):
    if cfg is not None and "approver_user_id" in cfg:
        return (cfg.get("approver_user_id") or "").strip()
    return approver_user_id()


def _number_field(cfg, table_id):
    if table_id == (cfg or {}).get("main_table"):
        return NO_FIELD
    return SUB_NO_FIELD


def _release_view(client, instance_code, user_id, contract_no, already_released, dry_run, detail=None):
    if dry_run or already_released or not user_id or not (contract_no or "").strip():
        return preview(user_id, contract_no, [], already_released, dry_run)
    if detail is None:
        try:
            detail = client.get_instance(instance_code)
        except Exception:
            return {
                "release": "skipped",
                "release_reason": "读取审批待办失败，台账已写入，未放行",
            }
    return preview(user_id, contract_no, detail.get("task_list") or [], False, False)


def _normalize_mobile(raw):
    text = re.sub(r"[\s-]", "", raw or "")
    if text.startswith("+86"):
        text = text[3:]
    elif text.startswith("86") and len(text) == 13:
        text = text[2:]
    return text


def run_entry(serial, dry_run=False, client=None, state=None, cfg=None):
    cfg = cfg or get_config()
    client = client or FeishuClient(cfg["app_id"], cfg["app_secret"])
    state = state or StateStore()
    serial = str(serial or "").strip()

    try:
        matches = client.query_by_serial(cfg["approval_code"], serial)
    except LedgerStop:
        raise
    except Exception:
        raise LedgerStop("按审批单号查询实例失败，已停住") from None
    if len(matches) != 1:
        raise LedgerStop("按审批单号 %s 找到 %d 条实例，需要唯一一条，已停住" % (serial, len(matches)))

    instance_code = matches[0].get("instance_code") or ""
    if not instance_code:
        raise LedgerStop("审批实例没有 instance_code，已停住")

    previous = state.get(instance_code) or {}
    if previous.get("record_id"):
        contract_no = previous.get("contract_no") or ""
        if not contract_no:
            try:
                contract_no = read_contract_no(
                    {}, client, cfg["app_token"], previous.get("table_id") or "",
                    previous["record_id"],
                    field_name=_number_field(cfg, previous.get("table_id")),
                )
            except Exception:
                contract_no = ""
            else:
                state.update(instance_code, contract_no=contract_no)
        result = {
            "ok": True,
            "idempotent": True,
            "instance_code": instance_code,
            "table_id": previous.get("table_id") or "",
            "record_id": previous["record_id"],
            "kind": previous.get("kind") or "",
        }
        if contract_no:
            result["contract_no"] = contract_no
        result.update(_release_view(
            client, instance_code, _operator_id(cfg), contract_no,
            already_released=bool(previous.get("released")), dry_run=False,
        ))
        return result

    try:
        detail = client.get_instance(instance_code)
    except LedgerStop:
        raise
    except Exception:
        raise LedgerStop("读取审批实例失败，已停住") from None
    form = parse_form(detail.get("form"))
    widgets = widgets_for(cfg.get("approval_code"))
    ledger = extract_ledger_fields(form, widgets)
    if not (ledger.get("相对方主体（全称）") or "").strip():
        # 2026-10-26 口径：不从合同兜底，如实反馈尝试过的控件后停住
        tried = "、".join(
            "%s(%s)" % (label, widgets[key])
            for key, label in (
                ("counterparty", "签约类型"),
                ("customer", "客户名称"),
                ("supplier", "供应商名称"),
            )
        )
        raise LedgerStop(
            "未从审批表单取到相对方主体：已依次尝试 %s，均无值或控件不存在"
            "（若表单里实际填了企业，多半是控件 id 与该审批定义不一致）。"
            "已停住，不建行、不占号，请人工核对" % tried
        )
    url = find_attachment(form, widgets)
    if not url:
        raise LedgerStop("审批单里没有合同附件，已停住")

    try:
        blob = client.download(url)
    except LedgerStop:
        raise
    except Exception:
        raise LedgerStop("合同附件下载失败，已停住") from None
    blob = ensure_docx(blob)  # 旧版 .doc 在本机 Word 静默转临时 .docx；不可用时停住
    parsed = parse_docx(blob)
    kind = parsed["kind"]
    for_sub = kind != "主合同"
    entity = map_our_entity(ledger.get("签约主体"), for_sub=for_sub)

    parent_record_id = ""
    cat_id = None
    if for_sub:
        if not parsed.get("parent_no"):
            raise LedgerStop("子合同未找到唯一主合同编号，已停住，不建子行")
        try:
            parents = client.bitable_search(
                cfg["app_token"], cfg["main_table"], PARENT_LOOKUP_FIELD, parsed["parent_no"],
                operator="contains",
            )
        except LedgerStop:
            raise
        except Exception:
            raise LedgerStop("按主合同编号查询台账失败，已停住，不建子行") from None
        if len(parents) != 1:
            raise LedgerStop(
                "主表按合同编号 %s 查到 %d 行，需要唯一一行，已停住，不建子行"
                % (parsed["parent_no"], len(parents))
            )
        parent_record_id = parents[0].get("record_id") or ""
        if not parent_record_id:
            raise LedgerStop("主合同行没有 record_id，已停住，不建子行")
        cat_id = category_record_id(kind)

    try:
        main_fields = client.bitable_list_fields(cfg["app_token"], cfg["main_table"])
        sub_fields = client.bitable_list_fields(cfg["app_token"], cfg["sub_table"])
    except LedgerStop:
        raise
    except Exception:
        raise LedgerStop("读取台账字段失败，已停住") from None

    if for_sub:
        try:
            rows = client.bitable_list_records(cfg["app_token"], cfg["category_table"])
        except LedgerStop:
            raise
        except Exception:
            raise LedgerStop("读取分类表失败，已停住") from None
        validate_schema(
            sub_fields,
            entity,
            category_record_id=cat_id,
            category_record_ids=[row.get("record_id") for row in rows],
        )
        fields = build_sub_fields(entity, ledger, parent_record_id, cat_id)
        table_id = cfg["sub_table"]
        table_name = "子表"
    else:
        validate_schema(main_fields, entity)
        fields = build_main_fields(parsed["title"], entity, ledger)
        table_id = cfg["main_table"]
        table_name = "主表"

    if dry_run:
        result = {
            "ok": True,
            "dry_run": True,
            "contract_no": "DRYRUN",
            "instance_code": instance_code,
            "table_id": table_id,
            "table": table_name,
            "kind": kind,
            "fields": fields,
        }
        result.update(preview("", "", [], False, dry_run=True))
        return result

    try:
        record_id, resp_fields = client.bitable_create_record(cfg["app_token"], table_id, fields)
    except LedgerStop:
        raise
    except Exception:
        raise LedgerStop("写入台账失败，已停住") from None
    if not record_id:
        raise LedgerStop("台账没有返回记录编号，已停住")

    try:
        contract_no = read_contract_no(
            resp_fields, client, cfg["app_token"], table_id, record_id,
            field_name=_number_field(cfg, table_id),
        )
    except Exception:
        # 行已建成：无论编号超时还是回读接口异常，都先记 record_id，重跑走幂等不再建行
        state.mark(instance_code, table_id=table_id, record_id=record_id, kind=kind)
        raise
    state.mark(
        instance_code, table_id=table_id, record_id=record_id,
        kind=kind, contract_no=contract_no,
    )
    result = {
        "ok": True,
        "instance_code": instance_code,
        "table_id": table_id,
        "record_id": record_id,
        "contract_no": contract_no,
        "kind": kind,
    }
    result.update(_release_view(
        client, instance_code, _operator_id(cfg), contract_no,
        already_released=False, dry_run=False, detail=detail,
    ))
    return result


def cmd_check(client=None, cfg=None):
    report = []
    problems = []

    def mark(item, ok, detail):
        report.append({"item": item, "ok": bool(ok), "detail": detail})
        if not ok:
            problems.append(detail)

    mark("entity_map", maps_ok(), "主体映射和分类映射文件都在" if maps_ok() else "缺少 entity_map.json 或 category_map.json")
    mark("approval_code", present("FEISHU_APPROVAL_CODE") or bool((cfg or {}).get("approval_code")),
         "审批定义 code 已填写" if (present("FEISHU_APPROVAL_CODE") or (cfg or {}).get("approval_code"))
         else "请在 .env 填写审批定义 code（FEISHU_APPROVAL_CODE）")
    mark("ledger_app_token", present("FEISHU_LEDGER_APP_TOKEN") or bool((cfg or {}).get("app_token")),
         "台账 app_token 已填写" if (present("FEISHU_LEDGER_APP_TOKEN") or (cfg or {}).get("app_token"))
         else "请在 .env 填写台账 app_token（FEISHU_LEDGER_APP_TOKEN）")
    tables_ok = all(present(name) for name in (
        "FEISHU_LEDGER_MAIN_TABLE", "FEISHU_LEDGER_SUB_TABLE", "FEISHU_LEDGER_CATEGORY_TABLE",
    )) or all((cfg or {}).get(key) for key in ("main_table", "sub_table", "category_table"))
    mark("ledger_tables", tables_ok,
         "主表、子表、分类表 id 已填写" if tables_ok else "请在 .env 填写台账主表、子表、分类表 id")

    app_id = ((cfg or {}).get("app_id") or os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = ((cfg or {}).get("app_secret") or os.getenv("FEISHU_APP_SECRET") or "").strip()
    app_token = ((cfg or {}).get("app_token") or os.getenv("FEISHU_LEDGER_APP_TOKEN") or "").strip()
    main_table = ((cfg or {}).get("main_table") or os.getenv("FEISHU_LEDGER_MAIN_TABLE") or "").strip()
    token_ok = False
    if not app_id or not app_secret:
        mark("feishu_token", False, "请在 .env 填写飞书应用凭证（FEISHU_APP_ID / FEISHU_APP_SECRET）")
    else:
        try:
            if client is None:
                client = FeishuClient(app_id, app_secret)
            client.tenant_access_token()
            token_ok = True
            mark("feishu_token", True, "飞书访问令牌可获取")
        except Exception:
            mark("feishu_token", False, "飞书访问令牌获取失败，请检查应用凭证是否有效")

    if token_ok and app_token and main_table:
        try:
            fields = client.bitable_list_fields(app_token, main_table)
            mark("main_fields", bool(fields), "能读取台账主表字段" if fields else "台账主表没有返回字段")
        except Exception:
            mark("main_fields", False, "无法读取台账主表字段，请确认应用已开通多维表格权限并被加为副本协作者")
    else:
        mark("main_fields", False, "飞书令牌或台账坐标还没齐，还不能读取主表字段")

    bound = present("FEISHU_APPROVER_USER_ID")
    detail = "使用人已绑定" if bound else "尚未绑定使用人。录入台账仍可用；放行前执行 bind-user"
    if bound:
        detail += "（放行白名单 %d 人，白名单外绑定需 bind-user --add）" % len(approver_whitelist())
    report.append({
        "item": "approver",
        "ok": bound,
        "detail": detail,
    })

    if problems:
        return {"ok": False, "error": "；".join(problems), "report": report}
    return {"ok": True, "report": report}


def run_bind(mobile="", email="", client=None, cfg=None, env_file=None, add=False):
    mobile = _normalize_mobile(mobile)
    email = (email or "").strip()
    if bool(mobile) == bool(email):
        raise LedgerStop("请只填写手机号或邮箱中的一项")
    cfg = cfg or get_config()
    client = client or FeishuClient(cfg["app_id"], cfg["app_secret"])
    user_id = client.lookup_user_id(mobile=mobile, email=email)
    credential = mobile or email

    updates = {
        "FEISHU_APPROVER_USER_ID": user_id,
        "FEISHU_APPROVER_USER_ID_TYPE": "user_id",
    }
    whitelist = approver_whitelist()
    if user_id in whitelist:
        updates["FEISHU_APPROVER_WHITELIST"] = ",".join(whitelist)
        audit.log_event("bind", credential, user_id, True, "白名单内换绑")
    elif not whitelist:
        updates["FEISHU_APPROVER_WHITELIST"] = user_id
        audit.log_event("bind", credential, user_id, True, "首次安装，初始化放行白名单")
    elif add:
        updates["FEISHU_APPROVER_WHITELIST"] = ",".join(whitelist + [user_id])
        audit.log_event("bind_add", credential, user_id, True, "管理员显式新增放行白名单")
    else:
        audit.log_event(
            "bind_refused", credential, user_id, False,
            "不在放行白名单。如确要新增，由管理员执行 bind-user --add",
        )
        raise LedgerStop(
            "该手机号或邮箱对应的用户不在本机放行白名单，已拒绝绑定。"
            "确要新增请由管理员执行 bind-user --add --mobile <手机号>"
        )
    upsert_env(env_file or paths.env_path(), updates)
    return {"ok": True, "bound": True}


def run_release(serial, comment, client=None, state=None, cfg=None):
    cfg = cfg or get_config()
    client = client or FeishuClient(cfg["app_id"], cfg["app_secret"])
    state = state or StateStore()
    user_id = _operator_id(cfg)
    if not user_id:
        raise LedgerStop("尚未绑定使用人，请先绑定手机号或邮箱")
    whitelist = approver_whitelist()
    if whitelist and user_id not in whitelist:
        audit.log_event(
            "release_refused", "", user_id, False,
            "单号 %s：绑定使用人不在放行白名单，未放行" % (serial or ""),
        )
        raise LedgerStop("绑定使用人不在本机放行白名单，未放行。请管理员核对 data/bind_audit.jsonl 后处理")
    comment = (comment or "").strip()
    if not comment:
        raise LedgerStop("请确认审批意见后再放行")

    serial = str(serial or "").strip()
    try:
        matches = client.query_by_serial(cfg["approval_code"], serial)
    except LedgerStop:
        raise
    except Exception:
        raise LedgerStop("按审批单号查询实例失败，已停住") from None
    if len(matches) != 1:
        raise LedgerStop("按审批单号 %s 找到 %d 条实例，需要唯一一条，已停住" % (serial, len(matches)))
    instance_code = matches[0].get("instance_code") or ""
    if not instance_code:
        raise LedgerStop("审批实例没有 instance_code，已停住")

    previous = state.get(instance_code) or {}
    if not previous.get("record_id"):
        raise LedgerStop("这张审批还没有写入台账，请先录入后再放行")
    if previous.get("released"):
        result = {
            "ok": True,
            "released": True,
            "idempotent": True,
            "instance_code": instance_code,
        }
        if previous.get("contract_no"):
            result["contract_no"] = previous["contract_no"]
        return result

    contract_no = previous.get("contract_no") or ""
    if not contract_no:
        raise LedgerStop("合同编号尚未生成，未放行")
    if not comment_has_number(comment, contract_no):
        raise LedgerStop("审批意见里没有合同编号 %s，未放行" % contract_no)

    try:
        detail = client.get_instance(instance_code)
    except LedgerStop:
        raise
    except Exception:
        raise LedgerStop("读取审批待办失败，未放行") from None
    tasks = pending_tasks(detail.get("task_list") or [], user_id)
    if not tasks:
        raise LedgerStop("当前使用人不是这张单的待办审批人，未放行")
    if len(tasks) > 1:
        raise LedgerStop("当前使用人在这张单上有多条待办，已停住，未放行")
    task_id = task_id_of(tasks[0])
    if not task_id:
        raise LedgerStop("待办没有任务编号，未放行")

    try:
        client.approve_task(
            cfg["approval_code"], instance_code, task_id, user_id, comment,
        )
    except (LedgerStop, FeishuApiError):
        raise
    except Exception:
        raise LedgerStop("同意审批失败，台账已写入，未放行") from None
    state.update(instance_code, released=True, task_id=task_id)
    audit.log_event(
        "release", "", user_id, True,
        "单号 %s 已同意，意见含合同编号 %s" % (serial, contract_no),
    )
    return {
        "ok": True,
        "released": True,
        "instance_code": instance_code,
        "contract_no": contract_no,
        "task_id": task_id,
        "node_name": tasks[0].get("node_name") or "",
    }


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="合同台账录入")
    sub = parser.add_subparsers(dest="cmd", required=True)
    entry = sub.add_parser("entry", help="按审批单号录入台账")
    entry.add_argument("serial", help="审批单号，如 202609090050")
    entry.add_argument("--dry-run", action="store_true", help="只解析和校验，不写台账")
    sub.add_parser("check", help="检查凭据、坐标和主表字段")
    bind = sub.add_parser("bind-user", help="用手机号或邮箱绑定这台电脑的使用人")
    who = bind.add_mutually_exclusive_group(required=True)
    who.add_argument("--mobile", help="使用人手机号")
    who.add_argument("--email", help="使用人个人邮箱")
    bind.add_argument("--add", action="store_true", help="管理员显式把该用户加入放行白名单后再绑定")
    release = sub.add_parser("release", help="用已确认的审批意见放行当前待办")
    release.add_argument("serial", help="审批单号，如 202609090050")
    release.add_argument("--comment", required=True, help="确认后的审批意见，须包含合同编号")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "check":
            result = cmd_check()
        elif args.cmd == "bind-user":
            result = run_bind(mobile=args.mobile or "", email=args.email or "", add=args.add)
        elif args.cmd == "release":
            result = run_release(args.serial, args.comment)
        else:
            result = run_entry(args.serial, dry_run=args.dry_run)
    except LedgerStop as exc:
        result = {"ok": False, "error": str(exc)}
    except (FeishuApiError, RuntimeError) as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception:
        result = {"ok": False, "error": "录入未完成，请检查网络、凭据与附件后重试"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
