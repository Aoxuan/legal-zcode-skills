# -*- coding: utf-8 -*-
"""统一快照落盘契约(模块一,法务 2.7 已定"永久保存")。

快照文件主体 = 企查查原始响应({Status,Message,OrderNumber,Result,...});
证据链 meta 追加到 snapshots/_manifest.jsonl。

文件名与 01-poc snapshot.save 一致: qcc_{apiCode}_{企业名}_{YYYYmmdd_HHMMSS}.json
JSON 落在 snapshots/json/,Excel 与 manifest 留在 snapshots/ 根。
"""
from __future__ import annotations
import json
import os
import time
from typing import Any, Optional

from common import paths

_DEFAULT_DIR = paths.snapshots_dir()


class StorageBackend:
    """快照存储后端抽象。P0 用 LocalFileBackend。"""

    def save_response(self, name: str, response: Any) -> str:
        raise NotImplementedError

    def append_manifest(self, entry: dict) -> None:
        raise NotImplementedError


class LocalFileBackend(StorageBackend):
    def __init__(self, directory: str = _DEFAULT_DIR):
        self.directory = directory
        self.manifest_path = os.path.join(directory, "_manifest.jsonl")

    def save_response(self, name: str, response: Any) -> str:
        json_dir = os.path.join(self.directory, "json")
        os.makedirs(json_dir, exist_ok=True)
        path = os.path.join(json_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        return path

    def append_manifest(self, entry: dict) -> None:
        os.makedirs(self.directory, exist_ok=True)
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


_backend: StorageBackend = LocalFileBackend()


def set_backend(backend: StorageBackend):
    """替换存储后端(如 ObjectStorageBackend)。"""
    global _backend
    _backend = backend


def save(api_code: int, company: str, request_params: dict, response: Any,
         actor: str = "", contract_id: str = "",
         grading: Optional[dict] = None) -> str:
    """保存一次企查查调用的完整快照。

    返回快照文件路径。命名: qcc_{apiCode}_{企业名}_{YYYYmmdd_HHMMSS}.json
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    safe = (company or "unknown").replace("/", "_")
    name = "qcc_%s_%s_%s.json" % (api_code, safe, ts)

    order_number = ""
    if isinstance(response, dict):
        order_number = response.get("OrderNumber", "") or ""

    from qcc.grading import RULES_VERSION  # 延迟导入避免循环
    rules_version = (grading or {}).get("snapshot", {}).get("rules_version", RULES_VERSION)

    path = _backend.save_response(name, response)
    _backend.append_manifest({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_code": api_code,
        "company": company,
        "request_params": request_params,
        "actor": actor,
        "contract_id": contract_id,
        "order_number": order_number,
        "snapshot_file": "json/" + name,
        "rules_version": rules_version,
        "grading": {
            "level": (grading or {}).get("level"),
            "hits": (grading or {}).get("hits", []),
        } if grading else None,
    })
    return path
