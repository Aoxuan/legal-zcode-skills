# -*- coding: utf-8 -*-
"""合同 Word 签约内容兜底：甲方/乙方抬头提取与相对方甄别。

表单三个企业控件（签约类型/客户名称/供应商名称）全空时用它兜底
（2026-10-26 生产案例：正式定义供应商控件 id 与副本不一致导致读空，
项目名称冒充相对方）。口径与实现裁剪自 legal-approval-risk 的
common/contract_subject.py（2026-10-19 二次纠偏版）：

- 统一签约主体排除法：排除我方「签约主体」与自然人主体（达人姓名
  可能是花名，用公司类关键词启发式甄别），剩余唯一公司主体即相对方；
  仅剩自然人则返回该自然人；0 个或 ≥2 个公司主体 → 无法唯一确定。
- 覆盖 body 全部段落（含表格内），段落文本取全部 w:t 节点＝接受所有
  修订视图；支持一行多抬头「甲方:【X】 乙方:Y」与角色词字符间容空白。

与源实现的差异：本技能入口已把 .doc 转成临时 .docx、非 Word 一律
停住，故只保留 docx 路径；抬头值额外剥「｜信用代码」段（表单控件同
款格式）；不做与表单控件的一致性比对，那是 approval-risk 的职责。
"""
import io
import re
import unicodedata

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 甲方侧/乙方侧/丙方抬头别名；「甲方/乙方」优先于别名（后扫别名不覆盖）。
PARTY_A_ALIASES = ("甲方", "买方", "需方", "采购方", "订购方", "发包方", "委托方",
                   "定作方", "出租方", "转让方", "许可方", "聘请方", "聘用方")
PARTY_B_ALIASES = ("乙方", "卖方", "供方", "供货方", "供应商", "承包方", "受托方",
                   "承揽方", "承租方", "受让方", "被许可方", "受聘方", "服务方")
PARTY_C_ALIASES = ("丙方", "居间方", "中介方", "见证方")
_CANONICAL = ("甲方", "乙方", "丙方")

_ROLE_SIDE = {}
for _alias in PARTY_A_ALIASES:
    _ROLE_SIDE[_alias] = "party_a"
for _alias in PARTY_B_ALIASES:
    _ROLE_SIDE[_alias] = "party_b"
for _alias in PARTY_C_ALIASES:
    _ROLE_SIDE[_alias] = "party_c"

# 角色词字符间容空白：两端对齐排版会把「丙方」拉成「丙    方」
_ROLES_BY_LEN = sorted(_ROLE_SIDE, key=len, reverse=True)


def _role_pattern(role):
    return r"\s*".join(re.escape(ch) for ch in role)


_ROLES_RE = "|".join(_role_pattern(r) for r in _ROLES_BY_LEN)
# 抬头行：角色 + 可选括号限定（如「甲方（买方）」）+ 冒号 + 值
_LINE_RE = re.compile(
    r"^\s*(?P<role>%s)\s*(?:[（(][^（）()]{0,20}[）)])?\s*[：:]\s*(?P<value>.+)$"
    % _ROLES_RE
)
# 一行多抬头：按「角色+冒号」正向前瞻切段
_SPLIT_RE = re.compile(
    r"(?=(?:(?:%s)\s*(?:[（(][^（）()]{0,20}[）)])?\s*[：:]))" % _ROLES_RE
)
_ALIASES_NOTE_RE = re.compile(r"[（(]以下简称[^（）()]*[）)]")
# 首尾可裁：句读/空白/引号/模板占位方括号。圆括号不剥——公司名可含「（上海）」
_EDGE_STRIP = "。，；、,;：: \t「」『』“”\"'【】〔〕［］"
# 公司类关键词：含任一视为公司主体；不含（如达人花名）视为自然人
_COMPANY_HINTS = (
    "公司", "工作室", "中心", "集团", "事务所", "合作社", "研究院", "研究所",
    "学院", "大学", "学校", "医院", "协会", "商会", "银行", "商行", "商店",
    "工厂", "合伙", "个体工商户", "经营部", "服务部", "门市", "代表处",
    "办事处", "分行", "支行", "营业部", "杂志社", "报社", "出版社", "电视台", "厂",
)
_EN_COMPANY_HINTS = ("LIMITED", "LTD", "LLC", "LLP", "PLC", "INC", "CORP",
                     "COMPANY", "CO.", "GMBH", "PTE", "B.V.", "S.A.")


def normalize_name(name):
    """归一化：NFKC 全角转半角、去空白、统一大写。"""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", str(name))
    return re.sub(r"\s+", "", text).upper()


def names_match(a, b, min_len=4):
    """同一主体判定：归一化后相等，或互为包含（须 ≥min_len 字防误判）。"""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return len(na) >= min_len and len(nb) >= min_len and (na in nb or nb in na)


def looks_like_company(name):
    """含公司类关键词（或英文组织形式）视为公司主体，否则自然人。"""
    text = normalize_name(name)
    if any(hint in text for hint in _COMPANY_HINTS):
        return True
    return any(hint in text for hint in _EN_COMPANY_HINTS)


def clean_subject_value(value):
    """清洗抬头值：剥「｜信用代码」段、「（以下简称…）」、首尾句读与占位括号；
    截掉值后拼接的标签段（联系人/地址等）；未填占位「【】」剥后为空。"""
    text = (value or "").strip()
    text = text.split("｜")[0].split("|")[0]
    text = _ALIASES_NOTE_RE.sub("", text).strip()
    cut = len(text)
    for mark in ("统一社会信用代码", "信用代码", "法定代表人", "身份证号",
                 "联系人", "联系方式", "联系电话", "联系地址", "住所", "地址",
                 "电话", "邮箱", "开户行", "账号"):
        idx = text.find(mark)
        if idx > 0:
            cut = min(cut, idx)
    text = text[:cut]
    text = re.split(r"\s{2,}", text.strip())[0]
    text = text.strip(_EDGE_STRIP)
    inner = len(text)
    for mark in ("【", "】", "〔", "〕", "［", "］"):
        idx = text.find(mark)
        if idx >= 0:
            inner = min(inner, idx)
    if inner < len(text):
        text = text[:inner]
    return text.strip(_EDGE_STRIP)


def _docx_paragraphs(blob):
    """docx → 文本段列表：body 全部段落（含表格内），取全部 w:t 节点。"""
    from docx import Document

    doc = Document(io.BytesIO(blob))
    out = []
    for p in doc.element.body.iter(_W_NS + "p"):
        text = "".join(t.text or "" for t in p.iter(_W_NS + "t"))
        if text.strip():
            out.append(text)
    return out


def _iter_headings(line):
    for seg in _SPLIT_RE.split((line or "").strip()):
        m = _LINE_RE.search(seg.strip())
        if not m:
            continue
        role = re.sub(r"\s+", "", m.group("role"))
        side = _ROLE_SIDE.get(role)
        if side:
            yield side, role, m.group("value")


def _scan_subjects(lines):
    """按侧归集主体；同侧取首次出现，「甲方/乙方/丙方」优先于别名。"""
    found = {}
    for line in lines:
        for side, role, value in _iter_headings(line):
            name = clean_subject_value(value)
            if len(name) < 2:
                continue
            canonical = role in _CANONICAL
            prev = found.get(side)
            if prev is None or (canonical and not prev.get("canonical")):
                found[side] = {"name": name, "canonical": canonical}
    return found


def parse_contract_subjects(blob, head_scan=60):
    """返回 {party_a: 名称, party_b: 名称[, party_c: ...]}；少于两侧返回 {}。"""
    lines = _docx_paragraphs(blob)
    found = _scan_subjects(lines[:head_scan])
    if len(found) < 2:
        for side, item in _scan_subjects(lines).items():
            found.setdefault(side, item)
    if len(found) < 2:
        return {}
    return {side: item["name"] for side, item in found.items()}


def resolve_counterparty(subjects, our_entity):
    """签约主体排除法：(kind, name)。company=唯一公司；natural_person=仅剩
    自然人；unresolved=0 个或 ≥2 个公司主体。"""
    sides = [v for v in (subjects or {}).values() if v]
    if not sides:
        return "unresolved", ""
    companies, persons = [], []
    for name in sides:
        if our_entity and names_match(name, our_entity):
            continue  # 我方（甲乙丙任一侧）
        if looks_like_company(name):
            companies.append(name)
        else:
            persons.append(name)
    if len(companies) == 1:
        return "company", companies[0]
    if not companies and persons:
        return "natural_person", persons[0]
    return "unresolved", ""


def party_from_docx(blob, our_entity):
    """一步到位的兜底入口：(kind, name)。解析不出两侧或无法唯一甄别 →
    ("unresolved", "")，调用方停住。"""
    try:
        subjects = parse_contract_subjects(blob)
    except Exception:
        return "unresolved", ""
    return resolve_counterparty(subjects, our_entity)
