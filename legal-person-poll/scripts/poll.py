# -*- coding: utf-8 -*-
"""自然人信息录入申请审批采集：轮询飞书审批实例 → 入库 dim_company。

用法（Windows 用 py / macOS 用 python3）:
  py scripts/poll.py check                     自检:飞书 token + approval_code + MySQL + 表结构
  py scripts/poll.py poll                      一轮采集入库
  py scripts/poll.py test-instance <code>      干跑单实例,打印 form 与解析结果,不写库

设计口径见 00-对齐/26-自然人审批采集-用户自部署提示词与共表设计.md:
  - 只采集**已审批通过(APPROVED)**的实例,近 N 天窗口(默认 3);其他状态不采
  - instance_code 已在库 → 跳过,不留状态轨迹
  - 身份证号双写 credit_code + tax_no;审批快照(完整 C1 响应)存 invoice_json
  - 先查后写,禁用 ON DUPLICATE KEY UPDATE;同一自然人已在库且有字段差异 →
    不写库,输出差异清单计「待人工确认」
  - 身份证号不做硬校验(审批通过即入库),格式异常仅输出提醒(2026-09-03 口径)
"""
import argparse
import json
import os
import re
import sys
import time
import traceback

import requests

BASE = "https://open.feishu.cn/open-apis"
ID_CARD_RE = re.compile(r"^\d{15}$|^\d{17}[\dXx]$")

# --------------------------------------------------------------------------- 配置


def load_cfg():
    here = os.path.dirname(os.path.abspath(__file__))
    for env_path in (os.path.join(here, "..", ".env"), os.path.join(here, ".env")):
        if os.path.exists(env_path):
            try:
                from dotenv import load_dotenv

                load_dotenv(env_path, override=False)
            except ImportError:
                pass
            break

    def _int(key, default):
        try:
            return int(os.getenv(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "app_id": os.getenv("FEISHU_APP_ID", ""),
        "app_secret": os.getenv("FEISHU_APP_SECRET", ""),
        "approval_code": os.getenv("APPROVAL_CODE", "F0A86650-2ED3-4468-BAC4-69E8D941B199"),
        "window_days": _int("WINDOW_DAYS", 3),
        "table": os.getenv("MYSQL_TABLE", "dim_company"),
        "db_host": os.getenv("MYSQL_HOST", ""),
        "db_port": _int("MYSQL_PORT", 3306),
        "db_user": os.getenv("MYSQL_USER", ""),
        "db_password": os.getenv("MYSQL_PASSWORD", ""),
        "db_name": os.getenv("MYSQL_DB", ""),
        "field_name": os.getenv("FIELD_PERSON_NAME", "自然人姓名"),
        "field_id": os.getenv("FIELD_ID_CARD", "自然人身份证号码"),
        "field_phone": os.getenv("FIELD_PHONE", "自然人联系电话"),
        "field_addr": os.getenv("FIELD_ADDRESS", "自然人地址"),
        "field_liaison": os.getenv("FIELD_LIAISON", "我方对接人"),
    }


# --------------------------------------------------------------------------- 飞书


def tenant_token(cfg):
    r = requests.post(
        BASE + "/auth/v3/tenant_access_token/internal",
        json={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]},
        timeout=15,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError("取 token 失败: %s %s" % (data.get("code"), data.get("msg")))
    return data["tenant_access_token"]


def _query_page(token, payload, page_token=""):
    params = {"page_size": 100}
    if page_token:
        params["page_token"] = page_token
    r = requests.post(
        BASE + "/approval/v4/instances/query",
        params=params,
        headers={"Authorization": "Bearer " + token},
        json=payload,
        timeout=30,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError("instances/query 失败: %s %s" % (data.get("code"), data.get("msg")))
    return data.get("data", {})


def query_instance_codes(token, approval_code, start_ms, end_ms):
    """C2:拉时间窗内**已审批通过(APPROVED)**的实例 code(2026-09-03 口志变更:只采通过,
    审批通过即业务把关,其他状态不再采集)。
    2026-09-03 实测(重要坑):
      - 时间参数名必须是 instance_start_time_from / instance_start_time_to(int 型),
        传 start_time/end_time 会被静默忽略并返回空列表(不报错);
      - 响应取 data.instance_list[].instance.code(嵌套对象),不是 instance_code_list;
      - instance_status 须为字符串;数组报 9499,REJECTED/CANCELED 报 99992402。"""
    payload = {
        "approval_code": approval_code,
        "instance_status": "APPROVED",
        "instance_start_time_from": int(start_ms),
        "instance_start_time_to": int(end_ms),
    }
    return _collect_pages(token, payload)


def _collect_pages(token, payload):
    codes, page_token = [], ""
    while True:
        data = _query_page(token, payload, page_token)
        for item in data.get("instance_list") or []:
            inst = item.get("instance") if isinstance(item, dict) else None
            if isinstance(inst, dict) and inst.get("code"):
                codes.append(inst["code"])
            elif isinstance(item, dict) and item.get("instance_code"):
                codes.append(item["instance_code"])
            elif isinstance(item, str):
                codes.append(item)
        if not data.get("has_more"):
            return codes
        page_token = data.get("page_token", "")
        if not page_token:
            return codes


def get_instance(token, instance_code):
    """C1:单个实例完整详情(完整响应原文存 invoice_json)。"""
    r = requests.get(
        BASE + "/approval/v4/instances/" + instance_code,
        headers={"Authorization": "Bearer " + token},
        timeout=30,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError("实例详情失败: %s %s" % (data.get("code"), data.get("msg")))
    return data.get("data", {})


def lookup_users(token, open_ids):
    """通讯录译名:open_id -> {name, mobile}(手机号去 +86 前缀)。查不到记空串。
    需 scope: contact:contact.base:readonly(姓名);手机号视开通情况可能为空。"""
    out = {}
    for oid in open_ids:
        try:
            r = requests.get(
                BASE + "/contact/v3/users/" + oid,
                params={"user_id_type": "open_id"},
                headers={"Authorization": "Bearer " + token},
                timeout=15,
            )
            d = r.json()
            u = ((d.get("data") or {}).get("user")) or {}
            m = u.get("mobile") or ""
            if m.startswith("+86"):
                m = m[3:]
            out[oid] = {"name": u.get("name") or "", "mobile": m}
        except Exception:
            out[oid] = {"name": "", "mobile": ""}
    return out


# --------------------------------------------------------------------------- 表单解析


def parse_form(detail):
    """form 是 JSON 字符串,元素 {type, name, custom_key, value}。按控件显示名索引。"""
    form = detail.get("form")
    try:
        items = json.loads(form) if isinstance(form, str) else (form or [])
    except (ValueError, TypeError):
        items = []
    return {it.get("name", ""): it for it in items if isinstance(it, dict)}


def widget_text(item):
    """把控件 value 规范成文本。人员/部门等控件 value 可能是数组或对象,
    解析不出可读名时保留原始字符串并在输出提示。"""
    if item is None:
        return None
    v = item.get("value")
    if v is None or v == "":
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, list):
        parts = [str(x).strip() for x in v if str(x).strip()]
        return ",".join(parts) if parts else None
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(v)


def extract_person(cfg, by_name):
    """按映射表从表单取自然人字段。"""
    def grab(field_key):
        return widget_text(by_name.get(cfg[field_key]))

    id_card = grab("field_id")
    liaison_item = by_name.get(cfg["field_liaison"])
    id_card_bad = bool(id_card) and not ID_CARD_RE.match(id_card)
    return {
        "name": grab("field_name"),
        "id_card": id_card,
        "phone": grab("field_phone"),
        "address": grab("field_addr"),
        "liaison": widget_text(liaison_item),
        "liaison_open_ids": (liaison_item or {}).get("open_ids") or [],
        "staff_mobile": None,
        # NOT NULL 列缺失即失败的仅姓名/身份证号
        "missing_required": [cfg[k] for k, val in
                             (("field_name", None), ("field_id", None))
                             if not grab(k)],
        # 身份证号格式异常(2026-09-03 口径:不做硬拦截,审批通过即入库,仅输出提醒)
        "id_card_bad": id_card_bad,
        "id_card_bad_value": id_card if id_card_bad else None,
    }


def enrich_liaison(token, person):
    """人员控件译名:通讯录取姓名/手机号,成功则覆盖原始 user_id,失败回退。"""
    oids = person.get("liaison_open_ids") or []
    if not oids:
        return person
    info = lookup_users(token, oids)
    names = [v["name"] for v in info.values() if v["name"]]
    mobiles = [v["mobile"] for v in info.values() if v["mobile"]]
    if names:
        person["liaison"] = ",".join(names)
    if mobiles:
        person["staff_mobile"] = ",".join(mobiles)
    return person


# --------------------------------------------------------------------------- MySQL


def db_conn(cfg):
    import pymysql

    return pymysql.connect(
        host=cfg["db_host"], port=cfg["db_port"], user=cfg["db_user"],
        password=cfg["db_password"], database=cfg["db_name"],
        charset="utf8mb4", autocommit=True, cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_columns(cfg, conn):
    """幂等 ALTER:缺列加列、缺索引加索引(uk_instance_code)。"""
    t = cfg["table"]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
            (cfg["db_name"], t),
        )
        cols = {row["COLUMN_NAME"] for row in cur.fetchall()}
        if not cols:
            raise RuntimeError("表 %s.%s 不存在" % (cfg["db_name"], t))
        spec = [
            ("instance_code", "ALTER TABLE `%s` ADD COLUMN `instance_code` VARCHAR(64) NULL" % t),
            ("contact_address", "ALTER TABLE `%s` ADD COLUMN `contact_address` VARCHAR(255) NULL" % t),
            ("liaison_staff", "ALTER TABLE `%s` ADD COLUMN `liaison_staff` VARCHAR(128) NULL" % t),
            ("staff_mobile", "ALTER TABLE `%s` ADD COLUMN `staff_mobile` VARCHAR(32) NULL" % t),
        ]
        added = []
        for col, ddl in spec:
            if col not in cols:
                cur.execute(ddl)
                added.append(col)
        cur.execute(
            "SELECT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s",
            (cfg["db_name"], t, "uk_instance_code"),
        )
        if not cur.fetchone():
            cur.execute("ALTER TABLE `%s` ADD UNIQUE KEY `uk_instance_code` (`instance_code`)" % t)
            added.append("uk_instance_code")
    return added


def _norm(v):
    """比较用:NULL 与空串视为相等。"""
    return (v or "").strip() if isinstance(v, str) else (v or "")


def classify_and_write(cfg, conn, person, instance_code, invoice_json):
    """先查后写三分支。返回 (action, info):inserted / skipped / confirm / failed。"""
    t = cfg["table"]
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM `%s` WHERE instance_code=%%s" % t, (instance_code,))
        if cur.fetchone():
            return "skipped", {"reason": "已采集(instance_code 在库)"}

        cur.execute(
            "SELECT id, company_name, contact_phone, contact_address, liaison_staff, "
            "staff_mobile FROM `%s` WHERE credit_code=%%s LIMIT 1" % t,
            (person["id_card"],),
        )
        row = cur.fetchone()
        if row:
            diffs = []
            for col, new in (("contact_phone", person["phone"]),
                             ("contact_address", person["address"]),
                             ("liaison_staff", person["liaison"]),
                             ("staff_mobile", person["staff_mobile"])):
                old = _norm(row.get(col))
                if old != _norm(new):
                    diffs.append({"id": row["id"], "field": col,
                                  "old": old or None, "new": new})
            if diffs:
                return "confirm", {"id": row["id"], "diffs": diffs,
                                   "hint": "自然人已在库且字段有差异,待人工确认,未写库"}
            return "skipped", {"reason": "自然人已在库且无变化", "id": row["id"]}

        try:
            cur.execute(
                "INSERT INTO `%s` (company_name, credit_code, tax_no, legal_person, "
                "contact_phone, contact_address, liaison_staff, staff_mobile, source, "
                "invoice_json, instance_code) VALUES (%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s,%%s)" % t,
                (person["name"], person["id_card"], person["id_card"], "",
                 person["phone"], person["address"], person["liaison"],
                 person["staff_mobile"],
                 "自然人信息录入申请表单", invoice_json, instance_code),
            )
            return "inserted", {"id": cur.lastrowid}
        except Exception as e:  # Duplicate entry 兜底
            if "Duplicate entry" in str(e):
                return "failed", {"error": "唯一键冲突: %s" % e}
            raise


# --------------------------------------------------------------------------- 子命令


def _ms_days_ago(days):
    return int((time.time() - days * 86400) * 1000)


def cmd_check(cfg):
    ok = True
    print("== check 自然人审批采集 ==")
    try:
        token = tenant_token(cfg)
        print("[OK] 飞书 token")
    except Exception as e:
        ok = False
        print("[FAIL] 飞书 token: %s" % e)
        token = None

    if token:
        try:
            codes = query_instance_codes(
                token, cfg["approval_code"], _ms_days_ago(cfg["window_days"]),
                int(time.time() * 1000))
            print("[OK] approval_code 可用,近 %d 天已通过(APPROVED)实例 %d 条"
                  % (cfg["window_days"], len(codes)))
        except Exception as e:
            ok = False
            print("[FAIL] approval_code: %s" % e)

    try:
        conn = db_conn(cfg)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.TABLES "
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                        (cfg["db_name"], cfg["table"]))
            if not cur.fetchone()["n"]:
                raise RuntimeError("表 %s 不存在" % cfg["table"])
        print("[OK] MySQL 连通,表 %s 存在" % cfg["table"])
        added = ensure_columns(cfg, conn)
        print("[OK] 表结构%s" % ("(本次补加: %s)" % ",".join(added) if added else "完整"))
        conn.close()
    except Exception as e:
        ok = False
        print("[FAIL] MySQL: %s" % e)

    print("check 结果: %s" % ("通过" if ok else "未通过"))
    return 0 if ok else 1


def _describe_instance(token, cfg, code):
    detail = get_instance(token, code)
    by_name = parse_form(detail)
    person = extract_person(cfg, by_name)
    snapshot = {
        "instance_code": code,
        "status": detail.get("status"),
        "user_id": detail.get("user_id"),
        "start_time": detail.get("start_time"),
        "end_time": detail.get("end_time"),
    }
    return detail, by_name, person, snapshot


def cmd_test_instance(cfg, code):
    token = tenant_token(cfg)
    detail, by_name, person, snapshot = _describe_instance(token, cfg, code)
    person = enrich_liaison(token, person)
    print("== 实例快照 ==")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print("== 原始 form(核对控件名用) ==")
    print(json.dumps(list(by_name.values()), ensure_ascii=False, indent=2))
    print("== 按映射表解析 ==")
    print(json.dumps({k: person[k] for k in
                      ("name", "id_card", "phone", "address", "liaison", "staff_mobile")},
                     ensure_ascii=False, indent=2))

    # 干跑分支判断(只读不写)
    if person["missing_required"]:
        print("[预判] failed: 必填缺失 %s(NOT NULL 列,无法入库)" % person["missing_required"])
        return 0
    if person["id_card_bad"]:
        print("[提醒] 身份证号格式异常(值=%r)——按现行口径仍会入库,请人工核对"
              % person["id_card_bad_value"])
    conn = db_conn(cfg)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM `%s` WHERE instance_code=%%s" % cfg["table"], (code,))
        if cur.fetchone():
            print("[预判] skipped: 已采集")
            conn.close()
            return 0
        cur.execute("SELECT id, contact_phone, contact_address, liaison_staff, staff_mobile "
                    "FROM `%s` WHERE credit_code=%%s LIMIT 1" % cfg["table"],
                    (person["id_card"],))
        row = cur.fetchone()
    conn.close()
    if row:
        diffs = [c for c, n in (("contact_phone", person["phone"]),
                                ("contact_address", person["address"]),
                                ("liaison_staff", person["liaison"]),
                                ("staff_mobile", person["staff_mobile"]))
                 if _norm(row.get(c)) != _norm(n)]
        print("[预判] %s: 自然人已在库(id=%s)%s" % (
            "confirm(有差异待人工确认)" if diffs else "skipped(无变化)", row["id"],
            " 差异字段: %s" % diffs if diffs else ""))
    else:
        print("[预判] inserted: 新自然人,将 INSERT(含 invoice_json 快照)")
    return 0


def cmd_poll(cfg):
    token = tenant_token(cfg)
    conn = db_conn(cfg)
    added = ensure_columns(cfg, conn)
    if added:
        print("[表结构] 本次补加: %s" % ",".join(added))

    codes = query_instance_codes(token, cfg["approval_code"],
                                 _ms_days_ago(cfg["window_days"]), int(time.time() * 1000))
    print("[扫描] 近 %d 天实例 %d 条" % (cfg["window_days"], len(codes)))

    stats = {"inserted": 0, "skipped": 0, "confirm": 0, "warn": 0, "failed": 0}
    confirm_details, failed_details, warn_details, liaison_raw = [], [], [], []

    with conn.cursor() as cur:
        for code in codes:
            try:
                cur.execute("SELECT id FROM `%s` WHERE instance_code=%%s" % cfg["table"], (code,))
                if cur.fetchone():  # 已采集:不调详情接口
                    stats["skipped"] += 1
                    continue

                detail = get_instance(token, code)
                by_name = parse_form(detail)
                person = extract_person(cfg, by_name)
                person = enrich_liaison(token, person)
                if person["missing_required"]:
                    stats["failed"] += 1
                    failed_details.append({"instance_code": code,
                                           "error": "必填缺失: %s" % person["missing_required"]})
                    continue
                if person["id_card_bad"]:
                    # 2026-09-03 口径:不做硬拦截,审批通过即入库,仅输出提醒
                    stats["warn"] += 1
                    warn_details.append({"instance_code": code,
                                         "id_card": person["id_card_bad_value"],
                                         "hint": "身份证号格式异常,已入库,请人工核对"})
                if by_name.get(cfg["field_liaison"]) and not _is_plain_text(
                        by_name[cfg["field_liaison"]]):
                    liaison_raw.append(code)  # 人员控件疑似未解析成可读名

                action, info = classify_and_write(
                    cfg, conn, person, code,
                    json.dumps(detail, ensure_ascii=False))
                stats[action] += 1
                if action == "confirm":
                    confirm_details.append({"instance_code": code, **info})
                elif action == "failed":
                    failed_details.append({"instance_code": code, **info})
            except Exception as e:
                stats["failed"] += 1
                failed_details.append({"instance_code": code,
                                       "error": "%s: %s" % (type(e).__name__, e)})
    conn.close()

    print("== 统计 ==")
    print("新增 %d / 跳过 %d / 待人工确认 %d / 提醒 %d / 失败 %d" % (
        stats["inserted"], stats["skipped"], stats["confirm"], stats["warn"], stats["failed"]))
    if warn_details:
        print("== 提醒(已入库,请人工核对) ==")
        print(json.dumps(warn_details, ensure_ascii=False, indent=2))
    if confirm_details:
        print("== 待人工确认(未写库) ==")
        print(json.dumps(confirm_details, ensure_ascii=False, indent=2))
    if failed_details:
        print("== 失败明细 ==")
        print(json.dumps(failed_details, ensure_ascii=False, indent=2))
    if liaison_raw:
        print("[提示] 以下实例的『我方对接人』疑似人员控件原始值,未能解析出姓名: %s"
              % ",".join(liaison_raw))
    print(json.dumps({"ok": not failed_details, "stats": stats}, ensure_ascii=False))
    return 0 if not failed_details else 1


def _is_plain_text(item):
    v = item.get("value")
    return isinstance(v, str) or v is None


# --------------------------------------------------------------------------- 入口


def main():
    ap = argparse.ArgumentParser(description="自然人信息录入申请审批采集")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("poll")
    ti = sub.add_parser("test-instance")
    ti.add_argument("instance_code")
    args = ap.parse_args()

    cfg = load_cfg()
    missing = [k for k in ("app_id", "app_secret", "db_host", "db_user",
                           "db_password", "db_name") if not cfg[k]]
    if missing:
        print("[FAIL] .env 缺配置: %s(参考 .env.example)" % missing)
        return 2

    try:
        if args.cmd == "check":
            return cmd_check(cfg)
        if args.cmd == "poll":
            return cmd_poll(cfg)
        if args.cmd == "test-instance":
            return cmd_test_instance(cfg, args.instance_code)
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
