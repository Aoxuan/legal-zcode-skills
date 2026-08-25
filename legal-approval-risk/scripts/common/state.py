# -*- coding: utf-8 -*-
"""已处理审批实例的幂等状态(本地 JSON)。

结构: {instance_code: {processed_at, risk_level, comment_id, company}}
poll 只处理本文件中不存在的 instance_code;评论成功后再写入,避免重复评论/计费。
"""
from __future__ import annotations

import json
import os
import time

from common.paths import state_path


class StateStore:
    def __init__(self, path=None):
        self.path = path or state_path()
        self._data = self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                obj = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return obj if isinstance(obj, dict) else {}

    def _save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def is_processed(self, instance_code):
        return bool(instance_code) and instance_code in self._data

    def get(self, instance_code):
        return self._data.get(instance_code)

    def mark(self, instance_code, risk_level="", comment_id="", company=""):
        self._data[instance_code] = {
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "risk_level": risk_level or "",
            "comment_id": comment_id or "",
            "company": company or "",
        }
        self._save()
        return self._data[instance_code]
