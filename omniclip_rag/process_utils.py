from __future__ import annotations

import os
import subprocess
from typing import Any


def hidden_subprocess_kwargs() -> dict[str, Any]:
    if os.name != 'nt':
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        'startupinfo': startupinfo,
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    }


def run_hidden(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    options = hidden_subprocess_kwargs()
    options.update(kwargs)
    return subprocess.run(*popenargs, **options)
