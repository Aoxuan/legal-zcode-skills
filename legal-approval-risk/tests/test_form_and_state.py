# -*- coding: utf-8 -*-
"""表单三要素提取、评论格式、幂等状态。不发起网络请求。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.state import StateStore
from feishu.client import extract_company_fields
from poll import format_risk_comment, _process_one

_COMBO_WIDGET = "对方单位名称"
_COMBO_FORM = [{
    "name": _COMBO_WIDGET,
    "type": "radioV2",
    "value": "甲公司｜91ABC｜张三",
    "option": {"key": "91ABC", "text": "甲公司｜91ABC｜张三"},
}]
_CFG = {
    "field_company": _COMBO_WIDGET,
    "comment_user_id": "",
    "comment_user_id_type": "open_id",
}


class FormExtractTests(unittest.TestCase):
    def test_radio_combo_option(self):
        items = [{
            "id": "widget1",
            "name": "对方单位名称",
            "type": "radioV2",
            "value": "成都白兔有你文化传播有限公司｜91510108MA67L910XP｜陈沛健",
            "option": {
                "key": "91510108MA67L910XP",
                "text": "成都白兔有你文化传播有限公司｜91510108MA67L910XP｜陈沛健",
            },
        }, {
            "name": "对方负责人",
            "type": "input",
            "value": "不该被当成法人的联系人",
        }]
        got = extract_company_fields(items, "对方单位名称")
        self.assertEqual(got["name"], "成都白兔有你文化传播有限公司")
        self.assertEqual(got["credit_code"], "91510108MA67L910XP")
        self.assertEqual(got["legal_person"], "陈沛健")
        self.assertEqual(got["missing"], [])

    def test_match_by_widget_id(self):
        items = [{
            "id": "cmp",
            "name": "公司",
            "value": "甲公司｜91ABC｜李四",
            "option": {"key": "91ABC", "text": "甲公司｜91ABC｜李四"},
        }]
        got = extract_company_fields(items, "cmp")
        self.assertEqual(got["name"], "甲公司")
        self.assertEqual(got["credit_code"], "91ABC")
        self.assertEqual(got["legal_person"], "李四")

    def test_name_only_missing_credit(self):
        got = extract_company_fields(
            [{"name": "对方单位名称", "value": "丙"}], "对方单位名称",
        )
        self.assertEqual(got["name"], "丙")
        self.assertEqual(got["missing"], ["credit_code"])

    def test_legal_person_optional(self):
        items = [{
            "name": "对方单位名称",
            "value": "丁公司｜91XYZ",
            "option": {"key": "91XYZ", "text": "丁公司｜91XYZ"},
        }]
        got = extract_company_fields(items, "对方单位名称")
        self.assertEqual(got["name"], "丁公司")
        self.assertEqual(got["credit_code"], "91XYZ")
        self.assertEqual(got["legal_person"], "")
        self.assertEqual(got["missing"], [])


class StateStoreTests(unittest.TestCase):
    def test_mark_and_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            store = StateStore(path)
            self.assertFalse(store.is_processed("INST-1"))
            store.mark("INST-1", risk_level="high", comment_id="c1", company="甲")
            self.assertTrue(store.is_processed("INST-1"))
            again = StateStore(path)
            self.assertTrue(again.is_processed("INST-1"))
            self.assertEqual(again.get("INST-1")["comment_id"], "c1")


class CommentAndProcessTests(unittest.TestCase):
    def test_format_comment_contains_level(self):
        text = format_risk_comment({
            "name": "甲公司",
            "credit_code": "91",
            "legal_person": "张三",
            "status": "存续",
            "three_element_ok": True,
            "risk_level": "high",
            "hits": [{"category": "失信", "reason": "命中失信", "count": 2}],
            "cost": 7.5,
            "query_date": "2026-08-24 19:47:31",
        })
        self.assertEqual(text, "\n".join([
            "查询日期：2026-08-24 19:47:31",
            "风险评级：高",
            "风险命中项：",
            "- 失信: 命中失信 (共2条)",
            "",
            "完整风险信息见附件 Excel",
        ]))
        self.assertNotIn("数据来源", text)
        self.assertNotIn("查询成本", text)

    def test_process_skips_already_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(os.path.join(tmp, "state.json"))
            state.mark("INST-9", risk_level="low")
            rec, skip, err = _process_one(
                client=None, store=None, state=state, instance_code="INST-9",
                cfg={}, dry_run=False,
            )
            self.assertIsNone(rec)
            self.assertIsNone(err)
            self.assertEqual(skip["reason"], "already_processed")

    def test_process_comments_and_marks(self):
        cfg = dict(_CFG)
        client = mock.Mock()
        client.get_instance.return_value = {
            "status": "PENDING",
            "form_items": _COMBO_FORM,
        }
        client.add_comment.return_value = "cmt-1"
        profile = {
            "name": "甲公司", "credit_code": "91ABC", "legal_person": "张三",
            "status": "存续", "three_element_ok": True, "risk_level": "low",
            "hits": [], "cost": 0, "source": "cache",
        }
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "qcc_甲公司.xlsx")
            with open(xlsx, "wb") as f:
                f.write(b"PK")
            profile["report_file"] = xlsx
            client.upload_approval_file.return_value = {
                "url": "https://example.com/a.xlsx",
                "fileSize": 2,
                "title": "qcc_甲公司.xlsx",
                "type": "attachment",
            }
            client.list_comments.return_value = []
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, skip, err = _process_one(
                    client, store=object(), state=state, instance_code="INST-NEW",
                    cfg=cfg, dry_run=False,
                )
            self.assertIsNone(skip)
            self.assertIsNone(err)
            self.assertTrue(rec["commented"])
            self.assertEqual(rec["comment_id"], "cmt-1")
            self.assertTrue(state.is_processed("INST-NEW"))
            client.upload_approval_file.assert_called_once_with(xlsx)
            client.add_comment.assert_called_once()
            self.assertEqual(
                client.add_comment.call_args.kwargs["files"][0]["title"],
                "qcc_甲公司.xlsx",
            )

    def test_dry_run_does_not_comment_or_mark(self):
        cfg = {"field_company": _COMBO_WIDGET}
        client = mock.Mock()
        client.get_instance.return_value = {
            "status": "PENDING",
            "form_items": _COMBO_FORM,
        }
        profile = {
            "name": "甲公司", "credit_code": "91ABC", "legal_person": "张三",
            "status": "存续", "three_element_ok": True, "risk_level": "medium",
            "hits": [], "cost": 7.5, "source": "qcc_live",
        }
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, skip, err = _process_one(
                    client, store=object(), state=state, instance_code="INST-DRY",
                    cfg=cfg, dry_run=True,
                )
            self.assertIsNone(skip)
            self.assertIsNone(err)
            self.assertFalse(rec["commented"])
            self.assertFalse(state.is_processed("INST-DRY"))
            client.add_comment.assert_not_called()

    def test_process_errors_without_excel(self):
        cfg = dict(_CFG)
        client = mock.Mock()
        client.get_instance.return_value = {
            "status": "PENDING",
            "form_items": _COMBO_FORM,
        }
        client.list_comments.return_value = []
        profile = {
            "name": "甲公司", "credit_code": "91ABC", "legal_person": "张三",
            "status": "存续", "three_element_ok": True, "risk_level": "low",
            "hits": [], "cost": 0, "source": "cache",
        }
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, skip, err = _process_one(
                    client, store=object(), state=state, instance_code="INST-NOX",
                    cfg=cfg, dry_run=False,
                )
            self.assertIsNone(rec)
            self.assertIsNone(skip)
            self.assertIn("Excel", err["error"])
            client.add_comment.assert_not_called()

    def test_force_updates_existing_comment(self):
        cfg = dict(_CFG)
        client = mock.Mock()
        client.get_instance.return_value = {
            "status": "PENDING",
            "form_items": _COMBO_FORM,
        }
        client.add_comment.return_value = "cmt-2"
        client.list_comments.return_value = []
        client.upload_approval_file.return_value = {
            "url": "https://example.com/a.xlsx",
            "fileSize": 2,
            "title": "qcc_甲公司.xlsx",
            "type": "attachment",
        }
        profile = {
            "name": "甲公司", "credit_code": "91ABC", "legal_person": "张三",
            "status": "存续", "three_element_ok": True, "risk_level": "low",
            "hits": [], "cost": 0, "source": "cache",
        }
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "qcc_甲公司.xlsx")
            with open(xlsx, "wb") as f:
                f.write(b"PK")
            profile["report_file"] = xlsx
            state = StateStore(os.path.join(tmp, "state.json"))
            state.mark("INST-OLD", risk_level="low", comment_id="cmt-1", company="甲公司")
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, skip, err = _process_one(
                    client, store=object(), state=state, instance_code="INST-OLD",
                    cfg=cfg, dry_run=False, force=True,
                )
            self.assertIsNone(skip)
            self.assertIsNone(err)
            self.assertTrue(rec["commented"])
            self.assertEqual(client.add_comment.call_args.kwargs["comment_id"], "cmt-1")


if __name__ == "__main__":
    unittest.main()
