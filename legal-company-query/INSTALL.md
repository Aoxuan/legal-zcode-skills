# 运行环境安装（需求方）

本技能用本机 Python 跑 `scripts/query.py`。Windows 与 macOS 均可，请先安装 **Python 3.11**，再装技能依赖。

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

Windows：

```
py -m pip install -r requirements.txt
py scripts/query.py check
```

macOS：

```
python3 -m pip install -r requirements.txt
python3 scripts/query.py check
```

`check` 通过（企查查凭据 + MySQL 均就绪）后再做查询。凭据见同目录 `.env.example`。
