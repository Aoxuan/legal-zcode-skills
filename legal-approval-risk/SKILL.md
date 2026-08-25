---
name: legal-approval-risk
description: 定时扫描飞书里待审批(PENDING)的合同审批,读取表单里已选定的对方企业三要素,拉取企查查风险并评论回该审批实例。当用户要轮询合同审批风险、给待审批合同补风险预警、检查 PENDING 审批的签约对象风险、安装或运行 legal-approval-risk 时,务必使用本技能。
---

# 合同审批风险预警技能

本技能扫描飞书里 **PENDING(待审批)** 的合同审批,从表单读取已确定的对方企业(全称 + 统一社会信用代码 + 法定代表人),复用企查查风险引擎给出低/中/高分级,并在该审批实例的**评论区**写一条简短风险预警 + 完整风险 Excel 附件。同一实例只评论一次。

评论文案只保留:查询日期、风险评级、风险命中项,并附一句「完整风险信息见附件 Excel」。明细全部在附件 Excel 里。

企业已在表单选定,**不要再跑模糊搜索/匹配**。开发源为仓库内 `法务合同签约技能/01-poc/`;本目录是可安装发行副本。

## 前置:凭据与依赖

首次使用前必须配齐(只需一次)。本机需要 **Python 3.11**(Windows 与 macOS 均可)。下文 `py` 在 macOS 上换成 `python3`。

1. 探测:Windows 跑 `py --version`,macOS 跑 `python3 --version`。没有命令或不是 3.11 → 停下来,把 [INSTALL.md](INSTALL.md) 交给用户。不要开浏览器下载,也不要在用户未确认时静默安装系统软件。
2. 把本文件夹放入平台 skills 目录(zcode / Cursor / Hermes 相同)。
3. 复制 `.env.example` 为 `.env`。**向用户索取**并写入下列凭据,缺则不要硬跑:
   - 飞书自建应用 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
   - 合同审批定义 `FEISHU_APPROVAL_CODES`(多个用逗号分隔)
   - 表单对方企业控件 `FEISHU_FIELD_COMPANY`(当前是「对方单位名称」;信用代码和法人从该外部选项一并取出,不读「对方负责人」)
   - 企查查 `QCC_APP_KEY` / `QCC_SECRET_KEY`(主账号) **以及** `QCC_APP_KEY_MONICA` / `QCC_SECRET_KEY_MONICA`(Monica 账号)。两套都要,只填一套会查不全
   - 远程 MySQL `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB`
4. 安装依赖:`py -m pip install -r requirements.txt`(macOS 用 `python3 -m pip ...`)。外网不通时用内网代理 `http://docker-proxy.xgate.com/simple`。
5. 自检:`py scripts/poll.py check`。飞书 token + 审批 code + 企查查 + MySQL 均就绪才算通过。

缺 Python 指向 [INSTALL.md](INSTALL.md);缺凭据则按 `.env.example` 向用户逐项索取并写入技能根目录 `.env`。不要把密钥写进对话记录或提交到 git。

### 飞书应用权限(管理员须先开通并发布)

本技能用应用身份(`tenant_access_token`)读审批、写评论,需在自建应用开通后**保存 → 创建版本 → 发布 → 管理员审批**。完整步骤见交付文档《飞书应用权限开通指南》。最小权限:

- `approval:approval.list:readonly`(查询实例列表)
- `approval:approval:readonly`(实例详情)
- `approval:approval`(创建评论,含写)
- `contact:user.id:readonly`(手机号/邮箱反查 open_id,用于指定评论发表人)

本技能**不需要**机器人、消息事件、webhook。租户写评论必须带 `FEISHU_COMMENT_USER_ID`(及可选的 `FEISHU_COMMENT_USER_ID_TYPE`,默认 `open_id`)。没有现成 `open_id` 时,向用户要**手机号或个人邮箱**(不要企业邮箱);`FEISHU_APP_ID` / `FEISHU_APP_SECRET` 已写入 `.env` 后,用 `tenant_access_token` 调 `POST /open-apis/contact/v3/users/batch_get_id?user_id_type=open_id`,把返回的 `open_id` 写入技能根目录 `.env` 的 `FEISHU_COMMENT_USER_ID`。不要把手机号/邮箱写入对话以外的文件,不要提交 `.env`。查不到则提示检查 `contact:user.id:readonly` 已发版,且该账号在应用通讯录权限范围内。

## 工作流程

### 一次性体检

```
py scripts/poll.py check
```

输出 `{ok, report, mysql_connected, feishu_token, approval_codes}`。`ok != true` 时把 `error` 用自然语言转告用户,按缺失项补 `.env`,不要暴露堆栈。

### 干跑一条已知实例(验收,不写评论)

```
py scripts/poll.py test-instance <instance_code>
```

输出 `{ok, instance_code, status, company, profile, comment_preview, commented:false}`。用于确认表单字段映射与风险结果,再开正式轮询。

### 补写/改写一条实例的评论(短文本 + Excel)

```
py scripts/poll.py comment-instance <instance_code>
```

始终调企查查最新接口(约 7.50 元),生成完整风险 Excel 后写入或改写该实例的风险评论。数据库缓存只给人工查询技能 `legal-company-query` 用,本技能不走缓存。

### 一趟扫描(loop 每次触发跑这一条)

```
py scripts/poll.py poll
```

输出 `{ok, scanned, new, processed, skipped, errors, dry_run}`。

- `processed`:本趟新处理并(默认)已评论的实例,含 `risk_level` / `commented` / `report_file`
- 评论只写查询日期 / 风险评级 / 风险命中项 +「完整风险信息见附件 Excel」;明细在附件;缺附件则该实例失败、下趟重试
- `poll --force` 会再查一次企查查并改写已处理实例的原评论
- `skipped`:已在 `data/state.json` 里处理过,或已不再是 PENDING
- `errors`:缺字段 / 接口失败等;该实例不写 state,下趟会重试
- `.env` 设 `FEISHU_COMMENT_DRY_RUN=1` 时只取风险、不写评论、不记 state

向用户展示时用自然语言摘要:扫了几条、新评论了几条、风险等级各是什么、有无错误。不要直接贴整段 JSON。高风险只作提示,是否继续由审批人判断。

### 用 zcode loop 定时跑

轮询宿主是法务本机:关机即停、错过某个时间点跳过可接受。默认建议每天 10:00 / 14:00 / 16:00,或固定间隔(如 4 小时)。

用户说「开始轮询 / 定时扫审批风险」时:

1. 先跑一次 `check`,不通过则停下来补凭据。
2. 再立刻跑一次 `poll`,把摘要回报。
3. 用 loop 按约定间隔重复:`/loop 4h 运行 legal-approval-risk 的 poll 命令并把结果摘要告诉我`。
4. 用户说停则停止 loop,不要再武装下一拍。

不要在技能内部 `sleep` 死循环;调度由 zcode loop(或用户本机任务计划)负责。

## 风险分级口径

与 `legal-company-query` 相同,`RULES_VERSION=legal-draft-2026-08-13`:

- **高风险**:失信/被执行/破产/限高/股权冻结/严重违法、行政处罚(法务裁定)、三要素不一致、登记状态含吊销/注销/停业/撤销/清算中;裁判文书累计≥10或近12个月被告≥3;开庭公告累计≥10;立案累计≥5。
- **中风险**:未移出的经营异常、环保处罚、动产抵押/股权出质/清算/欠税/税务非正常/税收违法/司法拍卖/公安通告;近24个月裁判文书且为被告、近24个月开庭公告、近24个月立案且为被告。
- **低风险**:以上均未命中。
- **未知**:查无此企业或数据不存在。

## 注意事项

- **不做企业匹配**:表单已有准确企业,直接 `profile`,不要调用 `search` / 886。
- **计费**:每家约 7.50 元(始终查企查查最新,不走库缓存);幂等保证同一审批实例只查并评论一次。人工查询/缓存复用请用 `legal-company-query`。
- **数据合规**:企查查数据仅用于内部风控,不得二次分发。快照与 Excel 落在技能内 `data/snapshots/`,查询结果写远程 MySQL,已处理实例记在 `data/state.json`。
- **错误处理**:脚本输出 `{ok:false,error}` 时把 `error` 转告用户,不要暴露原始堆栈或密钥。
