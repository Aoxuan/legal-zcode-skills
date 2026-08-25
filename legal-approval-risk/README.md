# Legal Approval Risk

扫描飞书待审批(PENDING)的合同审批，读取表单里已选定的对方企业，拉取企查查风险分级，并在审批评论区写一条简短风险预警 + 完整风险 Excel 附件。同一实例只处理一次。

评论文案只保留：查询日期、风险评级、风险命中项，并附「完整风险信息见附件 Excel」。明细全部在附件 Excel 里。

本仓库是可直接放入 zcode、Cursor 或 Hermes `skills` 目录的自包含技能包。技能入口与使用流程见 [SKILL.md](SKILL.md)。

## 功能

- `check`：检查飞书应用凭证、审批定义配置、企查查凭据和远程 MySQL
- `poll`：列出 PENDING 实例 → 取三要素 → 风险分级 → 写审批评论（短文本 + Excel，幂等）
- `test-instance`：干跑一条已知实例，不写评论
- `comment-instance`：补写/改写一条实例的风险评论（始终查企查查最新）

不做企业模糊匹配。表单必须已有企业全称、统一社会信用代码、法定代表人。

## 安装

运行环境要求 **Python 3.11**，Windows 与 macOS 均可。没有 Python 时，按 [INSTALL.md](INSTALL.md) 安装（Windows 用 `py`，macOS 用 `python3`）。

把本文件夹放到宿主平台的 `skills` 目录后：

Windows：`py -m pip install -r requirements.txt`  
macOS：`python3 -m pip install -r requirements.txt`

复制 `.env.example` 为 `.env` 并填写飞书、企查查、MySQL 凭据。企查查主账号和 Monica 账号两套都要填，只填一套会查不全。飞书自建应用还需开通并发布审批相关 scope，见 [SKILL.md](SKILL.md) 与交付文档《飞书应用权限开通指南》。

配置后自检：

Windows：`py scripts/poll.py check`  
macOS：`python3 scripts/poll.py check`

## 定时（zcode）

`check` 通过后，用 zcode loop 定时触发 `poll`，例如 `/loop 4h 运行 legal-approval-risk 的 poll 命令并把结果摘要告诉我`。也可按每天 10:00 / 14:00 / 16:00 三个定点。宿主关机即停，错过的时间点跳过即可。

## 数据与安全

- `.env`、真实密钥、企业快照、Excel、审计日志、`data/state.json` 不进入仓库
- 企查查数据仅用于内部风控审批，不得二次分发
- `poll` 对新企业始终查企查查最新（约 7.50 元）；已评论实例会被幂等跳过
- 本技能只在评论区提示风险并附完整 Excel，不自动拦截、放行或签约
