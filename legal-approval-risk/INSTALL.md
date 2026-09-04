# 运行环境安装（需求方）

本技能用本机 Python 跑 `scripts/poll.py`。Windows 与 macOS 均可，请先安装 **Python 3.11**，再装技能依赖。

- Windows 命令用 `py`
- macOS 命令用 `python3`

不要用 Microsoft Store / 随意一个系统自带 Python，也不要让智能体开浏览器自行下载。装完后**新开**一个终端再验收。

## 先探测

Windows：

```
py --version
```

macOS：

```
python3 --version
```

输出含 `3.11` 即可跳到「安装技能依赖」。没有命令或版本不对，按下面对应系统安装。

## Windows

我方可提供官方安装包（例如 `python-3.11.x-amd64.exe`），保存到本机后任选一条。

### 路 A：自行安装（点鼠标）

1. 双击 `python-3.11.x-amd64.exe`
2. 勾选 **Add python.exe to PATH**
3. 选当前用户安装（一般不必管理员）
4. 关掉已打开的终端，新开一个，执行 `py --version`，应看到 3.11

### 路 B：静默命令（IT / PowerShell）

在安装包所在目录执行（把文件名换成实际文件名）：

```
.\python-3.11.x-amd64.exe /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
```

装完同样新开终端跑 `py --version`。被权限或安全软件拦截时改走路 A，或由管理员把 `InstallAllUsers=0` 改成 `1`。

## macOS

Apple Silicon 与 Intel 均可，安装对应的官方 3.11 即可。任选一条。

### 路 A：官方安装包（点鼠标）

1. 打开 [Python 3.11 macOS 安装包](https://www.python.org/downloads/release/python-3119/)（选 macOS 64-bit universal2 installer）
2. 按向导安装
3. 新开终端执行 `python3 --version`，应看到 3.11

### 路 B：Homebrew

已安装 Homebrew 时：

```
brew install python@3.11
python3.11 --version
```

若 `python3` 仍不是 3.11，后续命令改用 `python3.11`。

## 安装技能依赖

在技能根目录（有 `requirements.txt` 的目录）执行。

先判断外网是否通。不通则走内网 pip 代理：

```
py -m pip install -r requirements.txt -i http://docker-proxy.xgate.com/simple
```

外网通时：

Windows：

```
py -m pip install -r requirements.txt
py scripts/poll.py check
```

macOS：

```
python3 -m pip install -r requirements.txt
python3 scripts/poll.py check
```

`check` 通过（飞书 token + 审批定义 code + 企查查凭据 + MySQL + 预审账号均就绪）后再跑 `poll` / `test-instance`。凭据见同目录 `.env.example`。

## 飞书侧准备（管理员）

1. **审批定义改造**：在合同审批定义里新增「系统风险预审」节点，审批人 = 预审员工账号，排在法务节点之前；在该节点把【企业风险评级】设为非必填 + 可编辑、把「风险报告附件」附件控件（控件名完整包含"风险报告附件"即可，技能按前缀模糊匹配）设为可编辑。两个回填控件只保留技能写入的内容——申请人误写/误传会被删除。没有这个节点，API 无处回填字段（详见《飞书应用权限开通指南》工作三）。
2. 使用本租户自建应用，把 `app_id` / `app_secret` 交给每位安装者写入自己的 `.env`（「每人可装」指技能包可分发，凭证仍是租户应用）。
3. 应用开通并发布：`approval:approval.list:readonly`、`approval:approval:readonly`、`approval:task`（同意任务，回填字段必需）、`approval:task:list_by_user`（查待办任务）、`contact:user.id:readonly`。
4. 把合同审批定义的 `approval_code`、表单里候选企业控件名称（现为 `客户名称,供应商名称,达人企业名称`，三者互斥、必现其一，填入 `FEISHU_FIELD_COMPANY`）、回填控件名（`FEISHU_FIELD_RATING` 默认「企业风险评级」、`FEISHU_FIELD_ATTACHMENT` 配稳定前缀「风险报告附件」）告诉安装者。
5. 预审账号必须带 `FEISHU_APPROVER_USER_ID`（`FEISHU_APPROVER_USER_ID_TYPE` 指明 `user_id` 或 `open_id`）；没有现成 ID 时，用已开通的 `contact:user.id:readonly` 按手机号/邮箱反查后再写入 `.env`。
6. MySQL 须与 `legal-person-poll` 技能**共库**（`dim_company` 自然人判定依赖该表；首次运行自动建表）。
