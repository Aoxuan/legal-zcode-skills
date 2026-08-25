# -*- coding: utf-8 -*-
"""审计留痕:每个外部调用追加一行 JSON 到 audit.jsonl。

字段含:ts, trace_id(贯穿一次合同流程), action, actor, object(合同流水号/企业名),
api_code, cost, duration_ms, result(成功/失败摘要), snapshot_path。
P0 写本地文件;阶段2 起按 Hermes 平台审计规范接入。

用法:
  - log(action, **fields):直接写一条
  - trace_context(trace_id, actor, contract_id):上下文管理器,自动填充公共字段
  - audited(action):方法装饰器,自动记录调用前后(含 duration_ms/result)
"""
from __future__ import annotations
import functools
import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Optional

from common.paths import audit_log

_LOG = audit_log()

# 当前上下文(由 trace_context 设置,装饰器/直接 log 读取)
_ctx = {"trace_id": None, "actor": None, "contract_id": None}


@contextmanager
def trace_context(trace_id: Optional[str] = None, actor: str = "",
                  contract_id: str = ""):
    """为一次合同流程设置公共审计字段。trace_id 缺省自动生成。"""
    global _ctx
    old = dict(_ctx)
    _ctx = {"trace_id": trace_id or _ctx.get("trace_id") or uuid.uuid4().hex,
            "actor": actor or _ctx.get("actor") or "",
            "contract_id": contract_id or _ctx.get("contract_id") or ""}
    try:
        yield _ctx["trace_id"]
    finally:
        _ctx = old


def log(action: str, **fields):
    record = {
        "ts": int(time.time()),
        "trace_id": fields.pop("trace_id", _ctx.get("trace_id")),
        "action": action,
        "actor": fields.pop("actor", _ctx.get("actor") or ""),
        "object": fields.pop("object", _ctx.get("contract_id") or ""),
    }
    record.update(fields)
    with open(_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def audited(action: str, *, api_code: Optional[int] = None, cost: float = 0.0):
    """方法装饰器:记录调用前后,含 duration_ms 与 result 摘要。

    被装饰方法的调用参数中若含 actor/object/contract_id 关键字,会自动提取;
    返回值若为 dict 且含 cost 字段,会覆盖装饰器 cost。
    异常时记录 result=error 并重新抛出。
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            start = time.time()
            obj = (kwargs.get("contract_id") or kwargs.get("company")
                   or kwargs.get("company_name") or kwargs.get("search_key")
                   or _ctx.get("contract_id") or "")
            actor = kwargs.get("actor", _ctx.get("actor") or "")
            contract_id = kwargs.get("contract_id", _ctx.get("contract_id") or "")
            try:
                ret = fn(self, *args, **kwargs)
                dur = int((time.time() - start) * 1000)
                c = cost
                if isinstance(ret, dict) and isinstance(ret.get("cost"), (int, float)):
                    c = ret["cost"]
                log(action, actor=actor, object=obj, contract_id=contract_id,
                    api_code=api_code, cost=c, duration_ms=dur, result="ok")
                return ret
            except Exception as e:
                dur = int((time.time() - start) * 1000)
                log(action, actor=actor, object=obj, contract_id=contract_id,
                    api_code=api_code, cost=cost, duration_ms=dur,
                    result="error:%s:%s" % (type(e).__name__, str(e)))
                raise
        return wrapper
    return deco
