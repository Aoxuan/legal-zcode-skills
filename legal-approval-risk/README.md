# Legal Approval Risk

作为合同审批流里「系统风险预审」节点的审批人(固定员工账号),扫描该账号在飞书合同审批里的待办任务,从表单企业控件(客户名称/供应商名称/达人企业名称,三者必现其一)读取对方三要素,**缓存优先(近 3 个月)**;缓存未命中先判 dim_company 自然人(是→回填「自然人无须评估风险」,不查企查查),企业才拉取企查查风险分级,用 `tasks/approve` 把风险评级(高风险/中风险带「详见风险报告附件」提示)回填到【企业风险评级】字段,低/中/高风险均把 Excel 风险报告**写入「风险报告附件」专用控件**(只保留报告,申请人误传文件一并删除;无报告场景误传文件清空),评论 `风险预审完成：<评级>，自动放行`,然后同意放行给法务。

预审节点一律同意放行:高/中/低风险都交给法务人工判断,不自动拦截。

本仓库是可直接放入 zcode、Cursor 或 Hermes `skills` 目录的自包含技能包。技能入口与使用流程见 [SKILL.md](SKILL.md)。

## 功能

- `check`：检查飞书应用凭证、审批定义配置、企查查凭据、远程 MySQL 与预审账号
- `poll`：扫描预审账号待办任务 → 取三要素 → 缓存优先 → 自然人免查（回填固定文案）/企业查企查查 → 回填【企业风险评级】（低/中/高均附「风险报告附件」Excel）→ 同意放行（幂等）
- `test-instance`：干跑一条已知实例，定位回填控件、取风险，不 approve
- `process-instance`：补处理单条实例（在预审账号待办里找到并 approve 回填，`--force` 强制重查）

不做企业模糊匹配。表单企业控件为外部选项单选，值「名称｜统一社会信用代码｜法定代表人」；字段名不固定（对方单位名称/相对方企业名称/签约企业名称/达人公司名称）。

## 前置（管理员须先做）

- 在合同审批定义里新增「系统风险预审」节点，审批人 = 预审员工账号，排在法务节点之前
- 在该节点把【企业风险评级】设为非必填 + 可编辑、把「风险报告附件」控件设为可编辑
- 飞书自建应用开通并发布：`approval:approval.list:readonly`、`approval:approval:readonly`、`approval:task`、`approval:task:list_by_user`、`contact:user.id:readonly`

## 安装

运行环境要求 **Python 3.11**，Windows 与 macOS 均可。没有 Python 时，按 [INSTALL.md](INSTALL.md) 安装（Windows 用 `py`，macOS 用 `python3`）。

把本文件夹放到宿主平台的 `skills` 目录后：

Windows：`py -m pip install -r requirements.txt`  
macOS：`python3 -m pip install -r requirements.txt`

复制 `.env.example` 为 `.env` 并填写飞书、企查查、MySQL 凭据，以及预审账号 `FEISHU_APPROVER_USER_ID` 与回填控件名。企查查只需客户账号一套 `QCC_APP_KEY` / `QCC_SECRET_KEY`。飞书自建应用还需开通并发布审批相关 scope，见 [SKILL.md](SKILL.md) 与交付文档《飞书应用权限开通指南》。

配置后自检：

Windows：`py scripts/poll.py check`  
macOS：`python3 scripts/poll.py check`

## 定时（zcode）

`check` 通过后，用 zcode loop 定时触发 `poll`，例如 `/loop 4h 运行 legal-approval-risk 的 poll 命令并把结果摘要告诉我`。也可按每天 10:00 / 14:00 / 16:00 三个定点。宿主关机即停，错过的时间点跳过即可。

## 数据与安全

- `.env`、真实密钥、企业快照、Excel、审计日志、`data/state.json` 不进入仓库
- 企查查数据仅用于内部风控审批，不得二次分发
- `poll` 仅在缓存未命中时查企查查最新（约 7.50 元/家）；90 天内同企业复用缓存，零成本
- 本技能只回填风险评级与报告并放行，不自动拦截或签约；高风险只作提示
