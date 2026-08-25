# legal-zcode-skills

法务部用的两个 zcode 技能，装在 zcode / Cursor / Hermes 的 skills 目录即可用。

| 技能目录 | 中文名 | 作用 |
|---|---|---|
| `legal-company-query/` | 企业风险查询 | 查一家公司的工商信息与风险等级，生成 Excel |
| `legal-approval-risk/` | 合同审批风险预警 | 定时扫描飞书待审批合同，把风险评到审批评论区并附 Excel |

## 安装到 zcode

1. 在 zcode「技能」页用本仓库地址安装（或把仓库克隆到 zcode 的 skills 目录）。应出现上面两个技能。
2. 每个技能文件夹里把 `.env.example` 复制为 `.env` 并填写凭据。**推荐由 IT 私下发一份已填好的 `.env`，法务只负责放到技能目录并保密。**
3. 每个技能目录装依赖：`py -m pip install -r requirements.txt`（macOS 用 `python3`）。外网不通走内网代理 `http://docker-proxy.xgate.com/simple`。
4. 自检：
   - 企业风险查询：`py scripts/query.py check`
   - 合同审批风险预警：`py scripts/poll.py check`

## 交付文档（给法务和管理员）

- [docs/法务操作说明书.md](docs/法务操作说明书.md) — 法务日常怎么用，零开发经验可读
- [docs/配置说明.md](docs/配置说明.md) — 法务/IT 要配什么，`.env` 各项人话解释
- [docs/飞书应用权限开通指南.md](docs/飞书应用权限开通指南.md) — 给飞书管理员，只开审批三条权限

## 运行环境

- 本机 Python 3.11（Windows 与 macOS 均可）。没有时见各技能目录下的 `INSTALL.md`。
- 企查查主账号 + Monica 账号两套 Key 都要填，只填一套会查不全。
- 合同审批风险预警还需飞书自建应用 + 审批定义 code + 表单字段映射，见《飞书应用权限开通指南》。

## 安全

- `.env`、真实密钥、企业快照、Excel、审计日志不入库（见 `.gitignore`）。
- 企查查数据仅用于内部风控审批，不得二次分发。
- 技能只查询/提示风险，不自动拦截、放行或签约。
