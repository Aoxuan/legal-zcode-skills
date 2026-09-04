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


def _candidate_keys(company_keys):
    """归一化候选字段名: str 按中英文逗号切分, list/tuple 原样;去空、保序去重。

    str 入参的完整原名(去首尾空白)也作为候选——控件名本身可能含逗号,
    如「风险报告附件（请勿上传文件，由法务AI智能体审批后上传）」,不能只按切分片段匹配。
    """
    if company_keys is None:
        return []
    raw = company_keys if isinstance(company_keys, (list, tuple)) else [company_keys]
    keys = []
    for item in raw:
        for part in str(item).replace("，", ",").split(","):
            part = part.strip()
            if part and part not in keys:
                keys.append(part)
    if isinstance(company_keys, str):
        full = company_keys.strip()
        if full and full not in keys:
            keys.append(full)
    return keys


def extract_companies(form_items, company_keys):
    """按候选字段收集表单里已填的企业控件。company_keys 可为 str(逗号分隔)或 list。

    新表单里「签约企业名称」「达人公司名称」都是 radioV2 外部选项:
    option.key=信用代码, value/text=名称｜代码｜法人。标识可对 id / custom_id / name。
    控件存在但值为空(被显示条件隐藏/未填)视为未出现,不计入。
    不读「对方负责人」(联系人)。法人缺了由企查查 2006 回填,不算表单缺字段。
    返回 [{field, name, credit_code, legal_person, missing}, ...],按表单出现顺序;
    missing 只含 name / credit_code。
    """
    keys = _candidate_keys(company_keys)
    out = []
    if not keys:
        return out
    for item in form_items or []:
        if not isinstance(item, dict):
            continue
        item_keys = _item_keys(item)
        matched = next((k for k in keys if k in item_keys), None)
        if matched is None:
            continue
        option = item.get("option") if isinstance(item.get("option"), dict) else {}
        raw_val = _normalize_value(item.get("value"))
        if not raw_val and not _normalize_value(option.get("text")):
            continue
        found = {"field": matched, "name": "", "credit_code": "", "legal_person": ""}
        combo = _split_combo(raw_val) or _split_combo(_normalize_value(option.get("text")))
        if combo:
            found["name"] = combo.get("name") or ""
            found["credit_code"] = combo.get("credit_code") or ""
            found["legal_person"] = combo.get("legal_person") or ""
        if option.get("key") and not found["credit_code"]:
            found["credit_code"] = str(option.get("key") or "").strip()
        if not found["name"]:
            found["name"] = raw_val
        found["missing"] = [k for k in ("name", "credit_code") if not found[k]]
        out.append(found)
    return out


def extract_company_fields(form_items, company_key):
    """兼容旧调用:取第一个已填的企业控件,返回单条 {name, credit_code, legal_person, missing}。"""
    companies = extract_companies(form_items, company_key)
    if companies:
        return companies[0]
    return {"name": "", "credit_code": "", "legal_person": "", "missing": ["name", "credit_code"]}


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

    @audited("feishu.get_approval_form")
    def get_approval_form(self, approval_code):
        """GET /approval/v4/approvals/:approval_code,返回定义表单控件数组。

        实例详情里空值/后加的控件可能不出现;回填【企业风险评级】时用定义里的 id/type 兜底。
        """
        data = self._json(
            "获取审批定义", "GET",
            BASE + "/approval/v4/approvals/%s" % approval_code,
        )
        body = data.get("data") or {}
        form = body.get("form")
        if isinstance(form, dict):
            form = form.get("form_content") or form.get("widgets") or form
        return parse_form(form)

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

    @audited("feishu.query_user_tasks")
    def query_user_tasks(self, user_id, user_id_type="open_id", topic="1", page_size=100):
        """GET /approval/v4/tasks/query。返回该账号待办任务列表(topic=1 待办审批)。

        每项归一为 {task_id, approval_code, instance_code, status, support_api_operate}。
        需权限 approval:task:list_by_user(或 approval:approval:readonly),tenant_access_token 可用。
        """
        out = []
        page_token = ""
        while True:
            params = {
                "user_id": user_id,
                "user_id_type": user_id_type or "open_id",
                "topic": topic or "1",
                "page_size": int(page_size),
            }
            if page_token:
                params["page_token"] = page_token
            data = self._json(
                "查询用户待办任务", "GET",
                BASE + "/approval/v4/tasks/query",
                params=params,
            )
            body = data.get("data") or {}
            for t in body.get("tasks") or []:
                if not isinstance(t, dict):
                    continue
                # tasks/query 字段名与 search/approve 不同:
                # definition_code=审批定义, process_code=实例, task_id=任务
                out.append({
                    "task_id": str(t.get("task_id") or t.get("id") or ""),
                    "approval_code": t.get("definition_code") or t.get("approval_code") or "",
                    "instance_code": t.get("process_code") or t.get("instance_code") or "",
                    "status": t.get("status") or "",
                    "support_api_operate": t.get("support_api_operate"),
                    "title": t.get("title") or t.get("definition_name") or "",
                })
            if not body.get("has_more"):
                break
            page_token = body.get("page_token") or ""
            if not page_token:
                break
        return out

    @audited("feishu.approve_task")
    def approve_task(self, approval_code, instance_code, task_id, user_id,
                     form, user_id_type="open_id", comment=""):
        """POST /approval/v4/tasks/approve。同意任务并按 form 回填可编辑控件。

        form:控件值 JSON 数组(仅含本节点要回填的可编辑控件),如
          [{"id":"widget...","type":"input","value":"高"},
           {"id":"widget...","type":"attachmentV2","value":["<file_code>"]}]
        传字符串化 JSON。需权限 approval:task,tenant_access_token 可用;
        user_id 必须是该任务的审批人。
        """
        params = {"user_id_type": user_id_type or "open_id"}
        payload = {
            "approval_code": approval_code,
            "instance_code": instance_code,
            "task_id": task_id,
            "user_id": user_id,
        }
        if comment:
            payload["comment"] = comment
        if form:
            payload["form"] = json.dumps(form, ensure_ascii=False)
        data = self._json(
            "同意审批任务", "POST",
            BASE + "/approval/v4/tasks/approve",
            params=params, json=payload,
        )
        return data.get("data") or {}


def find_control(form_items, name_keys):
    """在表单控件里按名称(或 widget id)定位控件,返回首个命中项 {id, type, name, value}。

    name_keys 可为 str(逗号分隔)或 list;按顺序匹配控件的 name / id / custom_id。
    先精确(相等)后模糊:精确未命中时,取首个**完全包含候选**(控件名 ⊇ 候选,候选 >=4 字)
    的控件。用于控件名带说明后缀的场景,如「风险报告附件（请勿上传文件，由…上传）」
    改说明不影响定位;控件名比配置短或缺字(如「风险报告附」)不匹配,过短候选
    (如默认「附件」)不做模糊兜底,避免误中「合同附件」。
    """
    keys = _candidate_keys(name_keys)
    # 第一轮:精确相等
    for item in form_items or []:
        if not isinstance(item, dict):
            continue
        item_keys = _item_keys(item)
        matched = next((k for k in keys if k in item_keys), None)
        if matched is None:
            continue
        return {
            "id": str(item.get("id") or ""),
            "type": item.get("type") or "",
            "name": item.get("name") or matched,
            "value": item.get("value"),
        }
    # 第二轮:模糊——控件名完全包含候选(候选>=4字),不做反向包含
    for item in form_items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        matched = next((k for k in keys if k and len(k) >= 4 and k in name), None)
        if matched is None:
            continue
        return {
            "id": str(item.get("id") or ""),
            "type": item.get("type") or "",
            "name": item.get("name") or matched,
            "value": item.get("value"),
        }
    return None
