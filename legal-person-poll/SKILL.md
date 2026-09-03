---
name: legal-person-poll
description: 轮询飞书「自然人信息录入申请」审批流,把自然人表单信息(姓名/身份证号/电话/地址/我方对接人)采集入库到 MySQL dim_company 表,与企查查企业信息共表(身份证号=纳税人识别号)。当用户要采集自然人审批、同步自然人信息入库、检查某自然人的录入审批是否已入库、安装或运行 legal-person-poll 时,务必使用本技能。
---

# legal-person-poll:自然人信息录入审批采集入库

轮询飞书审批「自然人信息录入申请」(全状态:PENDING/APPROVED/REJECTED/CANCELED),
解析表单写入 MySQL `dim_company`(与企业信息共表,身份证号双写 `credit_code`+`tax_no`,
审批快照存 `invoice_json`,`instance_code` 幂等)。

设计口径与决策记录:`00-对齐/26-自然人审批采集-用户自部署提示词与共表设计.md`。

## 飞书应用配置清单（部署前置）

技能通过飞书自建应用调 OpenAPI,部署前需在**开发者后台**确认以下配置(都是本次实测踩坑验证过的):

### 1. API 权限(scope)

「权限管理」中开通并发布:

| scope | 用途 | 必须 |
|---|---|---|
| `approval:approval.list:readonly` | C2 拉实例列表 `POST /approval/v4/instances/query` | ✅ 必须 |
| `approval:approval:readonly` | C1 实例详情 `GET /approval/v4/instances/:code`(表单与 invoice_json 快照) | ✅ 必须 |
| `contact:contact.base:readonly` | 对接人译名(姓名;实测手机号也随此权限返回,若手机号为空再补开 `contact:user.phone:readonly`) | 译名功能需要 |

**不需要** `approval:approval`(写评论权限)——本技能对审批只读、不写评论。

### 2. 通讯录权限范围

「安全设置 → 通讯录权限范围」:建议设为**全部成员**;至少要覆盖「我方对接人」控件里会出现的员工,否则译名拿不到、回退存原始 user_id。

### 3. 审批定义对应用可见

「审批管理后台」确认「自然人信息录入申请」定义对本应用开放(API 可见),否则实例列表可能返回空。

### 4. 变更必须发布才生效 ⚠️

改 scope 或通讯录权限范围后,只在后台"保存"**不算生效**——必须在「版本管理与发布」**创建版本 → 申请发布 → 管理员审批通过**后才真正作用到 API(实测踩坑:改完不发版,接口行为不变)。

### 5. .env 配置

复制 `.env.example` 为 `.env`:

- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`:**每位用户填自己有权限的自建应用**凭证(开发者后台 → 凭证与基础信息)
- `APPROVAL_CODE`:已预填「自然人信息录入申请」正式流程的审批定义 code(`F0A86650-…`)
- `MYSQL_*`:**共库已预填**(`REDACTED_MYSQL_USER.dim_company`),密码由发放方通过**安全渠道单独发送**——勿写进文档/群聊/代码
- `FIELD_*`:表单控件名映射,须与审批表单里的控件**标题逐字一致**(不确定先跑 `test-instance` 对照原始 form)
- `WINDOW_DAYS`:扫描时间窗,默认 3

## 用法(Windows 用 py / macOS 用 python3)

```bash
py -m pip install -r requirements.txt   # 首次;外网不通加 -i http://docker-proxy.xgate.com/simple
py scripts/poll.py check                # 自检:飞书 token + approval_code + MySQL + 表结构(自动补列)
py scripts/poll.py test-instance <instance_code>   # 干跑单实例,打印真实 form 核对控件名
py scripts/poll.py poll                 # 一轮采集入库
```

先 `check` → `test-instance` 核对 `.env` 里 FIELD_* 控件名 → `poll`。

## 入库逻辑(先查后写,禁用 ON DUPLICATE KEY UPDATE)

对窗口内每个实例:

1. `instance_code` 已在库 → 跳过(已采集,不调详情接口)
2. 身份证号已在库 → 对比电话/地址/对接人:
   - 无差异 → 跳过
   - 有差异 → **不写库**,输出差异清单计「待人工确认」
3. 都不在 → INSERT(姓名→company_name,身份证号→credit_code+tax_no,
   source='自然人信息录入申请表单',完整 C1 响应→invoice_json)

统计输出:新增 X / 跳过 Y / 待人工确认 Z(附差异)/ 失败 W(附 instance_code)。

## 定时轮询

宿主机定时执行 `poll`(如 zcode /loop 或系统计划任务,每天 10:00/14:00/16:00)。
关机即停、错过跳过;`instance_code` 幂等保证重跑安全。

## 表变更(幂等自动执行)

```sql
ALTER TABLE dim_company
  ADD COLUMN instance_code   VARCHAR(64)  NULL,
  ADD COLUMN contact_address VARCHAR(255) NULL,
  ADD COLUMN liaison_staff   VARCHAR(128) NULL,
  ADD COLUMN staff_mobile    VARCHAR(32)  NULL,
  ADD UNIQUE KEY uk_instance_code (instance_code);
```

不动现有列与企业数据;企业行新列保持 NULL。

## 我方对接人译名(2026-09-03 已实现)

人员控件解析后调通讯录接口(`GET /contact/v3/users/:open_id`)译名:
`liaison_staff` 存**姓名**(多人逗号分隔)、`staff_mobile` 存**手机号**(去 +86 前缀);
译名失败回退存原始 user_id。需 scope `contact:contact.base:readonly`(手机号视权限可能为空)。

## 排错

- **C2 拉列表永远返回空但不报错**：时间参数名必须是 `instance_start_time_from`/`instance_start_time_to`(int 型),响应取 `instance_list[].instance.code`——飞书对 `start_time`/`end_time` 这类错误参数名静默忽略(2026-09-03 实测踩坑,代码已按正确口径实现)。
- `check` 中 approval_code 报错:确认审批定义对应用可见(审批管理后台将该分组授权给自建应用),及 scope `approval:approval.list:readonly`/`approval:approval:readonly` 已开并发布。
- 表单解析为空:控件显示名与 `.env` FIELD_* 不一致,跑 `test-instance` 对照原始 form 回填。
- 「我方对接人」显示 user_id(如 f669g7b1):通讯录译名失败(scope 未开/用户不在通讯录范围),回退存原始值,按需开 `contact:contact.base:readonly` 后自动恢复译名。
- 身份证号格式异常计入失败:15/18 位校验未通过(如控件配错/少输校验位),不入库,核对表单后重新提交。
