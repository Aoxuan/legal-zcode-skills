# -*- coding: utf-8 -*-
"""海外主体录入薄客户端:调服务端 preview/commit 端点(00-对齐/38)。

本脚本不持有数据库凭据、不执行 SQL;写入只经服务端接口(口径由服务端统一)。
用法(Windows 用 py / macOS 用 python3):
  py scripts/entry.py preview --name "My Commerce Co.,Ltd" [--address .. --phone .. --bank .. --account .. --liaison .. --mobile ..]
  py scripts/entry.py commit  --name "My Commerce Co.,Ltd" --actor "张三" [--同 preview 可选项]
.env(技能根): LEGAL_ENTRY_BASE_URL / LEGAL_OVERSEAS_KEY / LEGAL_ENTRY_ACTOR(默认录入人)
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv


def sign(key: str, ts: str, body: str) -> str:
    """与服务端一致的 HMAC 签名:hex(hmac_sha256(key, f"{ts}\n{body}"))。"""
    return hmac.new(key.encode(), ("%s\n%s" % (ts, body)).encode(),
                    hashlib.sha256).hexdigest()


def call(base_url: str, key: str, path: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json",
               "X-Timestamp": ts, "X-Signature": sign(key, ts, body)}
    resp = requests.post(base_url.rstrip("/") + path, data=body.encode("utf-8"),
                         headers=headers, timeout=20)
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {"ok": False, "error": "服务端返回非 JSON: %s" % resp.text[:200]}


def main():
    ap = argparse.ArgumentParser(description="海外主体录入(经鉴权接口)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("preview", "commit"):
        sp = sub.add_parser(name)
        sp.add_argument("--name", required=True, help="公司全称,须与合同签约抬头逐字一致")
        sp.add_argument("--actor", default=None, help="录入人姓名(commit 必填;缺省读 LEGAL_ENTRY_ACTOR)")
        sp.add_argument("--address", default=None, help="公司地址")
        sp.add_argument("--phone", default=None, help="公司电话(国际前缀原样保留)")
        sp.add_argument("--bank", default=None, help="开户行")
        sp.add_argument("--account", default=None, help="银行账号")
        sp.add_argument("--liaison", default=None, help="我方对接人姓名")
        sp.add_argument("--mobile", default=None, help="对接人手机号(自动去 +86)")
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    base = (os.getenv("LEGAL_ENTRY_BASE_URL") or "").strip()
    key = (os.getenv("LEGAL_OVERSEAS_KEY") or "").strip()
    if not base or not key:
        sys.exit("请先在技能根 .env 填 LEGAL_ENTRY_BASE_URL / LEGAL_OVERSEAS_KEY")

    payload = {"name": args.name,
               "actor": args.actor or (os.getenv("LEGAL_ENTRY_ACTOR") or "").strip(),
               "address": args.address or "", "phone": args.phone or "",
               "bank": args.bank or "", "account": args.account or "",
               "liaison": args.liaison or "", "mobile": args.mobile or "", "code": ""}
    if args.cmd == "commit" and not payload["actor"]:
        sys.exit("commit 必须提供 --actor(录入人姓名),供审计追溯")

    status, data = call(base, key, "/legal/overseas-company/" + args.cmd, payload)
    print(json.dumps(dict(data, http=status), ensure_ascii=False, indent=2))

    if args.cmd == "preview" and data.get("ok"):
        d = data["data"]
        print("\n===== 将写入 dim_company(1 行)=====")
        for k, v in d["dim"].items():
            print("  %-15s = %s" % (k, v))
        print("占位信用代码: %s(事实表 2 行,风险评级固定低风险,有效期至 2027-12-31)" % d["code"])
        if d["similar"]:
            print("\n[注意] 库中已有相似名称企业,请确认不是重复/变体:")
            for r in d["similar"]:
                print("  - %s  %s" % (r.get("company_name"), r.get("credit_code")))
        if d["conflict"]:
            print("\n[阻止] 库中已有同名/同码行,commit 将被拒绝;请先人工核对这些行:")
            for r in d.get("conflict_rows") or []:
                print("  ", r)
        else:
            print('\n确认无误后用 commit 子命令正式写库(带 --actor "录入人姓名")。')
    sys.exit(0 if data.get("ok") else 1)


if __name__ == "__main__":
    main()
