from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


def find_cursor_executable() -> Path | None:
    env = os.environ.get("CURSOR_PATH", "").strip()
    candidates: list[str] = []
    if env:
        candidates.append(env)
    which = shutil.which("cursor") or shutil.which("cursor.cmd")
    if which:
        candidates.append(which)
    local = os.environ.get("LOCALAPPDATA", "")
    candidates.extend(
        [
            str(Path(local) / "Programs" / "cursor" / "Cursor.exe") if local else "",
            r"C:\Program Files\Cursor\Cursor.exe",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def cursor_status() -> dict[str, Any]:
    exe = find_cursor_executable()
    return {
        "available": exe is not None,
        "path": str(exe) if exe else "",
        "version": cursor_version(exe) if exe else "",
    }


def cursor_version(exe: Path | None = None) -> str:
    binary = exe or find_cursor_executable()
    if binary is None:
        return ""
    result = run_cursor_cli(["--version"], timeout=8, executable=binary)
    return str(result.get("stdout") or "").strip()


def open_in_cursor(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.exists():
        return {"ok": False, "error": f"Yol bulunamadi: {target}"}
    return run_cursor_cli([str(target)], timeout=15)


def run_cursor_cli(
    args: list[str],
    *,
    timeout: float = 20,
    executable: Path | None = None,
) -> dict[str, Any]:
    binary = executable or find_cursor_executable()
    if binary is None:
        return {"ok": False, "error": "Cursor CLI bulunamadi"}
    try:
        completed = subprocess.run(
            [str(binary), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Cursor komutu zaman asimi"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": completed.returncode == 0,
        "code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
