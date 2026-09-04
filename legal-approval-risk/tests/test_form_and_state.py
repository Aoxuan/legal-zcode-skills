# -*- coding: utf-8 -*-
"""表单企业字段提取、评级文案、approve 回填与幂等状态。不发起网络请求。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from common.state import StateStore
from common.company_store import MemoryCompanyStore, _is_id_number
from feishu.client import extract_companies, extract_company_fields, find_control
from poll import _rating_text, _build_approve_form, _process_one, fetch_or_cache_profile

_FIELDS = ["客户名称", "供应商名称", "达人企业名称"]
_TEST_ID = "11010519491231002X"  # 校验位合法的 18 位测试身份证


def _form_with_company():
    return [
        {"id": "cmp", "name": "供应商名称", "type": "radioV2",
         "value": "甲公司｜91ABC｜张三", "option": {"key": "91ABC", "text": "甲公司｜91ABC｜张三"}},
        {"id": "r1", "name": "企业风险评级", "type": "input", "value": ""},
        {"id": "a1", "name": "附件", "type": "attachmentV2", "value": ["https://old"]},
    ]


def _cfg():
    return {"field_companies": _FIELDS, "field_rating": "企业风险评级",
            "field_attachment": "附件", "approver_user_id": "ou_test",
            "approver_user_id_type": "open_id", "cache_days": 90}


def _profile(level="low", source="cache"):
    return {"name": "甲公司", "credit_code": "91ABC", "legal_person": "张三",
            "status": "存续", "three_element_ok": True, "risk_level": level,
            "hits": [], "cost": 0, "source": source}


class FormExtractTests(unittest.TestCase):
    def test_customer_radio(self):
        items = [{"id": "w1", "name": "客户名称", "type": "radioV2",
                  "value": "成都白兔有你文化传播有限公司｜91510108MA67L910XP｜陈沛健",
                  "option": {"key": "91510108MA67L910XP",
                             "text": "成都白兔有你文化传播有限公司｜91510108MA67L910XP｜陈沛健"}}]
        got = extract_companies(items, _FIELDS)
        self.assertEqual(got[0]["name"], "成都白兔有你文化传播有限公司")
        self.assertEqual(got[0]["credit_code"], "91510108MA67L910XP")
        self.assertEqual(got[0]["legal_person"], "陈沛健")
        self.assertEqual(got[0]["missing"], [])

    def test_supplier_radio(self):
        got = extract_companies(_form_with_company(), _FIELDS)
        self.assertEqual(got[0]["field"], "供应商名称")
        self.assertEqual(got[0]["name"], "甲公司")
        self.assertEqual(got[0]["credit_code"], "91ABC")

    def test_no_candidate_fields_returns_empty(self):
        items = [{"name": "我方单位名称", "type": "input", "value": "白兔有你"}]
        self.assertEqual(extract_companies(items, _FIELDS), [])

    def test_empty_value_widget_ignored(self):
        items = [{"name": "签约企业名称", "type": "radioV2", "value": ""}]
        self.assertEqual(extract_companies(items, _FIELDS), [])

    def test_legacy_single_key_wrapper(self):
        got = extract_company_fields([{"name": "签约企业名称", "value": "丙"}], "签约企业名称")
        self.assertEqual(got["name"], "丙")
        self.assertEqual(got["missing"], ["credit_code"])

    def test_control_name_containing_comma(self):
        """控件名含全角逗号(如「风险报告附件（请勿上传文件，由…上传）」)须按完整名命中。"""
        items = [{"id": "w9", "name": "风险报告附件（请勿上传文件，由法务AI智能体审批后上传）",
                  "type": "attachmentV2", "value": []}]
        ctl = find_control(items, "风险报告附件（请勿上传文件，由法务AI智能体审批后上传）")
        self.assertIsNotNone(ctl)
        self.assertEqual(ctl["id"], "w9")
        self.assertIsNone(find_control(items, "不存在的控件"))


class FindControlFuzzyTests(unittest.TestCase):
    """控件定位的先精确后模糊:括号说明后缀调整不影响匹配。"""

    def test_exact_match_wins_over_fuzzy(self):
        items = [
            {"id": "w1", "name": "风险报告附件（旧说明）", "type": "attachmentV2", "value": []},
            {"id": "w2", "name": "风险报告附件", "type": "attachmentV2", "value": []},
        ]
        self.assertEqual(find_control(items, "风险报告附件")["id"], "w2")

    def test_fuzzy_suffix_changed(self):
        """正式部署改了括号内说明,配置前缀仍命中。"""
        items = [{"id": "w1", "name": "风险报告附件（正式环境新说明）", "type": "attachmentV2", "value": []}]
        ctl = find_control(items, "风险报告附件")
        self.assertEqual(ctl["id"], "w1")

    def test_control_must_fully_contain_key(self):
        """控件名必须完全包含配置名:缺字或短于配置的控件名不匹配(2026-09-04 口径)。"""
        # 配置全名、控件名只是其中一段 → 不匹配(不做反向包含)
        items = [{"id": "w1", "name": "风险报告附件", "type": "attachmentV2", "value": []}]
        self.assertIsNone(find_control(items, "风险报告附件（请勿上传文件，由法务AI智能体审批后上传）"))
        # 控件名缺字(「风险报告附」) → 不匹配
        items2 = [{"id": "w1", "name": "风险报告附（新说明）", "type": "attachmentV2", "value": []}]
        self.assertIsNone(find_control(items2, "风险报告附件"))

    def test_short_key_never_fuzzy_matches(self):
        """过短配置(默认「附件」)不做模糊兜底,避免误中「合同附件」。"""
        items = [{"id": "w1", "name": "合同附件(请勿上传PDF、图片)", "type": "attachmentV2", "value": []}]
        self.assertIsNone(find_control(items, "附件"))

    def test_unrelated_control_not_matched(self):
        items = [{"id": "w1", "name": "合同附件(请勿上传PDF、图片)", "type": "attachmentV2", "value": []}]
        self.assertIsNone(find_control(items, "风险报告附件"))


class StateStoreTests(unittest.TestCase):
    def test_mark_and_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            store = StateStore(path)
            self.assertFalse(store.is_processed("INST-1"))
            store.mark("INST-1", risk_level="high", company="甲")
            self.assertTrue(store.is_processed("INST-1"))
            self.assertEqual(StateStore(path).get("INST-1")["company"], "甲")


class RatingTextTests(unittest.TestCase):
    def test_level_mapping(self):
        self.assertEqual(_rating_text({"risk_level": "high"}), "高风险，详见风险报告附件")
        self.assertEqual(_rating_text({"risk_level": "medium"}), "中风险，详见风险报告附件")
        self.assertEqual(_rating_text({"risk_level": "low"}), "低风险")
        self.assertEqual(_rating_text({"risk_level": "unknown"}), "未知（未查得风险数据）")

    def test_natural_person(self):
        self.assertEqual(_rating_text({"risk_level": "natural_person"}), "自然人无须评估风险")

    def test_none_fallback(self):
        self.assertEqual(_rating_text(None), "企业名称为空,风险查询接口不适用")


class IdNumberTests(unittest.TestCase):
    def test_valid_18(self):
        self.assertTrue(_is_id_number(_TEST_ID))

    def test_valid_15(self):
        self.assertTrue(_is_id_number("110105491231002"))

    def test_bad_checksum(self):
        self.assertFalse(_is_id_number("110105194912310021"))

    def test_ucc_not_id(self):
        self.assertFalse(_is_id_number("91510108MA67L910XP"))
        self.assertFalse(_is_id_number("91310114323125174J"))

    def test_empty_or_none(self):
        self.assertFalse(_is_id_number(""))
        self.assertFalse(_is_id_number(None))


class NaturalPersonTests(unittest.TestCase):
    def test_fetch_skips_qcc_for_person(self):
        store = MemoryCompanyStore()
        store.companies.append({"id": 1, "company_name": "张三", "credit_code": _TEST_ID,
                                "tax_no": _TEST_ID, "legal_person": ""})
        with mock.patch("poll.default_aggregator", side_effect=AssertionError("不应查企查查")):
            profile, err = fetch_or_cache_profile(store, "张三", _TEST_ID, "")
        self.assertIsNone(err)
        self.assertEqual(profile["risk_level"], "natural_person")
        self.assertEqual(profile["report_file"], "")

    def test_memory_source_mark_marks_person(self):
        store = MemoryCompanyStore()
        store.companies.append({"id": 2, "company_name": "李四", "credit_code": "bad-id",
                                "tax_no": "bad-id", "legal_person": "",
                                "source": "自然人信息录入申请表单"})
        self.assertTrue(store.is_natural_person("李四", "bad-id"))

    def test_company_not_person(self):
        store = MemoryCompanyStore()
        store.companies.append({"id": 3, "company_name": "甲公司",
                                "credit_code": "91510108MA67L910XP",
                                "tax_no": "91510108MA67L910XP", "legal_person": "张三"})
        self.assertFalse(store.is_natural_person("甲公司", "91510108MA67L910XP"))

    def test_process_one_natural_person(self):
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": _form_with_company()}
        profile = _profile("natural_person", "natural_person")
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, err = _process_one(
                    client, store=object(), state=state,
                    task={"task_id": "t1", "approval_code": "AC1", "instance_code": "INST-NP"},
                    cfg=cfg, dry_run=False,
                )
            self.assertIsNone(err)
            self.assertTrue(rec["approved"])
            self.assertEqual(rec["rating_text"], "自然人无须评估风险")
            self.assertEqual(rec["file_code"], "")
            self.assertTrue(state.is_processed("INST-NP"))
            client.upload_approval_file.assert_not_called()
            client.approve_task.assert_called_once()
            self.assertEqual(client.approve_task.call_args.kwargs["comment"],
                             "风险预审完成：自然人无须评估风险，自动放行")
            form = client.approve_task.call_args.args[4]
            att = next(f for f in form if f["id"] == "a1")
            self.assertEqual(att["value"], [])  # 自然人误传过文件(fixture 旧值)→清空


class BuildApproveFormTests(unittest.TestCase):
    def test_rating_and_attachment(self):
        form, _ = _build_approve_form(_form_with_company(), _cfg(), "高", "FILECODE")
        ids = {f["id"] for f in form}
        self.assertEqual(ids, {"r1", "a1"})
        rating = next(f for f in form if f["id"] == "r1")
        self.assertEqual(rating["value"], "高")
        att = next(f for f in form if f["id"] == "a1")
        self.assertEqual(att["type"], "attachmentV2")
        self.assertEqual(att["value"], ["FILECODE"])

    def test_no_file_code_skips_attachment(self):
        form, _ = _build_approve_form(_form_with_company(), _cfg(), "未知", "")
        self.assertEqual([f["id"] for f in form], ["r1"])

    def test_clear_attachment_writes_empty_value(self):
        """无报告但申请人误传过文件:附件控件写入空值(清空)。"""
        form, _ = _build_approve_form(_form_with_company(), _cfg(), "未知", "",
                                      clear_attachment=True)
        self.assertEqual([f["id"] for f in form], ["r1", "a1"])
        att = next(f for f in form if f["id"] == "a1")
        self.assertEqual(att["value"], [])

    def test_report_replaces_mistaken_uploads(self):
        """有报告时附件 value 只含报告 code,申请人误传文件(fixture 里的旧值)被删除。"""
        form, _ = _build_approve_form(_form_with_company(), _cfg(), "高", "FC1")
        att = next(f for f in form if f["id"] == "a1")
        self.assertEqual(att["value"], ["FC1"])

    def test_missing_rating_control_raises(self):
        items = [{"id": "cmp", "name": "对方单位名称", "type": "radioV2",
                  "value": "甲公司｜91ABC｜张三"}]
        with self.assertRaises(RuntimeError):
            _build_approve_form(items, _cfg(), "高", "FILECODE")


class ProcessOneTests(unittest.TestCase):
    def _task(self, ic="INST-NEW"):
        return {"task_id": "t1", "approval_code": "AC1", "instance_code": ic}

    def test_approves_and_marks(self):
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": _form_with_company()}
        client.upload_approval_file.return_value = {"code": "FC1", "url": "u", "fileSize": 2,
                                                    "title": "r.xlsx", "type": "attachment"}
        profile = _profile("high")
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "qcc_甲公司.xlsx")
            with open(xlsx, "wb") as f:
                f.write(b"PK")
            profile["report_file"] = xlsx
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, err = _process_one(client, store=object(), state=state,
                                        task=self._task(), cfg=cfg, dry_run=False)
            self.assertIsNone(err)
            self.assertTrue(rec["approved"])
            self.assertEqual(rec["rating_text"], "高风险，详见风险报告附件")
            self.assertEqual(rec["file_code"], "FC1")
            self.assertTrue(state.is_processed("INST-NEW"))
            self.assertEqual(state.get("INST-NEW")["reason"], "approved")
            client.approve_task.assert_called_once()
            self.assertEqual(client.approve_task.call_args.kwargs["comment"],
                             "风险预审完成：高风险，自动放行")
            self.assertEqual(client.upload_approval_file.call_count, 1)
            form = client.approve_task.call_args.args[4]
            self.assertTrue(any(f["id"] == "r1" and f["value"] == "高风险，详见风险报告附件" for f in form))
            att = next(f for f in form if f["id"] == "a1")
            self.assertEqual(att["value"], ["FC1"])  # 只保留报告;fixture 里误传的旧值被删除

    def test_low_risk_attaches_report_too(self):
        """低风险:同样上传报告并附到附件控件(2026-09-04 口径:低/中/高均附)。"""
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": _form_with_company()}
        client.upload_approval_file.return_value = {"code": "FC1", "url": "u", "fileSize": 2,
                                                    "title": "r.xlsx", "type": "attachment"}
        profile = _profile("low")
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "qcc_甲公司.xlsx")
            with open(xlsx, "wb") as f:
                f.write(b"PK")
            profile["report_file"] = xlsx
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, err = _process_one(client, store=object(), state=state,
                                        task=self._task("INST-LOW"), cfg=cfg, dry_run=False)
            self.assertIsNone(err)
            self.assertTrue(rec["approved"])
            self.assertEqual(rec["rating_text"], "低风险")
            self.assertEqual(rec["file_code"], "FC1")
            self.assertEqual(client.approve_task.call_args.kwargs["comment"],
                             "风险预审完成：低风险，自动放行")
            form = client.approve_task.call_args.args[4]
            att = next(f for f in form if f["id"] == "a1")
            self.assertEqual(att["value"], ["FC1"])

    def test_unknown_clears_mistaken_uploads(self):
        """未知(查无此企业):不附报告,但申请人误传过文件(fixture 旧值)则清空附件控件。"""
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": _form_with_company()}
        profile = _profile("unknown")
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, err = _process_one(client, store=object(), state=state,
                                        task=self._task("INST-UNK"), cfg=cfg, dry_run=False)
            self.assertIsNone(err)
            self.assertTrue(rec["approved"])
            self.assertEqual(rec["rating_text"], "未知（未查得风险数据）")
            self.assertEqual(rec["file_code"], "")
            client.upload_approval_file.assert_not_called()
            self.assertEqual(client.approve_task.call_args.kwargs["comment"],
                             "风险预审完成：未知（未查得风险数据），自动放行")
            form = client.approve_task.call_args.args[4]
            att = next(f for f in form if f["id"] == "a1")
            self.assertEqual(att["value"], [])

    def test_clear_rejected_by_feishu_degrades_to_approve(self):
        """清空附件被飞书拒绝:去掉清空条目重试放行,记录提醒,不阻塞审批。"""
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": _form_with_company()}
        client.approve_task.side_effect = [RuntimeError("invalid form"), None]
        profile = _profile("unknown")
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, err = _process_one(client, store=object(), state=state,
                                        task=self._task("INST-CLR"), cfg=cfg, dry_run=False)
        self.assertIsNone(err)
        self.assertTrue(rec["approved"])
        self.assertIn("未能清空", rec["attachment_warning"])
        first_form = client.approve_task.call_args_list[0].args[4]
        second_form = client.approve_task.call_args_list[1].args[4]
        self.assertTrue(any(f["id"] == "a1" and f["value"] == [] for f in first_form))
        self.assertFalse(any(f["id"] == "a1" for f in second_form))

    def test_no_company_approves_with_fallback(self):
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": [
            {"id": "r1", "name": "企业风险评级", "type": "input", "value": ""}]}
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(os.path.join(tmp, "state.json"))
            rec, err = _process_one(client, store=object(), state=state,
                                    task=self._task("INST-NC"), cfg=cfg, dry_run=False)
            self.assertIsNone(err)
            self.assertTrue(rec["approved"])
            self.assertEqual(rec["rating_text"], "企业名称为空,风险查询接口不适用")
            client.upload_approval_file.assert_not_called()
            client.approve_task.assert_called_once()
            self.assertEqual(client.approve_task.call_args.kwargs["comment"],
                             "风险预审完成，自动放行")
            form = client.approve_task.call_args.args[4]
            self.assertEqual([f["id"] for f in form], ["r1"])

    def test_dry_run_does_not_approve_or_mark(self):
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": _form_with_company()}
        client.upload_approval_file.return_value = {"code": "FC1", "url": "u", "fileSize": 2,
                                                    "title": "r.xlsx", "type": "attachment"}
        profile = _profile("medium", "qcc_live")
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "qcc_甲公司.xlsx")
            with open(xlsx, "wb") as f:
                f.write(b"PK")
            profile["report_file"] = xlsx
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, err = _process_one(client, store=object(), state=state,
                                        task=self._task("INST-DRY"), cfg=cfg, dry_run=True)
            self.assertIsNone(err)
            self.assertFalse(rec["approved"])
            self.assertEqual(rec["rating_text"], "中风险，详见风险报告附件")
            self.assertFalse(state.is_processed("INST-DRY"))
            client.approve_task.assert_not_called()

    def test_upload_error_returns_error(self):
        cfg = _cfg()
        client = mock.Mock()
        client.get_instance.return_value = {"status": "PENDING", "form_items": _form_with_company()}
        client.upload_approval_file.side_effect = RuntimeError("boom")
        profile = _profile("medium")
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.join(tmp, "qcc_甲公司.xlsx")
            with open(xlsx, "wb") as f:
                f.write(b"PK")
            profile["report_file"] = xlsx
            state = StateStore(os.path.join(tmp, "state.json"))
            with mock.patch("poll.fetch_or_cache_profile", return_value=(profile, None)):
                rec, err = _process_one(client, store=object(), state=state,
                                        task=self._task(), cfg=cfg, dry_run=False)
            self.assertIsNone(rec)
            self.assertIn("上传风险 Excel 失败", err["error"])
            client.approve_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
