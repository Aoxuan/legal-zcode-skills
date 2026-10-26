# -*- coding: utf-8 -*-
"""我方主体映射、写入字段和 schema 校验。不调用创建接口。"""
import json
import os
import time

from ledger.errors import LedgerStop
from ledger.widgets import NO_FIELD, SUMMARY_FIELD, SUB_NO_FIELD

_DIR = os.path.dirname(os.path.abspath(__file__))


def maps_ok():
    return os.path.isfile(_map_path("entity_map.json")) and os.path.isfile(_map_path("category_map.json"))


def map_our_entity(full_name, for_sub=False):
    data = _load_json("entity_map.json")
    name = (full_name or "").strip()
    short = data.get(name)
    if name.startswith("_") or not isinstance(short, str) or not short.strip():
        shown = name or "（空）"
        raise LedgerStop("签约主体「%s」对不上台账我方主体，已停住。请法务补上选项后再跑" % shown)
    short = short.strip()
    if for_sub:
        override = data.get("_子表覆盖") or {}
        if isinstance(override, dict) and override.get(short):
            short = str(override[short]).strip()
    return short


def category_record_id(kind):
    data = _load_json("category_map.json")
    item = data.get(kind)
    if not isinstance(item, dict) or not item.get("record_id"):
        raise LedgerStop("分类「%s」没有对应的分类表记录，已停住" % kind)
    return item["record_id"]


def validate_schema(fields, entity_short, category_record_id=None, category_record_ids=None):
    options = _option_names(fields, "我方主体")
    if entity_short not in options:
        raise LedgerStop(
            "我方主体「%s」不在台账选项里，已停住。请法务在台账加上该选项后再跑" % entity_short
        )
    if category_record_id is not None:
        ids = set(category_record_ids or [])
        if category_record_id not in ids:
            raise LedgerStop(
                "分类记录 %s 不在分类表里，已停住。分类表随副本变化，请重拉后再跑" % category_record_id
            )


def build_main_fields(title, entity_short, ledger):
    return {
        "合同名称": title,
        "合同类型": "主合同",
        "我方主体": entity_short,
        "相对方主体（全称）": ledger.get("相对方主体（全称）") or "",
        "合同金额": ledger.get("合同金额") or "",
        SUMMARY_FIELD: ledger.get(SUMMARY_FIELD) or "",
    }


def build_sub_fields(entity_short, ledger, parent_record_id, category_id):
    return {
        "主合同": [parent_record_id],
        "子合同类型": [category_id],
        "我方主体": entity_short,
        "相对方主体（全称）": ledger.get("相对方主体（全称）") or "",
        "金额": ledger.get("合同金额") or "",
        "摘要（概括子合同内容）": ledger.get(SUMMARY_FIELD) or "",
    }


def read_contract_no(
    resp_fields, client, app_token, table_id, record_id,
    field_name=None, timeout=30, interval=2,
):
    """创建响应里常已有编号；没有则进程内轮询，默认 30 秒。"""
    field_name = field_name or NO_FIELD
    found = _coerce_no((resp_fields or {}).get(field_name))
    if found:
        return found
    deadline = time.time() + timeout
    while time.time() < deadline:
        fields = client.bitable_get_record(app_token, table_id, record_id) or {}
        found = _coerce_no(fields.get(field_name))
        if found:
            return found
        time.sleep(interval)
    label = "子合同编号" if field_name == SUB_NO_FIELD else "合同编号"
    raise LedgerStop("%s 30 秒内没有生成，已停住，请到台账人工核查" % label)


def _option_names(fields, field_name):
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if field.get("field_name") != field_name:
            continue
        options = ((field.get("property") or {}).get("options") or [])
        return [opt.get("name") for opt in options if isinstance(opt, dict) and opt.get("name")]
    return []


def _coerce_no(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _coerce_no(value.get("text") or value.get("value") or "")
    if isinstance(value, list):
        for item in value:
            found = _coerce_no(item)
            if found:
                return found
    return ""


def _map_path(name):
    return os.path.join(_DIR, name)


def _load_json(name):
    path = _map_path(name)
    if not os.path.isfile(path):
        raise LedgerStop("缺少 %s，已停住" % name)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
