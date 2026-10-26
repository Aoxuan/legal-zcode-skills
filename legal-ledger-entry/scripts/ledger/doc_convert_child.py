# -*- coding: utf-8 -*-
"""子进程：用本机 Word 把 .doc 另存为 .docx。父进程 doc_convert.py 负责超时与清理。

约定输出标记（stdout 最后一行）：OK / NO_WIN32 / NO_WORD / OPEN_FAILED
"""
import ctypes
import sys


def _write_word_pid(word, pid_file):
    """记下本实例 Word 的 PID，供父进程超时后精确强杀。"""
    try:
        import ctypes.wintypes

        hwnd = int(word.Hwnd)
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        with open(pid_file, "w") as fh:
            fh.write(str(pid.value))
    except Exception:
        pass  # 拿不到 PID 就不记，超时时只杀子进程


def main(src, dst, pid_file):
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        print("NO_WIN32")
        return 3
    try:
        pythoncom.CoInitialize()
        word = win32com.client.DispatchEx("Word.Application")  # 独立新实例，不复用用户已开的 Word
    except Exception:
        print("NO_WORD")
        return 4
    doc = None
    try:
        _write_word_pid(word, pid_file)
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone
        try:
            word.AutomationSecurity = 3  # msoAutomationSecurityForceDisable，禁宏
        except Exception:
            pass
        try:
            doc = word.Documents.Open(
                src, ReadOnly=True, ConfirmConversions=False,
                AddToRecentFiles=False, Visible=False,
            )
            doc.SaveAs2(dst, FileFormat=16)  # wdFormatDocumentDefault = .docx
        except Exception:
            print("OPEN_FAILED")
            return 5
        print("OK")
        return 0
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=0)
        except Exception:
            pass
        try:
            word.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
