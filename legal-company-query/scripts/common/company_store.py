# -*- coding: utf-8 -*-
"""企业查询结果存储:每次真查企查查插入一版(query_date),默认读该企业最新一版。

表名默认 legal_company_query,避免占用业务库已有表。凭据只从 .env 读,不写死在代码里。
"""
from __future__ import annotations
import json
from typing import List, Optional

from qcc.aggregator import CompanyProfile

DDL = """
CREATE TABLE IF NOT EXISTS `{table}` (
  id BIGINT NOT NULL AUTO_INCREMENT,
  credit_code VARCHAR(32) NOT NULL DEFAULT '',
  company_name VARCHAR(255) NOT NULL,
  legal_person VARCHAR(128) NOT NULL DEFAULT '',
  status VARCHAR(64) NOT NULL DEFAULT '',
  query_date DATETIME NOT NULL,
  risk_level VARCHAR(16) NOT NULL DEFAULT 'unknown',
  three_element_ok TINYINT NULL,
  hits_json LONGTEXT,
  payloads_json LONGTEXT,
  called_apis VARCHAR(128) NOT NULL DEFAULT '',
  cost DECIMAL(10,2) NOT NULL DEFAULT 0,
  source VARCHAR(16) NOT NULL DEFAULT 'qcc_live',
  actor VARCHAR(128) NOT NULL DEFAULT '',
  contract_id VARCHAR(128) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  KEY idx_credit_date (credit_code, query_date),
  KEY idx_name (company_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _three_to_sql(v):
    if v is True:
        return 1
    if v is False:
        return 0
    return None


def _three_from_sql(v):
    if v is None:
        return None
    return bool(int(v))


def _payloads_from_json(raw) -> dict:
    if not raw:
        return {}
    obj = json.loads(raw) if isinstance(raw, str) else raw
    out = {}
    for k, v in (obj or {}).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            out[k] = v
    return out


def profile_from_row(row: dict) -> CompanyProfile:
    hits = row.get("hits_json") or "[]"
    if isinstance(hits, str):
        hits = json.loads(hits or "[]")
    apis = row.get("called_apis") or ""
    called = [int(x) for x in apis.split(",") if str(x).strip().isdigit()]
    qd = row.get("query_date")
    query_date = qd.strftime("%Y-%m-%d %H:%M:%S") if hasattr(qd, "strftime") else str(qd or "")
    return CompanyProfile(
        name=row.get("company_name") or "",
        credit_code=row.get("credit_code") or "",
        legal_person=row.get("legal_person") or "",
        status=row.get("status") or "",
        three_element_ok=_three_from_sql(row.get("three_element_ok")),
        risk_level=row.get("risk_level") or "unknown",
        hits=hits or [],
        snapshot_files={},
        cost=float(row.get("cost") or 0),
        called_apis=called,
        payloads=_payloads_from_json(row.get("payloads_json")),
        query_date=query_date,
        source="cache",
    )


class NullCompanyStore:
    """未配库或测试默认:不读写,查询走企查查。"""

    def search(self, keyword: str, limit: int = 5) -> List[CompanyProfile]:
        return []

    def get_latest(self, credit_code: str = "", name: str = "") -> Optional[CompanyProfile]:
        return None

    def insert(self, profile: CompanyProfile, actor: str = "", contract_id: str = "") -> None:
        return None


class MemoryCompanyStore:
    """进程内存储,单测用。"""

    def __init__(self):
        self.rows: List[CompanyProfile] = []

    def search(self, keyword: str, limit: int = 5) -> List[CompanyProfile]:
        kw = (keyword or "").strip().lower()
        if not kw:
            return []
        latest = {}
        for p in self.rows:
            key = p.credit_code or p.name
            if kw in (p.name or "").lower() or kw == (p.credit_code or "").lower():
                prev = latest.get(key)
                if prev is None or (p.query_date or "") >= (prev.query_date or ""):
                    latest[key] = p
        out = list(latest.values())
        out.sort(key=lambda x: x.query_date or "", reverse=True)
        return out[:limit]

    def get_latest(self, credit_code: str = "", name: str = "") -> Optional[CompanyProfile]:
        hits = []
        for p in self.rows:
            if credit_code and p.credit_code == credit_code:
                hits.append(p)
            elif name and p.name == name:
                hits.append(p)
        if not hits:
            return None
        hits.sort(key=lambda x: x.query_date or "", reverse=True)
        p = hits[0]
        p.source = "cache"
        return p

    def insert(self, profile: CompanyProfile, actor: str = "", contract_id: str = "") -> None:
        self.rows.append(profile)


class MysqlCompanyStore:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.table = cfg.get("table") or "legal_company_query"
        self._ensure_table()

    def _connect(self):
        import pymysql
        return pymysql.connect(
            host=self.cfg["host"],
            port=int(self.cfg["port"]),
            user=self.cfg["user"],
            password=self.cfg["password"],
            database=self.cfg["database"],
            charset=self.cfg.get("charset") or "utf8mb4",
            connect_timeout=10,
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _ensure_table(self):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(DDL.format(table=self.table.replace("`", "")))
            conn.commit()
        finally:
            conn.close()

    def search(self, keyword: str, limit: int = 5) -> List[CompanyProfile]:
        kw = (keyword or "").strip()
        if not kw:
            return []
        like = "%" + kw + "%"
        sql = (
            "SELECT t.* FROM `{table}` t "
            "INNER JOIN ("
            "  SELECT IF(credit_code='', company_name, credit_code) AS gkey, MAX(query_date) AS max_d "
            "  FROM `{table}` "
            "  WHERE company_name LIKE %s OR credit_code LIKE %s "
            "  GROUP BY IF(credit_code='', company_name, credit_code)"
            ") x ON IF(t.credit_code='', t.company_name, t.credit_code)=x.gkey "
            "AND t.query_date=x.max_d "
            "ORDER BY t.query_date DESC LIMIT %s"
        ).format(table=self.table.replace("`", ""))
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (like, like, int(limit)))
                rows = cur.fetchall() or []
        finally:
            conn.close()
        return [profile_from_row(r) for r in rows]

    def get_latest(self, credit_code: str = "", name: str = "") -> Optional[CompanyProfile]:
        if credit_code:
            sql = ("SELECT * FROM `{table}` WHERE credit_code=%s "
                   "ORDER BY query_date DESC LIMIT 1").format(table=self.table.replace("`", ""))
            args = (credit_code,)
        elif name:
            sql = ("SELECT * FROM `{table}` WHERE company_name=%s "
                   "ORDER BY query_date DESC LIMIT 1").format(table=self.table.replace("`", ""))
            args = (name,)
        else:
            return None
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                row = cur.fetchone()
        finally:
            conn.close()
        return profile_from_row(row) if row else None

    def insert(self, profile: CompanyProfile, actor: str = "", contract_id: str = "") -> None:
        payloads = {str(k): v for k, v in (profile.payloads or {}).items()}
        sql = (
            "INSERT INTO `{table}` (credit_code, company_name, legal_person, status, query_date, "
            "risk_level, three_element_ok, hits_json, payloads_json, called_apis, cost, source, "
            "actor, contract_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        ).format(table=self.table.replace("`", ""))
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    profile.credit_code or "",
                    profile.name or "",
                    profile.legal_person or "",
                    profile.status or "",
                    profile.query_date,
                    profile.risk_level or "unknown",
                    _three_to_sql(profile.three_element_ok),
                    json.dumps(profile.hits or [], ensure_ascii=False),
                    json.dumps(payloads, ensure_ascii=False),
                    ",".join(str(a) for a in (profile.called_apis or [])),
                    float(profile.cost or 0),
                    "qcc_live",
                    actor or "",
                    contract_id or "",
                ))
            conn.commit()
        finally:
            conn.close()


def store_from_env():
    """MYSQL_* 齐备则连库建表;缺配返回 None(调用方必须判失败,不得静默回退)。"""
    from common.config import get_mysql
    cfg = get_mysql()
    if not cfg:
        return None
    return MysqlCompanyStore(cfg)
