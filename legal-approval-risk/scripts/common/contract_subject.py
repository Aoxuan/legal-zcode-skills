# -*- coding: utf-8 -*-
"""合同 Word 附件的签约对方主体提取与一致性比对(2026-10-19 口径,二次纠偏)。

业务反馈:表单企业控件选的供应商可能与合同文件里实际签约的对方主体不一致,
此时风险查询无效。本模块从合同 docx 解析「甲方：xxx 乙方：xxx 丙方：xxx」类
抬头,取对方主体,与表单企业名归一化比对。

**统一签约主体排除法(2026-10-19 二次纠偏,替代首版的供应商→乙方/客户→甲方
方向规则)**:合作方常自行占据甲方位,方向不可靠。排除名单:
  ① 我方「签约主体」(审批单控件全称,甲乙丙任一侧);
  ② 自然人主体——**达人姓名可能是花名,不能拿表单控件比对**,改用关键词启发式:
     名称含公司类字样(公司/工作室/中心/集团/事务所等)视为公司,否则视为自然人
     (如「阿min学姐」)排除;
排除后剩余**唯一公司主体**即比对对象;剩余 0 个或多个 → 无法唯一确定 → None,
调用方走「放行+人工核对」兜底。

提取口径(2026-10-19 实测修订模式合同与漏拒案例后定):
  - 覆盖 body 全部段落(**含表格内**),python-docx 的 paragraphs 属性不含表格;
  - 段落文本取全部 w:t 节点——天然等同「接受所有修订」视图:修订插入(w:ins)
    的名称可见,被删除(w:delText)的旧名称不出现(python-docx>=1.1 实测如此);
  - 支持**一行多抬头**(签署栏「甲方:【X】 乙方:Y」),按抬头正向前瞻切段;
  - 旧版 .doc(OLE2)走 UTF-16LE 全文件扫描;**文本型 PDF(Word 导出)走
    pdfplumber 逐页提取**(扫描型无文本层→空→人工核对兜底,不做 OCR);
  - 清洗剥【】〔〕等模板占位括号与并列占位段;剥后为空(未填的「【】」)视为该侧缺失。

比对基准是表单企业控件的名称(与企查查查询主体同源),不依赖企查查返回值,
保证主体不一致时不产生任何外部计费调用。
"""
from __future__ import annotations

import io
import re

# 甲方侧(采购/委托)与乙方侧(供货/受托)抬头别名;「甲方/乙方」优先于别名。
# 丙方一并解析(达人合同常见:甲=品牌方/乙=我方/丙=达人),由排除法甄别。
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

# 抬头行:角色 + 可选括号限定(如「甲方（买方）」,整组可选) + 冒号 + 值。
# 角色按长度降序避免短词误截;**角色词字符间容空白**——两端对齐排版会把
# 「丙方」拉成「丙    方」(2026-10-19 实测 202610190120 漏丙方根因),
# 逐字插入 \s* 后「丙\s*方」两种写法都命中
_ROLES_BY_LEN = sorted(_ROLE_SIDE, key=len, reverse=True)


def _role_pattern(role):
    """角色词正则:字符间容空白(「丙方」与「丙    方」等价)。"""
    return r"\s*".join(re.escape(ch) for ch in role)


_ROLES_RE = "|".join(_role_pattern(r) for r in _ROLES_BY_LEN)
_LINE_RE = re.compile(
    r"^\s*(?P<role>%s)\s*(?:[（(][^（）()]{0,20}[）)])?\s*[：:]\s*(?P<value>.+)$"
    % _ROLES_RE
)
# 一行多抬头(签署栏「甲方:【X】 乙方:Y」):按「角色+冒号」正向前瞻切段
_SPLIT_RE = re.compile(
    r"(?=(?:(?:%s)\s*(?:[（(][^（）()]{0,20}[）)])?\s*[：:]))" % _ROLES_RE
)
# 值里要剥掉的说明性后缀:「（以下简称"甲方"）」「(以下简称甲方)」等
_ALIASES_NOTE_RE = re.compile(r"[（(]以下简称[^（）()]*[）)]")
# 首尾可裁字符:句读/空白/引号/**模板占位方括号【】〔〕［】**(名称填在【】里,
# 未填时值为「【】」,剥后即空)。圆括号不剥——公司名可含「（上海）」
_EDGE_STRIP = "。，；、,;：: \t「」『』“”\"'【】〔〕［〕"
# 公司类主体关键词:含任一视为公司主体;不含(如达人花名「阿min学姐」)视为自然人
_COMPANY_HINTS = (
    "公司", "工作室", "中心", "集团", "事务所", "合作社", "研究院", "研究所",
    "学院", "大学", "学校", "医院", "协会", "商会", "银行", "商行", "商店",
    "工厂", "合伙", "个体工商户", "经营部", "服务部", "门市", "代表处",
    "办事处", "分行", "支行", "营业部", "杂志社", "报社", "出版社", "电视台", "厂",
)
# 英文主体常见组织形式(归一化后为大写,直接子串匹配;海外主体如 Daketengwu Holding Limited)
_EN_COMPANY_HINTS = ("LIMITED", "LTD", "LLC", "LLP", "PLC", "INC", "CORP",
                     "COMPANY", "CO.", "GMBH", "PTE", "B.V.", "S.A.")

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# 旧版 .doc(OLE2 复合文档)魔数
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# .doc 文本扫描:正文以 UTF-16LE 连续存储,提取可读中文段(实测 202610190113
# 补充协议抬头「…甲方：X乙方：Y」可完整取出)
_READABLE_RE = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9，。：:；、（）()\[\]【】「」“”‘’《》·\-＿\s]{6,}"
)


class ContractParseError(Exception):
    """合同解析失败(非 docx / 无主体抬头 / docx 结构异常)。code 供日志区分。"""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        Exception.__init__(self, "%s%s" % (code, (": " + detail) if detail else ""))


def normalize_name(name):
    """归一化企业名:NFKC 全角转半角(Ａ→A、（→(、：→:、｜→|),去空白,统一大写。"""
    if not name:
        return ""
    import unicodedata
    text = unicodedata.normalize("NFKC", str(name))
    return re.sub(r"\s+", "", text).upper()


def names_match(a, b, min_len=4):
    """两个名称是否指同一主体:归一化后相等,或互为包含(须 ≥min_len 字防误判)。"""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return len(na) >= min_len and len(nb) >= min_len and (na in nb or nb in na)


def looks_like_company(name):
    """名称是否像公司主体:含公司类关键词(公司/工作室/中心/集团或英文 LIMITED 等)。

    不含关键词的(如达人花名「阿min学姐」、自然人姓名)视为自然人主体。
    达人姓名可能是花名,不能拿表单控件值比对,故用关键词启发式。"""
    text = normalize_name(name)
    if any(hint in text for hint in _COMPANY_HINTS):
        return True
    return any(hint in text for hint in _EN_COMPANY_HINTS)


def clean_subject_value(value):
    """清洗抬头值:剥占位括号【】与「（以下简称…）」与首尾句读引号,
    截掉拼接的信用代码等标签段;未填占位「【】」剥后为空串。

    值内可能并列第二个占位段(实测:「乙方:【赵馨】 昵称:【小福馨爱米线】"),
    剥完整体包裹后中间仍残余 】…【 ——截到最早出现的占位括号字符,防止
    后段文本(昵称/身份证号栏)污染名称或误触公司关键词。
    旧版 .doc 的 UTF-16 扫描段里抬头常与后续正文粘连(实测 202610190113
    「…有限公司    基于甲乙双方…」),按连续 ≥2 空白截断取首段。"""
    text = (value or "").strip()
    text = _ALIASES_NOTE_RE.sub("", text).strip()
    # 值后常拼接标签段(抬头行常连着联系人/联系方式/信用代码等),按最早出现的标记截断
    cut = len(text)
    for mark in ("统一社会信用代码", "信用代码", "法定代表人", "身份证号",
                 "联系人", "联系方式", "联系电话", "联系地址", "住所", "地址",
                 "电话", "邮箱", "开户行", "账号"):
        idx = text.find(mark)
        if idx > 0:
            cut = min(cut, idx)
    text = text[:cut]
    # .doc 粘连正文:连续 ≥2 空白视为段界,取首段(docx 段落值不含连续空白,不受影响)
    text = re.split(r"\s{2,}", text.strip())[0]
    # 剥整体首尾包裹(【杭州X公司】→杭州X公司)后,再截中间残余的并列占位段
    text = text.strip(_EDGE_STRIP)
    inner = len(text)
    for mark in ("【", "】", "〔", "〕", "［", "］"):
        idx = text.find(mark)
        if idx >= 0:
            inner = min(inner, idx)
    if inner < len(text):
        text = text[:inner]
    return text.strip(_EDGE_STRIP)


def _doc_text(blob):
    """旧版 .doc(OLE2)文本提取:不解析复合文档结构,直接全文件按 UTF-16LE
    解码后扫描可读中文段(正文文本以 UTF-16LE 连续存储)。"""
    text = blob.decode("utf-16-le", errors="ignore")
    return [m.group(0) for m in _READABLE_RE.finditer(text)]


def _pdf_paragraphs(blob):
    """文本型 PDF(Word 导出)段落提取:pdfplumber 逐页取文本,按行切分。

    只支持带文本层的 PDF(Word/在线文档导出,2026-10-19 口径);扫描型 PDF
    无文本层 → 返回空列表,调用方判 no_subject 走「人工核对」兜底。"""
    try:
        import pdfplumber
    except ImportError:
        raise ContractParseError(
            "parse_error", "pdfplumber 未安装(py -m pip install pdfplumber)")
    out = []
    try:
        with pdfplumber.open(io.BytesIO(blob)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        out.append(line)
    except Exception as e:
        raise ContractParseError("parse_error", "%s: %s" % (type(e).__name__, e))
    return out


def _docx_paragraphs(blob):
    """Word/PDF 附件 bytes → 文本段列表。

    docx(PK 头): body 全部段落(**含表格内**;不含页眉页脚),段落文本拼接全部
    w:t 节点——天然等同「接受所有修订」视图:修订插入(w:ins)的名称可见,
    被删除(w:delText)的旧名称不出现(python-docx>=1.1 实测)。
    旧版 .doc(OLE2 魔数): UTF-16LE 解码扫描可读段(2026-10-19 实测补充协议
    抬头可完整提取,此前该类附件被误入「无法校验」兜底导致漏拒)。
    文本型 PDF(%PDF 头,Word 导出): pdfplumber 逐页提取(2026-10-19 口径:
    仅支持文本层 PDF;扫描型无文本层 → 空列表 → no_subject 人工核对兜底)。
    其他格式(图片等)抛 ContractParseError("not_docx")。
    """
    if not blob:
        raise ContractParseError("not_docx")
    if blob[:8] == _OLE_MAGIC:
        return _doc_text(blob)
    if blob[:4] == b"%PDF":
        return _pdf_paragraphs(blob)
    if blob[:2] != b"PK":
        raise ContractParseError("not_docx")
    try:
        from docx import Document
        doc = Document(io.BytesIO(blob))
    except Exception as e:
        raise ContractParseError("parse_error", "%s: %s" % (type(e).__name__, e))
    out = []
    try:
        for p in doc.element.body.iter(_W_NS + "p"):
            text = "".join(t.text or "" for t in p.iter(_W_NS + "t"))
            if text.strip():
                out.append(text)
    except Exception as e:  # body 结构异常
        raise ContractParseError("parse_error", "%s: %s" % (type(e).__name__, e))
    return out


def _iter_headings(line):
    """一段文本内逐个产出抬头:(side, role, value),role 为去空白归一后的角色词。

    签署栏/旧版 .doc 常见「甲方:【X】 乙方:Y」甚至「补充协议二甲方：X乙方：Y」
    连续拼在一行:先按「角色+冒号」前瞻切段,再对每段搜索抬头(.doc 提取段
    前面可能带「补充协议二」等前缀,故用 search 不锚定段首)。角色词字符间
    容空白(「丙    方」两端对齐排版),归一后查侧别表。
    """
    for seg in _SPLIT_RE.split((line or "").strip()):
        m = _LINE_RE.search(seg.strip())
        if not m:
            continue
        role = re.sub(r"\s+", "", m.group("role"))
        side = _ROLE_SIDE.get(role)
        if side:
            yield side, role, m.group("value")


def _scan_subjects(lines):
    """扫描段落,按侧归集主体 {side: {name, canonical, role}}。同侧取首次出现;
    「甲方/乙方/丙方」字样优先于别名(后扫到的别名不覆盖已命中的 canonical)。"""
    found = {}
    for line in lines:
        for side, role, value in _iter_headings(line):
            name = clean_subject_value(value)
            if len(name) < 2:  # 空占位「【】」剥后为空,该侧缺失
                continue
            canonical = role in _CANONICAL
            prev = found.get(side)
            if prev is None or (canonical and not prev.get("canonical")):
                found[side] = {"name": name, "canonical": canonical, "role": role}
    return found


def parse_contract_subjects(blob, head_scan=60):
    """解析合同 docx,返回 {party_a: 名称, party_b: 名称[, party_c: ...]}。

    先扫首部 head_scan 段(合同抬头通常在首两页);任一侧缺失再全文补扫该侧。
    少于两侧抛 ContractParseError("no_subject")。
    """
    lines = _docx_paragraphs(blob)
    found = _scan_subjects(lines[:head_scan])
    if len(found) < 2:
        for side, item in _scan_subjects(lines).items():
            found.setdefault(side, item)
    subjects = {side: item["name"] for side, item in found.items()}
    if len(subjects) < 2:
        raise ContractParseError("no_subject", "仅识别到 %s" % list(subjects.values()))
    return subjects


def resolve_counterparty(subjects, our_entity, field=""):
    """签约主体排除法甄别合同对方主体,返回 (kind, name)。

    kind ∈ company | natural_person | unresolved:
      - company:排除后剩**唯一公司主体**,name=名称(调用方与表单企业名比对);
      - natural_person:排除我方后**仅剩自然人、无公司主体**——合同对方是自然人
        而表单企业控件填了企业,形态不符,name=自然人名(调用方按 mismatch 拒绝,
        2026-10-19 三次口径:此前误入「无法校验」兜底放行导致漏拒);
      - unresolved:剩 0 个或 ≥2 个公司主体,无法唯一确定(人工核对兜底)。
    三方达人合同(我方+对方公司+达人个人)→ 达人在自然人池被忽略 → company ✓。
    统一排除法(2026-10-19 二次纠偏):不按控件字段定方向,field 仅日志用。
    """
    sides = [v for v in (subjects or {}).values() if v]
    if not sides:
        return "unresolved", ""
    companies, persons = [], []
    for name in sides:
        if our_entity and names_match(name, our_entity):
            continue  # 我方
        if looks_like_company(name):
            companies.append(name)
        else:
            persons.append(name)
    if len(companies) == 1:
        return "company", companies[0]
    if not companies and persons:
        return "natural_person", persons[0]
    return "unresolved", ""
