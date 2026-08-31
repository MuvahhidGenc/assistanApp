from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.file_tools import resolve_user_path
from hermes.tools.windows.powershell import run_command, run_powershell_json


_DNS_PRESETS: dict[str, list[str]] = {
    "google": ["8.8.8.8", "8.8.4.4"],
    "cloudflare": ["1.1.1.1", "1.0.0.1"],
    "opendns": ["208.67.222.222", "208.67.220.220"],
}


class SetDnsTool(BaseTool):
    name = "set_dns"
    description = "Aktif ag adaptorunun DNS sunucularini degistirir."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "network"

    async def execute(
        self,
        preset: str = "",
        servers: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        try:
            dns_servers = list(servers or [])
            if not dns_servers and preset:
                dns_servers = _DNS_PRESETS.get(preset.lower(), [])
            if not dns_servers:
                dns_servers = _DNS_PRESETS["google"]

            adapter_data = await run_powershell_json(
                "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                "Select-Object -First 1 Name,InterfaceAlias"
            )
            adapter = "Wi-Fi"
            if isinstance(adapter_data, dict):
                adapter = adapter_data.get("InterfaceAlias") or adapter_data.get("Name") or adapter

            safe_adapter = str(adapter).replace("'", "''")
            servers_ps = ",".join(f"'{s}'" for s in dns_servers)
            await run_powershell_json(
                f"Set-DnsClientServerAddress -InterfaceAlias '{safe_adapter}' "
                f"-ServerAddresses @({servers_ps})"
            )
            return ToolExecutionResult(
                success=True,
                output={"adapter": adapter, "servers": dns_servers, "preset": preset or None},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ReadScreenTextTool(BaseTool):
    name = "read_screen_text"
    description = "Ekran goruntusunden OCR ile metin okur."
    risk_level = RiskLevel.READ_ONLY
    category = "computer_control"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        try:
            from hermes.vision import ocr_screen

            data = await asyncio.to_thread(ocr_screen)
            text = data.get("text", "")
            return ToolExecutionResult(
                success=bool(data.get("ocr")),
                output={"text": text, "ocr": data.get("ocr", False)},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class DownloadFileTool(BaseTool):
    name = "download_file"
    description = "URL'den dosya indirir."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "network"

    async def execute(
        self,
        url: str = "",
        path: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        if not url:
            return ToolExecutionResult(success=False, error="url required")
        try:
            import httpx

            target = resolve_user_path(path) if path else Path(tempfile.gettempdir()) / "download.bin"
            target.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.get(url)
                response.raise_for_status()
                target.write_bytes(response.content)
            return ToolExecutionResult(
                success=True,
                output={"url": url, "path": str(target), "size": target.stat().st_size},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class GitCloneTool(BaseTool):
    name = "git_clone"
    description = "Git deposunu klonlar."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "network"

    async def execute(
        self,
        url: str = "",
        path: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        if not url:
            return ToolExecutionResult(success=False, error="url required")
        try:
            target = resolve_user_path(path) if path else Path.cwd() / "repo"
            target.parent.mkdir(parents=True, exist_ok=True)
            code, stdout, stderr = await run_command(
                ["git", "clone", url, str(target)],
            )
            return ToolExecutionResult(
                success=code == 0,
                output={"url": url, "path": str(target), "stdout": stdout, "stderr": stderr},
                verified=code == 0,
                error=stderr if code != 0 else None,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class InstallProgramTool(BaseTool):
    name = "install_program"
    description = "Paket yoneticisi ile program kurar."
    risk_level = RiskLevel.HIGH_RISK
    category = "system"

    async def execute(self, package: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not package:
            return ToolExecutionResult(success=False, error="package required")
        winget_ids = {
            "chrome": "Google.Chrome",
            "firefox": "Mozilla.Firefox",
            "libreoffice": "TheDocumentFoundation.LibreOffice",
            "vscode": "Microsoft.VisualStudioCode",
        }
        winget_id = winget_ids.get(package.lower(), package)
        try:
            code, stdout, stderr = await run_command(
                ["winget", "install", "--id", winget_id, "-e", "--accept-package-agreements"],
            )
            return ToolExecutionResult(
                success=code == 0,
                output={"package": package, "winget_id": winget_id, "stdout": stdout},
                verified=code == 0,
                error=stderr if code != 0 else None,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))
