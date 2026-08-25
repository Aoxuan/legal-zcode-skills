# -*- coding: utf-8 -*-
"""飞书开放平台客户端(审批轮询 + 评论)。

开发源为 01-poc/feishu/client.py;本文件在其鉴权基础上增加 PENDING 查询、
实例详情、审批评论。端点见 01-poc/飞书OpenAPI对接速查.md。
"""
from __future__ import annotations

import json
import time

import requests

from common.audit import audited

BASE = "https://open.feishu.cn/open-apis"


class FeishuApiError(RuntimeError):
    def __init__(self, action, payload):
        self.action = action
        self.payload = payload
        RuntimeError.__init__(self, "飞书%s失败: %s" % (action, payload))


def _raise_if_error(action, data):
    if data.get("code") not in (0, None):
        raise FeishuApiError(action, data)
    return data


def parse_form(form):
    """把实例详情里的 form 解析为控件数组 [{id,name,type,value}, ...]。"""
    if isinstance(form, list):
        return form
    if isinstance(form, dict):
        return [form]
    if isinstance(form, str):
        text = form.strip()
        if not text:
            return []
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    return []


def _normalize_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.replace("\\u0026", "&").strip()
    if isinstance(value, list):
        if not value:
            return ""
        return _normalize_value(value[0])
    if isinstance(value, dict):
        for key in ("id", "text", "value", "name", "option_id"):
            if value.get(key) not in (None, ""):
                return _normalize_value(value[key])
        return ""
    return str(value).strip()


def _item_keys(item):
    keys = []
    for key in ("id", "custom_id", "name"):
        raw = item.get(key)
        if raw not in (None, ""):
            keys.append(str(raw).strip())
    return keys


def _split_combo(text):
    """外部选项展示文案: 名称｜信用代码｜法人。"""
    raw = (text or "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace("|", "｜").split("｜") if p.strip()]
    if len(parts) < 2:
        return None
    out = {"name": parts[0]}
    if len(parts) >= 2:
        out["credit_code"] = parts[1]
    if len(parts) >= 3:
        out["legal_person"] = parts[2]
    return out


def extract_company_fields(form_items, name_key, credit_key, legal_key):
    """从表单控件按 .env 标识取企业三要素。标识可对 id / custom_id / name。

    支持 radioV2 外部选项: option.key=信用代码, value/text=名称｜代码｜法人。
    返回 {name, credit_code, legal_person, missing}。缺字段列入 missing,不猜测。
    """
    wanted = {
        "name": (name_key or "").strip(),
        "credit_code": (credit_key or "").strip(),
        "legal_person": (legal_key or "").strip(),
    }
    found = {"name": "", "credit_code": "", "legal_person": ""}
    for item in form_items or []:
        if not isinstance(item, dict):
            continue
        keys = _item_keys(item)
        option = item.get("option") if isinstance(item.get("option"), dict) else {}
        raw_val = _normalize_value(item.get("value"))
        combo = _split_combo(raw_val) or _split_combo(_normalize_value(option.get("text")))
        for field, needle in wanted.items():
            if not needle or needle not in keys or found[field]:
                continue
            if field == "credit_code" and option.get("key"):
                found[field] = str(option.get("key") or "").strip()
            elif combo and combo.get(field):
                found[field] = combo[field]
            else:
                found[field] = raw_val
        if combo:
            for field in ("name", "credit_code", "legal_person"):
                if not found[field] and combo.get(field) and wanted[field] in keys:
                    found[field] = combo[field]
            # 三要素都挤在同一控件时,一次命中补齐空字段
            if any(wanted[f] in keys for f in wanted):
                for field in ("name", "credit_code", "legal_person"):
                    if not found[field] and combo.get(field):
                        found[field] = combo[field]
        if option.get("key") and not found["credit_code"] and any(wanted[f] in keys for f in wanted):
            found["credit_code"] = str(option.get("key") or "").strip()
    missing = [k for k, v in found.items() if not v]
    found["missing"] = missing
    return found


class FeishuClient:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expire = 0

    def tenant_access_token(self):
        """自建应用获取 tenant_access_token,用于应用身份调用 API。"""
        now = time.time()
        if self._token and now < self._token_expire:
            return self._token
        url = BASE + "/auth/v3/tenant_access_token/internal"
        resp = requests.post(
            url, json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuApiError("取token", data)
        self._token = token
        expire = int(data.get("expire") or 7200)
        self._token_expire = now + max(expire - 60, 60)
        return self._token

    def _auth(self):
        return {"Authorization": "Bearer " + self.tenant_access_token()}

    def _json(self, action, method, url, **kwargs):
        resp = requests.request(method, url, headers=self._auth(), timeout=20, **kwargs)
        resp.raise_for_status()
        return _raise_if_error(action, resp.json())

    @audited("feishu.query_pending_instances")
    def query_pending_instances(self, approval_code, start_ms, end_ms, page_size=100):
        """POST /approval/v4/instances/query, instance_status=PENDING。

        返回 instance_code 列表。时间窗须 <=30 天。可能有延迟。
        """
        codes = []
        page_token = ""
        while True:
            params = {"page_size": int(page_size)}
            if page_token:
                params["page_token"] = page_token
            payload = {
                "approval_code": approval_code,
                "instance_status": "PENDING",
                "instance_start_time_from": int(start_ms),
                "instance_start_time_to": int(end_ms),
            }
            data = self._json(
                "查询PENDING实例", "POST",
                BASE + "/approval/v4/instances/query",
                params=params, json=payload,
            )
            body = data.get("data") or {}
            for item in body.get("instance_list") or []:
                inst = item.get("instance") if isinstance(item, dict) else None
                if isinstance(inst, dict) and inst.get("code"):
                    codes.append(inst["code"])
                elif isinstance(item, dict) and item.get("instance_code"):
                    codes.append(item["instance_code"])
                elif isinstance(item, str):
                    codes.append(item)
            if not body.get("has_more"):
                break
            page_token = body.get("page_token") or ""
            if not page_token:
                break
        return codes

    @audited("feishu.get_instance")
    def get_instance(self, instance_code):
        """GET /approval/v4/instances/:instance_id,返回详情 dict(含解析后的 form 数组)。"""
        data = self._json(
            "获取审批实例", "GET",
            BASE + "/approval/v4/instances/%s" % instance_code,
        )
        detail = data.get("data") or {}
        detail["form_items"] = parse_form(detail.get("form"))
        return detail

    @audited("feishu.upload_approval_file")
    def upload_approval_file(self, file_path, file_name=None):
        """上传审批附件,返回 {url, fileSize, title, type, code}。url 约 12 小时有效。"""
        import os
        file_name = file_name or os.path.basename(file_path)
        url = "https://www.feishu.cn/approval/openapi/v2/file/upload"
        with open(file_path, "rb") as f:
            resp = requests.post(
                url, headers=self._auth(), timeout=60,
                data={"name": file_name, "type": "attachment"},
                files={"content": (file_name, f)},
            )
        resp.raise_for_status()
        data = _raise_if_error("上传审批附件", resp.json())
        body = data.get("data") or {}
        file_url = (body.get("url") or "").replace("\\u0026", "&")
        if not file_url:
            raise FeishuApiError("上传审批附件", data)
        return {
            "code": body.get("code") or "",
            "url": file_url,
            "fileSize": os.path.getsize(file_path),
            "title": file_name,
            "type": "attachment",
        }

    @audited("feishu.add_comment")
    def add_comment(self, instance_code, content, user_id="", user_id_type="open_id",
                    comment_id="", files=None):
        """POST /approval/v4/instances/:instance_id/comments。PENDING 态可评论。

        files 为 upload_approval_file 返回的附件列表。传入 comment_id 则编辑原评论。
        """
        params = {}
        if user_id:
            params["user_id"] = user_id
            params["user_id_type"] = user_id_type or "open_id"
        body = {"text": content}
        if files:
            body["files"] = files
        payload = {
            "content": json.dumps(body, ensure_ascii=False),
        }
        if comment_id:
            payload["comment_id"] = comment_id
        data = self._json(
            "写审批评论", "POST",
            BASE + "/approval/v4/instances/%s/comments" % instance_code,
            params=params, json=payload,
        )
        body = data.get("data") or {}
        cid = body.get("comment_id") or body.get("id") or ""
        return str(cid) if cid not in (None, 0, "0") else ""

    @audited("feishu.list_comments")
    def list_comments(self, instance_code, user_id="", user_id_type="open_id"):
        """GET /approval/v4/instances/:instance_id/comments。"""
        params = {}
        if user_id:
            params["user_id"] = user_id
            params["user_id_type"] = user_id_type or "open_id"
        data = self._json(
            "列出审批评论", "GET",
            BASE + "/approval/v4/instances/%s/comments" % instance_code,
            params=params or None,
        )
        body = data.get("data") or {}
        return body.get("comments") or body.get("items") or []
