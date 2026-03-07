from __future__ import annotations

import subprocess


def copy_text(text: str) -> None:
    subprocess.run(["cmd", "/c", "clip"], input=text, text=True, check=True)
