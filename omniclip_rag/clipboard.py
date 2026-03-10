from __future__ import annotations

from .process_utils import run_hidden


# Why: Windows 的 clip.exe 读取 Unicode 时更稳定的输入格式是 UTF-16LE；
# 直接走 text=True 会退回当前控制台编码，中文 Windows 常见的 gbk 会把 ✔ 之类字符写炸。
def copy_text(text: str) -> None:
    normalized = (text or '').replace('\r\n', '\n').replace('\n', '\r\n')
    payload = normalized.encode('utf-16le')
    run_hidden(["cmd", "/u", "/c", "clip"], input=payload, check=True)


