---
name: legal-company-query
description: 拉取企业经营信息与风险预警。当用户要查询企业/公司的工商信息、经营状态、法定代表人、统一社会信用代码、三要素核验、签约对象/交易对手背景调查、企业风险、涉诉失信被执行限高、合作风险排查时,务必使用本技能。即使用户只说"查一下这家公司""这个客户靠谱吗""帮我看看这家企业的风险",只要涉及了解一家企业的经营与风险状况,就应触发本技能。
---

# 企业风险查询技能

本技能通过企查查开放平台拉取一家企业的工商信息与风险预警,并按规则给出低/中/高风险分级,供法务/业务在签约前对签约对象做背景调查与风险把关。

开发源为仓库内 `法务合同签约技能/01-poc/`(规则与客户端以那边为准);本目录是发行副本。

## 前置:凭据与依赖

首次使用前必须配齐(只需一次),三端(zcode / Cursor / Hermes)步骤相同。本机需要 **Python 3.11**(Windows 与 macOS 均可)。下文 `py` 在 macOS 上换成 `python3`。

1. 探测:Windows 跑 `py --version`,macOS 跑 `python3 --version`。没有命令或不是 3.11 → 停下来,把 [INSTALL.md](INSTALL.md) 交给用户。不要开浏览器下载,也不要在用户未确认时静默安装系统软件。
2. 把本文件夹放入平台 skills 目录。
3. 复制 `.env.example` 为 `.env`,填入企查查主账号 `QCC_APP_KEY`/`QCC_SECRET_KEY`(必填) **以及** Monica 账号 `QCC_APP_KEY_MONICA`/`QCC_SECRET_KEY_MONICA`(必填,固定用于 886/2006/887/888/889) **以及** 远程 MySQL `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DB`(全部必填)。两套企查查 Key 都要,只填一套会查不全。平台环境变量同样生效。
4. 安装依赖:`py -m pip install -r requirements.txt`(macOS 用 `python3 -m pip ...`;requests、python-dotenv、openpyxl、pymysql)。
5. 自检:`py scripts/query.py check`(macOS 用 `python3 scripts/query.py check`;企查查凭据 + MySQL 连通均就绪才算通过)。

缺 Python / 企查查 / MySQL 时不要硬跑。缺 Python 指向 [INSTALL.md](INSTALL.md);缺凭据则按 `.env.example` 补齐技能根目录 `.env`。`check`/`profile` 缺 `MYSQL_*` 直接失败,不静默回退到只写本地。

## 工作流程(两阶段,你来编排对话)

企业名可能不精准,故分两步:先模糊匹配出候选,让用户选定一个,再对该企业取详情与风险。

### 第 1 步:模糊匹配候选

用户给出企业名(可能简称/不完整)后,运行:

```
py scripts/query.py search <企业名>
```

输出 JSON:`{ok, keyword, count, candidates:[{index, name, credit_code, legal_person, status}, ...], exact_match, match}`。

- 若 `exact_match == true`:无需让用户选,直接用 `match` 的 `name`/`credit_code`/`legal_person` 进入第 2 步(用户输入的企业名与某候选完全一致,已自动选定)。
- 若 `count == 0`:告诉用户"未找到匹配「<企业名>」的企业",请用户换个名称再试,结束。
- 若 `count == 1`:无需让用户选,直接对该候选进入第 2 步(用其 name/credit_code/legal_person)。
- 若 `count > 1`:把候选**编号展示**给用户(序号 + 名称 + 信用代码 + 法人 + 状态),请用户回复一个序号选定。用户选定后,取对应候选的 `name`/`credit_code`/`legal_person` 进入第 2 步。

### 第 2 步:取详情与风险

对选定的企业运行(三个参数依次为 name、credit_code、legal_person;后两个若无可用空串 `""` 传,但建议尽量用第 1 步返回的真实值以保证三要素核验准确):

```
py scripts/query.py profile "<name>" "<credit_code>" "<legal_person>"
```

**强制查最新**(用户说"查最新/查实时/刷新一下/重新查"等,或明确表示不要缓存数据时)加 `--refresh`,绕过 MySQL 缓存、强制调企查查并写回新版本:

```
py scripts/query.py profile --refresh "<name>" "<credit_code>" "<legal_person>"
```

输出 JSON:`{ok, profile:{name, credit_code, legal_person, status, three_element_ok, risk_level, hits, called_apis, cost, snapshot_files, report_file, query_date, source}}`。

`profile` 会**先查 MySQL**:
- 若库中已有该企业的存档记录,直接返回库内结果并重生成 Excel,**不调用企查查、不产生费用**(`source="cache"`)。
- 若库中无记录,才真调企查查详情接口(2006/856/887/888/889,命异常加 739),落本地 JSON 快照 + 写一行进 MySQL + 出 Excel(`source="qcc_live"`,约 7.50 元/家)。MySQL 写入失败时把 `error` 转告用户,不要假装已落库。

向用户展示(用自然语言,不要直接贴 JSON):
- **数据来源**:先看 `source`——`cache` 告诉用户"本次返回的是数据库已存档结果(未调用企查查、未产生费用)";`qcc_live` 告诉用户"本次为实时查询企查查"。`cache` 命中时若用户想要最新数据,加 `--refresh` 重新跑一次即可强制拉最新并写回新版本。
- **基本信息**:名称、统一社会信用代码、法定代表人、登记状态。
- **三要素校验**:`three_element_ok`(true=一致 / false=不一致 / null=未核验)。
- **风险等级**:`risk_level` ∈ low/medium/high/unknown,用"低/中/高/未知"表述。
- **命中项**:逐条列出 `hits` 里的 `category` + `reason`(有 `count` 则附"共 N 条")。
- **成本提示**:本次 `cost` 元(`cache` 命中为 0;`qcc_live` 约 7.50 元),让用户对计费有感知。
- **Excel 汇总**:`report_file` 是一份 `qcc_{企业名称}_{时间}.xlsx`,两列(指标名/指标值),已自动落盘到技能内 `data/snapshots/`,并在返回的 `report_file` 中给出完整本地路径。这是本地文件落盘(不是浏览器下载),把路径告知用户,供法务/业务直接打开阅读。

### 第 2.5 步(可选):脱机重生成 Excel

若用户只想要 Excel 而不想再花一次 profile 的钱(例如该企业之前已查过、快照还在),运行:

```
py scripts/query.py export "<企业名>"
```

不调用任何企查查接口(零成本),从已落盘快照重生成 `qcc_{企业名称}_{时间}.xlsx`,输出 `{ok, report_file}`。无快照时返回 `{ok:false,error}`。

## 风险分级口径(重要)

当前分级规则按 `05-企查查接口数据法务确认表.xlsx`「风险分级规则建议稿」落地(`RULES_VERSION=legal-draft-2026-08-13`)。判定逻辑(取命中规则中的最高级):
- **高风险**:失信/被执行/破产/限高/股权冻结/严重违法、行政处罚(法务裁定)、三要素不一致、登记状态含吊销/注销/停业/撤销/清算中;裁判文书累计≥10或近12个月被告≥3;开庭公告累计≥10;立案累计≥5。
- **中风险**:未移出的经营异常、环保处罚、动产抵押/股权出质/清算/欠税/税务非正常/税收违法/司法拍卖/公安通告;近24个月裁判文书且为被告、近24个月开庭公告、近24个月立案且为被告。
- **低风险**:以上均未命中。
- **未知**:查无此企业或数据不存在。

详细字段映射见 [references/RISK_MAPPING.md](references/RISK_MAPPING.md);完整阈值见 [references/risk-rules.md](references/risk-rules.md)。向用户解释风险等级时先读这两个文件;法务裁定栏除行政处罚外尚未勾选,其余按建议稿执行。

## 注意事项

- **计费**:每次 `search` 约 0.10 元,每次 `profile` 约 7.50 元(企查查按调用计费)。非必要不重复查同一企业;同一企业短时间内多次询问,优先复用上一次结果而非重跑 profile。
- **数据合规**:企查查数据仅用于内部风控,不得二次分发;原始 JSON 响应落盘留档到技能内 `data/snapshots/json/`,Excel 汇总与 `_manifest.jsonl` 存 `data/snapshots/`,查询结果必写远程 MySQL。
- **只读查询**:本技能只查询与展示,不做拦截/放行/签约等动作;高风险仅作为提示,是否继续由法务/业务人工判断。
- **错误处理**:`query.py` 输出 `{ok:false,error}` 时,把 `error` 用自然语言转告用户,不要暴露原始堆栈。
