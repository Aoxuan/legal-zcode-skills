# -*- coding: utf-8 -*-
"""企业查询结果存储:每次真查企查查插入一版(query_date),默认读该企业最新一版。

表名默认 legal_company_query,避免占用业务库已有表。凭据只从 .env 读,不写死在代码里。
"""
from __future__ import annotations
import json
from typing import List, Optional

from qcc.aggregator import Candidate, CompanyProfile

# 企业主数据维度表:身份查找(名称/信用代码/税号)本地精确匹配用。
DIM_DDL = """
CREATE TABLE IF NOT EXISTS `dim_company` (
  id BIGINT NOT NULL AUTO_INCREMENT,
  company_name VARCHAR(255) NOT NULL,
  credit_code VARCHAR(32) NOT NULL,
  tax_no VARCHAR(32) NOT NULL,
  legal_person VARCHAR(128) NOT NULL DEFAULT '',
  contact_name VARCHAR(128) NULL,
  contact_phone VARCHAR(32) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_credit_code (credit_code),
  UNIQUE KEY uk_tax_no (tax_no),
  KEY idx_company_name (company_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL = """
CREATE TABLE IF NOT EXISTS `{table}` (
  id BIGINT NOT NULL AUTO_INCREMENT,
  company_id BIGINT NULL,
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
  KEY idx_name (company_name),
  KEY idx_company_id (company_id),
  CONSTRAINT fk_query_company FOREIGN KEY (company_id) REFERENCES dim_company(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _tax_no_from_payloads(payloads, credit_code=""):
    """从 2006 原始响应取 TaxNo;取不到回退 credit_code(占位,满足 NOT NULL)。"""
    try:
        data = (payloads or {}).get(2006) or {}
        tax = ((data.get("Result") or {}).get("Data") or {}).get("TaxNo")
        if tax and str(tax).strip():
            return str(tax).strip()
    except (AttributeError, TypeError):
        pass
    return credit_code or ""


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

    def find_exact(self, keyword: str) -> Optional[Candidate]:
        return None

    def get_latest(self, credit_code: str = "", name: str = "") -> Optional[CompanyProfile]:
        return None

    def insert(self, profile: CompanyProfile, actor: str = "", contract_id: str = "") -> None:
        return None


class MemoryCompanyStore:
    """进程内存储,单测用。"""

    def __init__(self):
        self.rows: List[CompanyProfile] = []
        self.companies: List[dict] = []  # 维度表: {id,name,credit_code,tax_no,legal_person}
        self._next_id = 1

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

    def find_exact(self, keyword: str) -> Optional[Candidate]:
        kw = (keyword or "").strip()
        if not kw:
            return None
        hits = []
        for c in self.companies:
            if c["company_name"] == kw or c["credit_code"] == kw or c["tax_no"] == kw:
                hits.append(c)
        if len(hits) != 1:
            return None
        c = hits[0]
        return Candidate(name=c["company_name"], credit_code=c["credit_code"],
                         legal_person=c["legal_person"], status="")

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

    def _upsert_company(self, profile: CompanyProfile) -> Optional[int]:
        cc = (profile.credit_code or "").strip()
        if not cc:
            return None
        tax = _tax_no_from_payloads(profile.payloads, cc)
        for c in self.companies:
            if c["credit_code"] == cc:
                c["company_name"] = profile.name or c["company_name"]
                c["legal_person"] = profile.legal_person or c["legal_person"]
                if tax:
                    c["tax_no"] = tax
                return c["id"]
        cid = self._next_id
        self._next_id += 1
        self.companies.append({
            "id": cid, "company_name": profile.name or "", "credit_code": cc,
            "tax_no": tax, "legal_person": profile.legal_person or "",
        })
        return cid

    def insert(self, profile: CompanyProfile, actor: str = "", contract_id: str = "") -> None:
        company_id = self._upsert_company(profile)
        profile.company_id = company_id  # type: ignore[attr-defined]
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
                cur.execute(DIM_DDL)
                cur.execute(DDL.format(table=self.table.replace("`", "")))
            conn.commit()
        finally:
            conn.close()

    def find_exact(self, keyword: str) -> Optional[Candidate]:
        """维度表精确匹配:名称/信用代码/税号 任一相等。恰好 1 条返回候选,否则 None。"""
        kw = (keyword or "").strip()
        if not kw:
            return None
        sql = (
            "SELECT company_name, credit_code, legal_person FROM `dim_company` "
            "WHERE company_name=%s OR credit_code=%s OR tax_no=%s"
        )
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (kw, kw, kw))
                rows = cur.fetchall() or []
        finally:
            conn.close()
        if len(rows) != 1:
            return None
        r = rows[0]
        return Candidate(name=r["company_name"], credit_code=r["credit_code"],
                         legal_person=r["legal_person"], status="")

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

    def _upsert_company(self, profile: CompanyProfile) -> Optional[int]:
        """按 credit_code upsert 维度表,返回 company_id。信用代码为空返回 None。"""
        cc = (profile.credit_code or "").strip()
        if not cc:
            return None
        tax = _tax_no_from_payloads(profile.payloads, cc)
        upsert = (
            "INSERT INTO `dim_company` (company_name, credit_code, tax_no, legal_person) "
            "VALUES (%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE "
            "company_name=VALUES(company_name), legal_person=VALUES(legal_person), "
            "tax_no=IF(VALUES(tax_no)<>'',VALUES(tax_no),dim_company.tax_no)"
        )
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(upsert, (profile.name or "", cc, tax, profile.legal_person or ""))
                company_id = cur.lastrowid
                if not company_id:  # ON DUPLICATE 命中更新时 lastrowid=0,回查
                    cur.execute("SELECT id FROM `dim_company` WHERE credit_code=%s", (cc,))
                    row = cur.fetchone()
                    company_id = row["id"] if row else None
            conn.commit()
        finally:
            conn.close()
        return company_id

    def insert(self, profile: CompanyProfile, actor: str = "", contract_id: str = "") -> None:
        company_id = self._upsert_company(profile)
        payloads = {str(k): v for k, v in (profile.payloads or {}).items()}
        sql = (
            "INSERT INTO `{table}` (company_id, credit_code, company_name, legal_person, status, query_date, "
            "risk_level, three_element_ok, hits_json, payloads_json, called_apis, cost, source, "
            "actor, contract_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        ).format(table=self.table.replace("`", ""))
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    company_id,
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
    """有 MYSQL_HOST 则连库建表;失败则返回 None 让调用方回退。"""
    from common.config import get_mysql
    cfg = get_mysql()
    if not cfg:
        return None
    return MysqlCompanyStore(cfg)
