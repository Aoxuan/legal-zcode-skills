# -*- coding: utf-8 -*-
"""旧版 .doc → .docx：本机 Word 静默转换临时副本后交回解析管线。

不动审批附件原件；转换在临时目录进行，用完即删。
COM 调用在子进程里做，父进程只管超时（超时强杀该 Word 实例）与降级提示。
"""
import os
import shutil
import subprocess
import sys
import tempfile

from ledger.errors import LedgerStop

_DOCX_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"
_CONVERT_TIMEOUT = 45  # 秒


def ensure_docx(data, convert=None):
    """docx 原样返回；.doc 交给转换器；其余格式原样返回（由 parse_docx 报格式错误）。"""
    blob = bytes(data or b"")
    if not blob.startswith(_OLE2_MAGIC):
        return blob
    converter = convert or _convert_via_word
    return converter(blob)


def _convert_via_word(blob):
    if sys.platform != "win32":
        raise LedgerStop("本机不是 Windows，无法自动转换 .doc。请手动另存为 .docx 后重新上传再跑")
    try:
        import win32com  # noqa: F401  仅探测依赖
    except ImportError:
        raise LedgerStop(
            "缺少 pywin32，无法自动转换 .doc。请执行 py -m pip install pywin32 后重跑，"
            "或手动另存为 .docx 重新上传"
        )
    child = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "doc_convert_child.py",
    )
    tmp = tempfile.mkdtemp(prefix="ledger_doc2docx_")
    src = os.path.join(tmp, "in.doc")
    dst = os.path.join(tmp, "out.docx")
    pid_file = os.path.join(tmp, "word.pid")
    try:
        with open(src, "wb") as fh:
            fh.write(blob)
        try:
            proc = subprocess.run(
                [sys.executable, child, src, dst, pid_file],
                capture_output=True, text=True, timeout=_CONVERT_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            _kill_word(pid_file)
            raise LedgerStop("Word 转换 .doc 超时，已停止。请手动另存为 .docx 重新上传再跑") from None
        marker = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
        if marker == "NO_WIN32":
            raise LedgerStop("缺少 pywin32，无法自动转换 .doc。请执行 py -m pip install pywin32 后重跑")
        if marker == "NO_WORD":
            raise LedgerStop("本机未检测到 Word，无法自动转换 .doc。请手动另存为 .docx 重新上传再跑")
        if marker != "OK" or not os.path.isfile(dst):
            raise LedgerStop("Word 打不开或转换失败（文件可能加密、损坏），已停住。请手动另存为 .docx 重新上传再跑")
        with open(dst, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _kill_word(pid_file):
    """只杀本次子进程起的 Word 实例，不动用户自己开的 Word。"""
    try:
        with open(pid_file) as fh:
            pid = fh.read().strip()
    except OSError:
        return
    if pid.isdigit():
        subprocess.run(
            ["taskkill", "/F", "/PID", pid],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
