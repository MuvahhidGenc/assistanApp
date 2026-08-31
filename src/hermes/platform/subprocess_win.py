from __future__ import annotations

import subprocess
import sys


def hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if sys.platform != "win32":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return info


def hidden_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run_hidden(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    kwargs.setdefault("startupinfo", hidden_startupinfo())
    kwargs["creationflags"] = kwargs.get("creationflags", 0) | hidden_creationflags()
    return subprocess.run(args, **kwargs)
