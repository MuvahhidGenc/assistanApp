"""Windows elevation helpers."""

from __future__ import annotations

import ctypes
import sys
import tempfile
from pathlib import Path


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Restart this process elevated. Returns True if a new process was started."""
    if is_admin():
        return False
    exe = sys.executable
    args = " ".join(f'"{arg}"' for arg in sys.argv[1:])
    params = args if args else ""
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return int(rc) > 32
    except Exception:
        return False


def ensure_admin_or_exit() -> None:
    if is_admin():
        return
    if relaunch_as_admin():
        raise SystemExit(0)


def needs_admin_error(text: str) -> bool:
    blob = (text or "").casefold()
    return any(
        token in blob
        for token in (
            "access is denied",
            "access denied",
            "erişim engellendi",
            "erisim engellendi",
            "yetkisiz",
            "unauthorized",
            "requires elevation",
            "yükselt",
            "yukselt",
            "administrator",
            "yönetici",
            "yonetici",
            "run as administrator",
        )
    )


async def run_powershell_elevated(script: str, timeout: float = 90.0) -> tuple[int, str, str]:
    """Show Windows UAC, then run the script as administrator."""
    from hermes.tools.windows.powershell import run_powershell

    exit_file = Path(tempfile.mktemp(suffix=".exit"))
    out_file = Path(tempfile.mktemp(suffix=".out"))
    ps1 = Path(tempfile.mktemp(suffix=".ps1"))
    try:
        body = f"""
$ErrorActionPreference = 'Stop'
try {{
{script}
    'OK' | Out-File -FilePath '{out_file}' -Encoding utf8
    0 | Out-File -FilePath '{exit_file}' -Encoding ascii -NoNewline
}} catch {{
    $_.Exception.Message | Out-File -FilePath '{out_file}' -Encoding utf8
    1 | Out-File -FilePath '{exit_file}' -Encoding ascii -NoNewline
}}
"""
        ps1.write_text(body, encoding="utf-8")
        safe = str(ps1).replace("'", "''")
        wrapper = (
            f"$p = Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru "
            f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','{safe}'; "
            "if ($null -eq $p) { exit 1 }; exit $p.ExitCode"
        )
        code, stdout, stderr = await run_powershell(wrapper, timeout=timeout)
        if exit_file.exists():
            try:
                code = int(exit_file.read_text(encoding="ascii").strip() or code)
            except ValueError:
                pass
        if out_file.exists():
            file_out = out_file.read_text(encoding="utf-8", errors="replace").strip()
            if file_out:
                stdout = file_out
        return code, stdout, stderr
    finally:
        ps1.unlink(missing_ok=True)
        exit_file.unlink(missing_ok=True)
        out_file.unlink(missing_ok=True)
