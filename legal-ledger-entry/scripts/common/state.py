# -*- coding: utf-8 -*-
"""按 instance_code 记住已建的台账行。"""
import json
import os
import time

from common.paths import state_path
from ledger.errors import LedgerStop


class StateStore:
    def __init__(self, path=None):
        self.path = path or state_path()
        self._data = self._read()

    def load(self):
        return json.loads(json.dumps(self._data, ensure_ascii=False))

    def save(self, data=None):
        if data is not None:
            if not isinstance(data, dict):
                raise LedgerStop("状态文件内容不是对象，已停住")
            self._data = data
        self._write()

    def get(self, instance_code):
        record = self._data.get(instance_code)
        if not isinstance(record, dict):
            return None
        return dict(record)

    def mark(self, instance_code, table_id, record_id, kind, contract_no=None, updated_at=None):
        record = {
            "table_id": table_id,
            "record_id": record_id,
            "kind": kind,
            "updated_at": updated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if contract_no:
            record["contract_no"] = contract_no
        self._data[instance_code] = record
        self._write()
        return dict(record)

    def update(self, instance_code, **fields):
        current = self.get(instance_code)
        if current is None:
            raise LedgerStop("本地没有这张审批的入账记录，已停住")
        for key, value in fields.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        current["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._data[instance_code] = current
        self._write()
        return dict(current)

    def _read(self):
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                obj = json.load(fh)
        except json.JSONDecodeError:
            raise LedgerStop("本地状态文件损坏，已停住，请检查 data/state.json")
        except OSError:
            raise LedgerStop("本地状态文件读不了，已停住")
        return obj if isinstance(obj, dict) else {}

    def _write(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
