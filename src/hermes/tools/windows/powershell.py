from __future__ import annotations

import asyncio
import json
from typing import Any


async def run_powershell(script: str, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run PowerShell script and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return (
            124,
            "",
            f"Command timed out after {timeout:.0f}s",
        )
    return (
        proc.returncode if proc.returncode is not None else 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def run_powershell_json(script: str, timeout: float = 60.0) -> Any:
    """Run PowerShell and parse JSON output."""
    wrapped = f"({script}) | ConvertTo-Json -Depth 6 -Compress"
    code, stdout, stderr = await run_powershell(wrapped, timeout=timeout)
    if code != 0:
        raise RuntimeError(f"PowerShell exit {code}: {stderr or stdout}")
    text = stdout.strip()
    if not text:
        return None
    return json.loads(text)


async def run_command(args: list[str], timeout: float = 60.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return (
            124,
            "",
            f"Command timed out after {timeout:.0f}s",
        )
    return (
        proc.returncode if proc.returncode is not None else 0,
        stdout.decode("cp857", errors="replace"),
        stderr.decode("cp857", errors="replace"),
    )
