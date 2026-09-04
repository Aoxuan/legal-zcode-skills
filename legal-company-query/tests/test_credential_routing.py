# -*- coding: utf-8 -*-
"""全部接口走同一套客户账号。离线,不触网。

不调用真实企查查接口:用 unittest.mock 替换 QccClient 的各接口方法,断言被调用对象。
"""
import os
import sys
import json
import unittest
from unittest import mock

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from qcc.aggregator import CompanyAggregator, find_exact_match, Candidate
from qcc.client import QccClient


def _make_client(app_key: str) -> QccClient:
    return QccClient(app_key, "secret-%s" % app_key)


class CredentialRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = _make_client("CUST")
        self.agg = CompanyAggregator(self.client)
        self.client._get = mock.MagicMock(return_value={"Status": "200", "Result": {}})

    def test_all_apis_use_single_client(self):
        for code in (886, 2006, 887, 888, 889, 739):
            with self.subTest(code=code):
                self.agg._call(code, search_key="某公司")
                self.client._get.assert_called()
                self.client._get.reset_mock()
        self.agg._call(856, credit_code="c", company_name="n", oper_name="o")
        self.client._get.assert_called()

    def test_search_candidates_uses_same_client(self):
        self.client._get.return_value = {
            "Status": "200",
            "Result": [{"Name": "某公司", "CreditCode": "X", "OperName": "张三", "Status": "存续"}],
        }
        out = self.agg.search_candidates("某公司")
        self.client._get.assert_called_once()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].name, "某公司")


class ExactMatchTests(unittest.TestCase):
    def test_exact_match_returns_matched_candidate(self):
        cs = [Candidate("甲公司", "A1"), Candidate("成都讯格得信息科技有限公司", "91510100MA61WKYX40")]
        m = find_exact_match(cs, "成都讯格得信息科技有限公司")
        self.assertIsNotNone(m)
        self.assertEqual(m.credit_code, "91510100MA61WKYX40")

    def test_exact_match_strips_whitespace(self):
        cs = [Candidate("甲公司", "A1")]
        self.assertIsNotNone(find_exact_match(cs, "  甲公司  "))

    def test_no_exact_match_returns_none(self):
        cs = [Candidate("甲公司", "A1"), Candidate("乙公司", "B1")]
        self.assertIsNone(find_exact_match(cs, "丙公司"))

    def test_empty_candidates_returns_none(self):
        self.assertIsNone(find_exact_match([], "甲公司"))


class ConfigQccTests(unittest.TestCase):
    def test_get_qcc_reads_customer_keys(self):
        env = {
            "QCC_APP_KEY": "CUST_KEY", "QCC_SECRET_KEY": "CUST_SECRET",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            from common.config import get_qcc
            creds = get_qcc()
            self.assertEqual(creds["app_key"], "CUST_KEY")
            self.assertEqual(creds["secret_key"], "CUST_SECRET")

    def test_get_qcc_raises_when_missing(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("QCC_APP_KEY", "QCC_SECRET_KEY")}
        with mock.patch.dict(os.environ, env, clear=True):
            from common.config import get_qcc
            with self.assertRaises(RuntimeError) as ctx:
                get_qcc()
            self.assertIn("QCC_APP_KEY", str(ctx.exception))


class CmdProfileCacheTests(unittest.TestCase):
    """profile 应先查 MySQL:命中返回 cache(不调企查查),未命中才调企查查并落库。"""

    def setUp(self):
        import io
        import query
        self.q = query
        self._stdout = sys.stdout
        sys.stdout = io.StringIO()

    def tearDown(self):
        sys.stdout = self._stdout

    def _run_profile(self, store, agg):
        with mock.patch.object(self.q, "_require_qcc", return_value=True), \
             mock.patch.object(self.q, "_require_mysql_store", return_value=store), \
             mock.patch.object(self.q, "default_aggregator", return_value=agg), \
             mock.patch.object(self.q, "save_snapshot", return_value="/tmp/dummy.json"), \
             mock.patch.object(self.q, "build_report", return_value="/tmp/dummy.xlsx"):
            return self.q.cmd_profile("成都讯格得信息科技有限公司", "", "")

    def _cached_profile(self):
        from qcc.aggregator import CompanyProfile
        return CompanyProfile(
            name="成都讯格得信息科技有限公司", credit_code="91X", legal_person="邱昊",
            status="存续", three_element_ok=True, risk_level="low", hits=[],
            cost=7.5, called_apis=[2006], payloads={}, query_date="2026-07-30 12:00:00",
            source="cache")

    def test_cache_hit_returns_cached_and_skips_qcc(self):
        from common.company_store import MemoryCompanyStore
        store = MemoryCompanyStore()
        store.insert(self._cached_profile())
        agg = mock.MagicMock()
        agg.fetch_profile.side_effect = AssertionError("cache hit 不应调企查查")
        rc = self._run_profile(store, agg)
        out = json.loads(sys.stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["profile"]["source"], "cache")
        self.assertEqual(out["profile"]["name"], "成都讯格得信息科技有限公司")
        agg.fetch_profile.assert_not_called()

    def test_cache_miss_calls_qcc_and_inserts(self):
        from common.company_store import MemoryCompanyStore
        from qcc.aggregator import CompanyProfile
        store = MemoryCompanyStore()
        live = CompanyProfile(
            name="成都讯格得信息科技有限公司", credit_code="91X", legal_person="邱昊",
            status="存续", three_element_ok=True, risk_level="low", hits=[],
            cost=7.5, called_apis=[2006, 856, 887, 888, 889], payloads={},
            query_date="2026-08-17 17:00:00", source="qcc_live")
        agg = mock.MagicMock()
        agg.fetch_profile.return_value = live
        rc = self._run_profile(store, agg)
        out = json.loads(sys.stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["profile"]["source"], "qcc_live")
        agg.fetch_profile.assert_called_once()
        self.assertEqual(len(store.rows), 1)

    def test_force_refresh_skips_cache_and_calls_qcc(self):
        from common.company_store import MemoryCompanyStore
        store = MemoryCompanyStore()
        store.insert(self._cached_profile())  # 库里已有
        from qcc.aggregator import CompanyProfile
        live = CompanyProfile(
            name="成都讯格得信息科技有限公司", credit_code="91X", legal_person="邱昊",
            status="存续", three_element_ok=True, risk_level="low", hits=[],
            cost=7.5, called_apis=[2006, 856, 887, 888, 889], payloads={},
            query_date="2026-08-17 18:00:00", source="qcc_live")
        agg = mock.MagicMock()
        agg.fetch_profile.return_value = live
        with mock.patch.object(self.q, "_require_qcc", return_value=True), \
             mock.patch.object(self.q, "_require_mysql_store", return_value=store), \
             mock.patch.object(self.q, "default_aggregator", return_value=agg), \
             mock.patch.object(self.q, "save_snapshot", return_value="/tmp/dummy.json"), \
             mock.patch.object(self.q, "build_report", return_value="/tmp/dummy.xlsx"):
            rc = self.q.cmd_profile("成都讯格得信息科技有限公司", "", "", force_refresh=True)
        out = json.loads(sys.stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(out["profile"]["source"], "qcc_live")
        agg.fetch_profile.assert_called_once()
        # 强制刷新应写回新版本(库中现有 2 行:旧 cache + 新 live)
        self.assertEqual(len(store.rows), 2)


class CmdSearchLocalTests(unittest.TestCase):
    """search 应先读 legal_company 维度表:恰好 1 条则不调 886(source=local)。"""

    def setUp(self):
        import io
        import query
        self.q = query
        self._stdout = sys.stdout
        sys.stdout = io.StringIO()

    def tearDown(self):
        sys.stdout = self._stdout

    def _run_search(self, store, agg):
        with mock.patch.object(self.q, "_require_qcc", return_value=True), \
             mock.patch.object(self.q, "store_from_env", return_value=store), \
             mock.patch.object(self.q, "default_aggregator", return_value=agg):
            return self.q.cmd_search("成都讯格得信息科技有限公司")

    def test_local_match_skips_886(self):
        from common.company_store import MemoryCompanyStore
        from qcc.aggregator import CompanyProfile
        store = MemoryCompanyStore()
        store.insert(CompanyProfile(
            name="成都讯格得信息科技有限公司", credit_code="91X", legal_person="邱昊",
            status="存续", three_element_ok=True, risk_level="low", hits=[],
            cost=7.5, called_apis=[2006], payloads={}, query_date="2026-08-01 10:00:00",
            source="qcc_live"))
        agg = mock.MagicMock()
        agg.search_candidates.side_effect = AssertionError("本地命中不应调 886")
        rc = self._run_search(store, agg)
        out = json.loads(sys.stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        self.assertTrue(out["exact_match"])
        self.assertEqual(out["source"], "local")
        agg.search_candidates.assert_not_called()

    def test_no_store_falls_back_to_886(self):
        agg = mock.MagicMock()
        agg.search_candidates.return_value = [Candidate("成都讯格得信息科技有限公司", "91X", "邱昊", "存续")]
        rc = self._run_search(None, agg)
        out = json.loads(sys.stdout.getvalue())
        self.assertEqual(rc, 0)
        self.assertEqual(out["count"], 1)
        self.assertNotIn("source", out)             # 走 886 路径,无 source=local
        agg.search_candidates.assert_called_once()


if __name__ == "__main__":
    unittest.main()