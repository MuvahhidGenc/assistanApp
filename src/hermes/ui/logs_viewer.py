from __future__ import annotations

import os
import sys
from pathlib import Path

from hermes.config.paths import app_log_path


def read_log_tail(path: Path | None = None, *, max_lines: int = 200) -> tuple[list[str], str | None]:
    log_path = path or app_log_path()
    if not log_path.exists():
        return [], "Log dosyasi henuz olusturulmadi. Uygulama calistiktan sonra tekrar deneyin."

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], f"Log dosyasi okunamadi: {exc}"

    lines = text.splitlines()
    if not lines:
        return [], "Log dosyasi bos."

    return lines[-max_lines:], None


def open_log_file(path: Path | None = None) -> tuple[bool, str]:
    log_path = path or app_log_path()
    if not log_path.exists():
        return False, "Log dosyasi bulunamadi. Uygulama henuz log yazmamis olabilir."

    try:
        if sys.platform == "win32":
            os.startfile(log_path)  # noqa: S606
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(log_path)])  # noqa: S603
        return True, str(log_path)
    except OSError as exc:
        return False, f"Log dosyasi acilamadi: {exc}"
