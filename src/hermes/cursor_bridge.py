from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


def find_cursor_executable() -> Path | None:
    env_path = os.environ.get("CURSOR_PATH", "").strip()
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor" / "Cursor.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Cursor" / "Cursor.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Cursor" / "Cursor.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def cursor_status() -> dict[str, Any]:
    path = find_cursor_executable()
    return {"available": path is not None, "path": str(path) if path else None}


def run_cursor_cli(args: list[str]) -> dict[str, Any]:
    exe = find_cursor_executable()
    if exe is None:
        return {"ok": False, "error": "Cursor bulunamadi. CURSOR_PATH ortam degiskenini ayarlayin."}
    try:
        result = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
