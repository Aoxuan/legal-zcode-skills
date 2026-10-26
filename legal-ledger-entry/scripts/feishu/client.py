# -*- coding: utf-8 -*-
"""飞书审批读取、多维表格写入，以及使用人身份的待办同意。不发消息、不导出 PDF。"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from ledger.errors import LedgerStop

BASE = "https://open.feishu.cn/open-apis"
_CN = timezone(timedelta(hours=8))


class FeishuApiError(RuntimeError):
    pass


def _raise_if_error(action, data):
    if not isinstance(data, dict):
        raise FeishuApiError("%s失败" % action)
    if data.get("code") not in (0, None):
        raise FeishuApiError("%s失败（code=%s）" % (action, data.get("code")))
    return data


def parse_form(form):
    if isinstance(form, list):
        return form
    if isinstance(form, dict):
        return [form]
    if isinstance(form, str):
        text = form.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise LedgerStop("审批表单无法解析，已停住")
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    return []


def serial_window(serial):
    """单号前 8 位是日期。查询窗盖住这一天前后，且不超过 30 天。"""
    serial = (serial or "").strip()
    if not re.fullmatch(r"\d{12,}", serial):
        raise LedgerStop("审批单号格式无法识别，已停住")
    try:
        day = datetime.strptime(serial[:8], "%Y%m%d").replace(tzinfo=_CN)
    except ValueError:
        raise LedgerStop("审批单号里的日期无法识别，已停住")
    start = day - timedelta(days=1)
    end = day + timedelta(days=2)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


class FeishuClient:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expire = 0

    def tenant_access_token(self):
        now = time.time()
        if self._token and now < self._token_expire:
            return self._token
        url = BASE + "/auth/v3/tenant_access_token/internal"
        try:
            resp = requests.post(
                url, json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException:
            raise FeishuApiError("取访问令牌失败") from None
        token = data.get("tenant_access_token") if isinstance(data, dict) else None
        if not token:
            raise FeishuApiError("取访问令牌失败")
        self._token = token
        expire = int(data.get("expire") or 7200)
        self._token_expire = now + max(expire - 60, 60)
        return self._token

    def query_by_serial(self, approval_code, serial_number):
        """POST /approval/v4/instances/query。不传 instance_status，返回全部状态，再按 serial_id 过滤。"""
        start_ms, end_ms = serial_window(serial_number)
        wanted = str(serial_number).strip()
        payload = {
            "approval_code": approval_code,
            "instance_start_time_from": start_ms,
            "instance_start_time_to": end_ms,
        }
        found = []
        seen = set()
        page_token = ""
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._json(
                "查询审批实例", "POST",
                BASE + "/approval/v4/instances/query",
                params=params, json=payload,
            )
            body = data.get("data") or {}
            for item in body.get("instance_list") or []:
                inst = item.get("instance") if isinstance(item, dict) else None
                if not isinstance(inst, dict):
                    continue
                serial = str(inst.get("serial_id") or inst.get("serial_number") or "")
                code = inst.get("code") or ""
                if serial == wanted and code and code not in seen:
                    seen.add(code)
                    found.append({
                        "instance_code": code,
                        "serial_number": serial,
                        "status": inst.get("status") or "",
                    })
            if not body.get("has_more"):
                break
            page_token = body.get("page_token") or ""
            if not page_token:
                break
        return found

    def get_instance(self, instance_code):
        data = self._json(
            "读取审批实例", "GET",
            BASE + "/approval/v4/instances/%s" % instance_code,
        )
        detail = data.get("data") or {}
        detail["form"] = parse_form(detail.get("form"))
        return detail

    def download(self, url):
        clean = (url or "").replace("\\u0026", "&")
        try:
            resp = requests.get(clean, timeout=120)
            resp.raise_for_status()
        except requests.RequestException:
            raise LedgerStop("合同附件下载失败，已停住") from None
        return resp.content

    def bitable_list_fields(self, app_token, table_id):
        fields = []
        page_token = ""
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._json(
                "读取台账字段", "GET",
                BASE + "/bitable/v1/apps/%s/tables/%s/fields" % (app_token, table_id),
                params=params,
            )
            body = data.get("data") or {}
            fields.extend(body.get("items") or [])
            if not body.get("has_more"):
                return fields
            page_token = body.get("page_token") or ""
            if not page_token:
                return fields

    def bitable_list_records(self, app_token, table_id):
        records = []
        page_token = ""
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._json(
                "读取台账记录", "GET",
                BASE + "/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id),
                params=params,
            )
            body = data.get("data") or {}
            for item in body.get("items") or []:
                if isinstance(item, dict) and item.get("record_id"):
                    records.append({
                        "record_id": item.get("record_id"),
                        "fields": item.get("fields") or {},
                    })
            if not body.get("has_more"):
                return records
            page_token = body.get("page_token") or ""
            if not page_token:
                return records

    def bitable_search(self, app_token, table_id, field_name, value, operator="is"):
        records = []
        page_token = ""
        body_json = {
            "filter": {
                "conjunction": "and",
                "conditions": [{
                    "field_name": field_name,
                    "operator": operator,
                    "value": [value],
                }],
            },
        }
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            data = self._json(
                "查询台账记录", "POST",
                BASE + "/bitable/v1/apps/%s/tables/%s/records/search" % (app_token, table_id),
                params=params, json=body_json,
            )
            body = data.get("data") or {}
            for item in body.get("items") or []:
                if isinstance(item, dict) and item.get("record_id"):
                    records.append({
                        "record_id": item.get("record_id"),
                        "fields": item.get("fields") or {},
                    })
            if not body.get("has_more"):
                return records
            page_token = body.get("page_token") or ""
            if not page_token:
                return records

    def bitable_create_record(self, app_token, table_id, fields):
        data = self._json(
            "新增台账记录", "POST",
            BASE + "/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id),
            json={"fields": fields},
        )
        record = (data.get("data") or {}).get("record") or {}
        return record.get("record_id"), record.get("fields") or {}

    def lookup_user_id(self, mobile="", email=""):
        """POST /contact/v3/users/batch_get_id。返回 user_id，不返回手机号或邮箱。"""
        mobile = (mobile or "").strip()
        email = (email or "").strip()
        if mobile:
            body = {"mobiles": [mobile]}
        elif email:
            body = {"emails": [email]}
        else:
            raise LedgerStop("请提供手机号或邮箱")
        data = self._json(
            "查询使用人", "POST",
            BASE + "/contact/v3/users/batch_get_id",
            params={"user_id_type": "user_id"},
            json=body,
        )
        for user in (data.get("data") or {}).get("user_list") or []:
            if not isinstance(user, dict):
                continue
            user_id = str(user.get("user_id") or "").strip()
            if user_id:
                return user_id
        raise LedgerStop("查不到该手机号或邮箱对应的飞书用户，请确认通讯录权限已发版且该账号在应用可见范围内")

    def approve_task(self, approval_code, instance_code, task_id, user_id, comment):
        """POST /approval/v4/tasks/approve。只提交审批意见，不改表单控件。"""
        payload = {
            "approval_code": approval_code,
            "instance_code": instance_code,
            "task_id": task_id,
            "user_id": user_id,
            "comment": comment,
        }
        data = self._json(
            "同意审批任务", "POST",
            BASE + "/approval/v4/tasks/approve",
            params={"user_id_type": "user_id"},
            json=payload,
        )
        return data.get("data") or {}

    def bitable_get_record(self, app_token, table_id, record_id):
        data = self._json(
            "回读台账记录", "GET",
            BASE + "/bitable/v1/apps/%s/tables/%s/records/%s" % (app_token, table_id, record_id),
        )
        record = (data.get("data") or {}).get("record") or {}
        return record.get("fields") or {}

    def _auth(self):
        return {"Authorization": "Bearer " + self.tenant_access_token()}

    def _json(self, action, method, url, **kwargs):
        try:
            resp = requests.request(method, url, headers=self._auth(), timeout=20, **kwargs)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException:
            raise FeishuApiError("%s失败" % action) from None
        return _raise_if_error(action, payload)
