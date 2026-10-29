---
name: legal-overseas-entry
description: 把企查查查不到的海外主体公司(如 My Commerce Co.,Ltd 这类境外注册公司)人工录入企业库,让业务能在合同审批表单企业控件里搜到并免计费通过风险预审。当用户要录入海外公司、海外主体补录、境外企业入库、无法通过企查查查询的合作方登记时,务必使用本技能。
---

# legal-overseas-entry:海外主体人工录入(经鉴权接口)

海外主体企查查查不到,须先录入企业库,业务才能在审批表单选中、预审才能命中缓存(零计费)。
本技能**不接触数据库**:写入经服务端鉴权接口,口径(占位码/两行事实行/低风险/有效期)由服务端统一。
设计文档:`00-对齐/38-海外主体录入接口化-设计与实施计划.md`。

## 红线(必须遵守)

1. **只准用 `scripts/entry.py` 调接口**。禁止手写 SQL、禁止任何 UPDATE/DELETE、禁止直连数据库——本技能没有数据库凭据,也不要尝试。
2. **两段式**:先 `preview`,把"将写入的行 + 相似名提示 + 冲突提示"原样复述给用户;用户明确回复确认后,才用 `commit` 写库。
3. `commit` 必须带 `--actor`(录入人姓名,问用户"以谁的名义录入")。
4. 公司名必须与合同 Word 签约抬头**逐字一致**(空格/大小写/标点),对不上会被预审自动拒绝退回。

## 用法(Windows 用 py / macOS 用 python3)

    py -m pip install -r requirements.txt   # 首次;外网不通加 -i http://docker-proxy.xgate.com/simple
    py scripts/entry.py preview --name "公司全称" [--address .. --phone .. --bank .. --account .. --liaison .. --mobile ..]
    # 用户确认后:
    py scripts/entry.py commit --name "公司全称" --actor "录入人" [--同 preview 可选项]

## 要向用户收集的信息(能问到就全带上,录后只能管理员人工改库)

- 公司全称(必填,与合同抬头一致);地址;公司电话(国际前缀)
- 开户行与账号;我方对接人姓名与手机号;录入人姓名

## 结果判读

- preview 返回 `conflict=true` → 停止,把冲突行给用户人工核对,不 commit。
- preview 有 `similar` → 向用户逐条列出,确认非重复后再继续。
- commit 返回 200 → 成功;让用户在审批表单企业控件里搜**精确全称**验证可选中(搜片段搜不到)。

## 注意事项

- 风险评级固定"低风险"(海外主体无法机器分级)。法务对该主体有风控保留时,记录在案交法务人工跟进,不要试图改评级。
- 有效期:事实行 query_date=2027-12-31,2028-03 前需续期(重新 preview/commit 会被幂等挡住,届时联系管理员)。
- 报错 `鉴权失败` → .env 的 LEGAL_OVERSEAS_KEY 不对或本机时钟偏差>5分钟;`服务未启用` → 服务端未配 key,联系管理员。
- 本技能只 INSERT 不 UPDATE:录错了不能覆盖重录,需联系管理员按 `source='manual_overseas'` 定位人工处理。
