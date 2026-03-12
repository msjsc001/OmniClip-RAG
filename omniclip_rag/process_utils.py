from __future__ import annotations

import os
import subprocess
from typing import Any


def hidden_subprocess_kwargs() -> dict[str, Any]:
    if os.name != 'nt':
        return {}
    return {
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000),
    }


def run_hidden(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    options = hidden_subprocess_kwargs()
    options.update(kwargs)
    
    capture_output = options.pop('capture_output', False)
    text_mode = options.pop('text', False)
    timeout = options.pop('timeout', None)
    check = options.pop('check', False)
    
    if capture_output:
        options['stdout'] = subprocess.PIPE
        options['stderr'] = subprocess.PIPE

    with subprocess.Popen(*popenargs, **options) as process:
        try:
            # We avoid process.communicate() here because on Windows it spawns background
            # _readerthreads. When called from a daemon thread with an initialized COM
            # apartment, spawning new threads for pipes can cause a fatal access violation
            # (RPC_E_WRONG_THREAD). We thus read synchronously.
            stdout_data, stderr_data = None, None
            if capture_output:
                stdout_raw = process.stdout.read() if process.stdout else b''
                stderr_raw = process.stderr.read() if process.stderr else b''
                if text_mode:
                    stdout_data = stdout_raw.decode('utf-8', errors='replace')
                    stderr_data = stderr_raw.decode('utf-8', errors='replace')
                else:
                    stdout_data, stderr_data = stdout_raw, stderr_raw

            retcode = process.wait(timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            if capture_output:
                exc.stdout = stdout_data
                exc.stderr = stderr_data
            process.wait()
            raise
        except Exception:
            process.kill()
            process.wait()
            raise

    if check and retcode:
        raise subprocess.CalledProcessError(
            retcode, process.args, output=stdout_data, stderr=stderr_data
        )

    return subprocess.CompletedProcess(
        process.args, retcode, stdout_data, stderr_data
    )
