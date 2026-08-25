# -*- coding: utf-8 -*-
"""凭据路由回归测试:验证 886/2006/887/888/889 走 Monica,856/739 走主客户端,且不发起网络请求。

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
from common.config import QCC_MONICA_APIS


def _make_client(app_key: str) -> QccClient:
    return QccClient(app_key, "secret-%s" % app_key)


class CredentialRoutingTests(unittest.TestCase):
    def setUp(self):
        self.main = _make_client("MAIN")
        self.monica = _make_client("MONICA")
        self.agg = CompanyAggregator(self.main, monica_client=self.monica)
        # 用 mock 替换底层 _get,避免任何真实网络请求
        self.main._get = mock.MagicMock(return_value={"Status": "200", "Result": {}})
        self.monica._get = mock.MagicMock(return_value={"Status": "200", "Result": {}})

    def test_monica_apis_use_monica_client(self):
        for code in QCC_MONICA_APIS:
            with self.subTest(code=code):
                self.agg._call(code, search_key="某公司")
                self.monica._get.assert_called()
                self.main._get.assert_not_called()

    def test_main_apis_use_main_client(self):
        for code in (856, 739):
            with self.subTest(code=code):
                if code == 856:
                    self.agg._call(code, credit_code="c", company_name="n", oper_name="o")
                else:
                    self.agg._call(code, search_key="某公司")
                self.main._get.assert_called()
                self.monica._get.assert_not_called()
                # 重置以便下一轮独立断言
                self.main._get.reset_mock()
                self.monica._get.reset_mock()

    def test_no_monica_client_falls_back_to_main(self):
        """显式不提供 Monica 客户端时回退主客户端(仅用于无网络测试场景)。"""
        agg = CompanyAggregator(self.main, monica_client=None)
        agg._call(886, search_key="某公司")
        self.assertEqual(self.main._get.call_count, 1)

    def test_search_candidates_uses_monica_for_886(self):
        self.monica._get.return_value = {
            "Status": "200",
            "Result": [{"Name": "某公司", "CreditCode": "X", "OperName": "张三", "Status": "存续"}],
        }
        out = self.agg.search_candidates("某公司")
        self.monica._get.assert_called_once()
        self.main._get.assert_not_called()
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


class ConfigMonicaFallbackTests(unittest.TestCase):
    def test_get_qcc_monica_falls_back_to_main_when_missing(self):
        """缺 Monica 两项时回退主账号(生产账号已开通全部接口,只填两项)。"""
        env = {k: v for k, v in os.environ.items()
               if k not in ("QCC_APP_KEY_MONICA", "QCC_SECRET_KEY_MONICA")}
        env["QCC_APP_KEY"] = "MAIN_KEY"
        env["QCC_SECRET_KEY"] = "MAIN_SECRET"
        with mock.patch.dict(os.environ, env, clear=True):
            from common.config import get_qcc_monica
            creds = get_qcc_monica()
            self.assertEqual(creds["app_key"], "MAIN_KEY")
            self.assertEqual(creds["secret_key"], "MAIN_SECRET")

    def test_get_qcc_monica_uses_monica_when_present(self):
        env = {
            "QCC_APP_KEY": "MAIN_KEY", "QCC_SECRET_KEY": "MAIN_SECRET",
            "QCC_APP_KEY_MONICA": "MONICA_KEY", "QCC_SECRET_KEY_MONICA": "MONICA_SECRET",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            from common.config import get_qcc_monica
            creds = get_qcc_monica()
            self.assertEqual(creds["app_key"], "MONICA_KEY")
            self.assertEqual(creds["secret_key"], "MONICA_SECRET")

    def test_get_qcc_monica_raises_on_half(self):
        """有 AppKey 无 Secret 仍报错,不拿主账号 Secret 拼 Monica Key。"""
        env = {
            "QCC_APP_KEY": "MAIN_KEY", "QCC_SECRET_KEY": "MAIN_SECRET",
            "QCC_APP_KEY_MONICA": "MONICA_KEY",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            from common.config import get_qcc_monica
            with self.assertRaises(RuntimeError) as ctx:
                get_qcc_monica()
            self.assertIn("QCC_SECRET_KEY_MONICA", str(ctx.exception))

    def test_get_qcc_main_raises_when_missing(self):
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


if __name__ == "__main__":
    unittest.main()
