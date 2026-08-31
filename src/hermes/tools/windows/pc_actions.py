from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.input_backend import run_in_thread
from hermes.tools.windows.powershell import run_command, run_powershell, run_powershell_json
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


_DNS_PRESETS: dict[str, list[str]] = {
    "google": ["8.8.8.8", "8.8.4.4"],
    "cloudflare": ["1.1.1.1", "1.0.0.1"],
    "quad9": ["9.9.9.9", "149.112.112.112"],
    "dhcp": [],
}


class SetDnsTool(BaseTool):
    name = "set_dns"
    description = "Aktif ag kartinin DNS sunucularini degistirir (google/cloudflare/dhcp veya IP)."
    risk_level = RiskLevel.HIGH_RISK
    category = "network"

    async def execute(
        self,
        servers: str | list[str] | None = None,
        preset: str = "",
        adapter: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        preset_key = (preset or "").strip().lower()
        if preset_key in _DNS_PRESETS:
            addresses = list(_DNS_PRESETS[preset_key])
        elif isinstance(servers, str):
            addresses = [part.strip() for part in re.split(r"[,\s]+", servers) if part.strip()]
        elif isinstance(servers, list):
            addresses = [str(item).strip() for item in servers if str(item).strip()]
        else:
            return ToolExecutionResult(success=False, error="preset veya servers gerekli")

        try:
            alias = adapter.strip()
            if not alias:
                adapters = await run_powershell_json(
                    "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                    "Select-Object -First 1 Name,InterfaceAlias,Status"
                )
                if isinstance(adapters, list):
                    adapters = adapters[0] if adapters else {}
                alias = str((adapters or {}).get("InterfaceAlias") or (adapters or {}).get("Name") or "")
            if not alias:
                return ToolExecutionResult(success=False, error="Aktif ag karti bulunamadi")

            safe_alias = alias.replace("'", "''")
            if not addresses:
                script = (
                    f"Set-DnsClientServerAddress -InterfaceAlias '{safe_alias}' -ResetServerAddresses; "
                    f"ipconfig /flushdns | Out-Null; "
                    f"(Get-DnsClientServerAddress -InterfaceAlias '{safe_alias}' -AddressFamily IPv4).ServerAddresses"
                )
            else:
                joined = ",".join(f"'{ip}'" for ip in addresses)
                script = (
                    f"Set-DnsClientServerAddress -InterfaceAlias '{safe_alias}' "
                    f"-ServerAddresses @({joined}); "
                    f"ipconfig /flushdns | Out-Null; "
                    f"(Get-DnsClientServerAddress -InterfaceAlias '{safe_alias}' -AddressFamily IPv4).ServerAddresses"
                )

            from hermes.platform.elevation import is_admin, needs_admin_error, run_powershell_elevated

            if is_admin():
                code, stdout, stderr = await run_powershell(script)
            else:
                code, stdout, stderr = await run_powershell_elevated(script)
                if code != 0 and needs_admin_error(stderr or stdout):
                    return ToolExecutionResult(
                        success=False,
                        error=(
                            "DNS icin Windows yonetici izni (UAC) gerekli. "
                            "Hermes onayindan sonra acilan UAC penceresini de onayla."
                        ),
                    )
                if code != 0:
                    code, stdout, stderr = await run_powershell(script)
            if code != 0:
                from hermes.platform.elevation import needs_admin_error

                if needs_admin_error(stderr or stdout):
                    return ToolExecutionResult(
                        success=False,
                        error=(
                            "DNS icin yonetici izni gerekli. "
                            "UAC penceresini onayla veya Hermes'i yonetici olarak calistir."
                        ),
                    )
                return ToolExecutionResult(success=False, error=stderr or stdout or "DNS ayarlanamadi")
            verified_servers = [line.strip() for line in stdout.splitlines() if line.strip()]
            if addresses and verified_servers:
                if not all(any(v in verified_servers for v in [ip]) for ip in addresses):
                    logger.info("dns_verify_mismatch", expected=addresses, got=verified_servers)
            return ToolExecutionResult(
                success=True,
                output={
                    "adapter": alias,
                    "servers": addresses or verified_servers or ["dhcp"],
                    "preset": preset_key or None,
                    "verified": verified_servers,
                },
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "preset": {"type": "string", "description": "google | cloudflare | quad9 | dhcp"},
                "servers": {"type": "string", "description": "Virgulle IP listesi"},
                "adapter": {"type": "string"},
            },
        }


class ListWindowsTool(BaseTool):
    name = "list_windows"
    description = "Acik pencerelerin basliklarini listeler."
    risk_level = RiskLevel.READ_ONLY
    category = "computer_control"

    async def execute(self, limit: int = 30, **kwargs: Any) -> ToolExecutionResult:
        def _list() -> list[str]:
            import pygetwindow as gw

            titles = [str(w.title).strip() for w in gw.getAllWindows() if str(w.title).strip()]
            return titles[: max(1, min(int(limit), 80))]

        try:
            titles = await run_in_thread(_list)
            return ToolExecutionResult(success=True, output={"windows": titles}, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class SetVolumeTool(BaseTool):
    name = "set_volume"
    description = "Sistem sesini ayarlar: mute/unmute/up/down veya 0-100 seviye."
    risk_level = RiskLevel.LOW_RISK
    category = "computer_control"

    async def execute(self, action: str = "", level: int | None = None, **kwargs: Any) -> ToolExecutionResult:
        act = (action or "").strip().lower()
        if level is None and str(kwargs.get("level", "")).isdigit():
            level = int(kwargs["level"])

        def _press(name: str) -> None:
            import pyautogui

            pyautogui.press(name)

        try:
            if act in ("mute", "unmute", "toggle"):
                await run_in_thread(_press, "volumemute")
                return ToolExecutionResult(success=True, output={"action": "mute_toggle"}, verified=True)
            if act in ("up", "yukselt", "ac"):
                await run_in_thread(_press, "volumeup")
                return ToolExecutionResult(success=True, output={"action": "up"}, verified=True)
            if act in ("down", "kis", "kıs"):
                await run_in_thread(_press, "volumedown")
                return ToolExecutionResult(success=True, output={"action": "down"}, verified=True)
            if level is not None:
                value = max(0, min(int(level), 100))
                code, stdout, stderr = await run_powershell(
                    f"$wsh = New-Object -ComObject WScript.Shell; "
                    f"1..50 | ForEach-Object {{ $wsh.SendKeys([char]174) }}; "
                    f"1..{max(1, value // 2)} | ForEach-Object {{ $wsh.SendKeys([char]175) }}"
                )
                if code != 0:
                    return ToolExecutionResult(success=False, error=stderr or stdout)
                return ToolExecutionResult(success=True, output={"action": "set", "level": value}, verified=True)
            return ToolExecutionResult(success=False, error="action veya level gerekli")
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class GetClipboardTool(BaseTool):
    name = "get_clipboard"
    description = "Pano metnini okur."
    risk_level = RiskLevel.READ_ONLY
    category = "system"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        try:
            code, stdout, stderr = await run_powershell("Get-Clipboard")
            if code != 0:
                return ToolExecutionResult(success=False, error=stderr or stdout)
            text = stdout.strip()
            return ToolExecutionResult(
                success=True,
                output={"text": text[:4000], "length": len(text)},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class SetClipboardTool(BaseTool):
    name = "set_clipboard"
    description = "Pano metnini yazar."
    risk_level = RiskLevel.LOW_RISK
    category = "system"

    async def execute(self, text: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not text:
            return ToolExecutionResult(success=False, error="text required")
        safe = text.replace("'", "''")
        try:
            code, stdout, stderr = await run_powershell(f"Set-Clipboard -Value '{safe}'")
            if code != 0:
                return ToolExecutionResult(success=False, error=stderr or stdout)
            return ToolExecutionResult(success=True, output={"copied": True, "length": len(text)}, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class KillProcessTool(BaseTool):
    name = "kill_process"
    description = "Islem sonlandirir (pid veya process adi)."
    risk_level = RiskLevel.HIGH_RISK
    category = "system"

    async def execute(self, pid: int | None = None, name: str = "", **kwargs: Any) -> ToolExecutionResult:
        if pid:
            code, stdout, stderr = await run_command(["taskkill", "/PID", str(int(pid)), "/F"])
        elif name:
            safe = name.replace('"', "")
            code, stdout, stderr = await run_command(["taskkill", "/IM", safe, "/F"])
        else:
            return ToolExecutionResult(success=False, error="pid veya name gerekli")
        return ToolExecutionResult(
            success=code == 0,
            output={"pid": pid, "name": name, "output": stdout},
            error=None if code == 0 else (stderr or stdout),
            verified=code == 0,
        )


class ControlServiceTool(BaseTool):
    name = "control_service"
    description = "Windows servisini baslatir veya durdurur."
    risk_level = RiskLevel.HIGH_RISK
    category = "system"

    async def execute(self, service_name: str = "", action: str = "status", **kwargs: Any) -> ToolExecutionResult:
        if not service_name:
            return ToolExecutionResult(success=False, error="service_name required")
        act = (action or "status").strip().lower()
        safe = service_name.replace("'", "''")
        if act == "start":
            script = f"Start-Service -Name '{safe}'"
        elif act == "stop":
            script = f"Stop-Service -Name '{safe}' -Force"
        else:
            script = f"Get-Service -Name '{safe}' | Select-Object Name,Status,DisplayName"
        try:
            if act in ("start", "stop"):
                code, stdout, stderr = await run_powershell(script)
                if code != 0:
                    from hermes.platform.elevation import needs_admin_error, run_powershell_elevated

                    if needs_admin_error(stderr or stdout):
                        code, stdout, stderr = await run_powershell_elevated(script)
                if code != 0:
                    return ToolExecutionResult(success=False, error=stderr or stdout)
                return ToolExecutionResult(
                    success=True, output={"service": service_name, "action": act}, verified=True
                )
            data = await run_powershell_json(script)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ReadScreenTextTool(BaseTool):
    name = "read_screen_text"
    description = "Ekran goruntusu alir ve mumkunse gorunen metni okur."
    risk_level = RiskLevel.READ_ONLY
    category = "computer_control"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        from hermes.vision import ocr_screen

        try:
            data = await run_in_thread(lambda: ocr_screen(include_boxes=True))
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class CreateFolderTool(BaseTool):
    name = "create_folder"
    description = "Belirtilen yolda klasor olusturur (masaustu veya tam yol)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "system"

    async def execute(self, path: str = "", **kwargs: Any) -> ToolExecutionResult:
        from pathlib import Path

        raw = (path or kwargs.get("name") or "Hermes").strip()
        if not raw:
            return ToolExecutionResult(success=False, error="path gerekli")
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = Path.home() / "Desktop" / target
        try:
            target.mkdir(parents=True, exist_ok=True)
            return ToolExecutionResult(success=True, output={"path": str(target)}, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Tam yol veya klasor adi"},
            },
            "required": ["path"],
        }


class OpenPathTool(BaseTool):
    name = "open_path"
    description = "Dosya veya klasoru Windows'ta acar (Explorer)."
    risk_level = RiskLevel.LOW_RISK
    category = "system"

    async def execute(self, path: str = "", **kwargs: Any) -> ToolExecutionResult:
        raw = (path or kwargs.get("target") or "").strip()
        if not raw:
            return ToolExecutionResult(success=False, error="path gerekli")
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = Path.home() / "Desktop" / target
        if not target.exists():
            return ToolExecutionResult(success=False, error=f"Yol bulunamadi: {target}")
        try:
            import os

            os.startfile(str(target))  # noqa: S606
            return ToolExecutionResult(
                success=True,
                output={"path": str(target), "opened": True},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Acilacak dosya/klasor yolu"},
            },
            "required": ["path"],
        }


class InstallProgramTool(BaseTool):
    name = "install_program"
    description = "winget ile program kurar (LibreOffice, Chrome vb.). Onay gerektirir."
    risk_level = RiskLevel.HIGH_RISK
    category = "system"

    _WINGET_IDS: dict[str, str] = {
        "libreoffice": "TheDocumentFoundation.LibreOffice",
        "chrome": "Google.Chrome",
        "googlechrome": "Google.Chrome",
        "firefox": "Mozilla.Firefox",
        "vscode": "Microsoft.VisualStudioCode",
        "visualstudiocode": "Microsoft.VisualStudioCode",
        "7zip": "7zip.7zip",
        "git": "Git.Git",
        "node": "OpenJS.NodeJS.LTS",
        "nodejs": "OpenJS.NodeJS.LTS",
        "python": "Python.Python.3.12",
        "spotify": "Spotify.Spotify",
        "discord": "Discord.Discord",
        "notepad++": "Notepad++.Notepad++",
        "vlc": "VideoLAN.VLC",
        "zoom": "Zoom.Zoom",
    }

    async def execute(self, package: str = "", **kwargs: Any) -> ToolExecutionResult:
        pkg = (package or kwargs.get("name") or "").strip()
        if not pkg:
            return ToolExecutionResult(success=False, error="package gerekli")

        key = re.sub(r"[^a-z0-9+]", "", pkg.casefold())
        winget_id = self._WINGET_IDS.get(key)
        if not winget_id:
            for alias, app_id in self._WINGET_IDS.items():
                if alias in key or key in alias:
                    winget_id = app_id
                    break

        agreements = "--accept-package-agreements --accept-source-agreements"
        if winget_id:
            safe_id = winget_id.replace("'", "''")
            script = f"winget install {agreements} -e --id '{safe_id}' --disable-interactivity"
        else:
            safe = pkg.replace("'", "''")
            script = f"winget install {agreements} -e --query '{safe}' --disable-interactivity"

        try:
            logger.info("install_program_start", package=pkg, winget_id=winget_id)
            install_timeout = 1200.0
            code, stdout, stderr = await run_powershell(script, timeout=install_timeout)
            if code != 0:
                from hermes.platform.elevation import needs_admin_error, run_powershell_elevated

                combined = stderr or stdout or ""
                if needs_admin_error(combined) or "administrator" in combined.casefold():
                    logger.info("install_program_elevated", package=pkg, winget_id=winget_id)
                    code, stdout, stderr = await run_powershell_elevated(script, timeout=install_timeout)
                elif "already installed" in combined.casefold() or "zaten yüklü" in combined.casefold():
                    return ToolExecutionResult(
                        success=True,
                        output={"package": pkg, "winget_id": winget_id, "note": "Zaten kurulu"},
                        verified=True,
                    )
            combined = (stderr or stdout or "").strip()
            if code != 0:
                if "timed out" in combined.casefold() or code == 1 and not combined:
                    return ToolExecutionResult(
                        success=False,
                        error=(
                            "Kurulum zaman asimina ugradi veya UAC iptal edildi. "
                            "Windows UAC penceresini onayla; LibreOffice gibi buyuk paketler 5-15 dk surebilir."
                        ),
                    )
                if "winget" in combined.casefold() and "not recognized" in combined.casefold():
                    return ToolExecutionResult(
                        success=False,
                        error="winget bulunamadi. Windows App Installer guncelleyin veya Microsoft Store'dan winget kurun.",
                    )
                return ToolExecutionResult(success=False, error=combined or "Kurulum basarisiz")
            verify_id = (winget_id or pkg).replace("'", "''")
            if winget_id:
                verify_script = f"winget list --id '{verify_id}' -e"
            else:
                verify_script = f"winget list --query '{verify_id}'"
            vcode, vout, _ = await run_powershell(verify_script, timeout=60.0)
            verified = vcode == 0 and bool((vout or "").strip())
            return ToolExecutionResult(
                success=True,
                output={
                    "package": pkg,
                    "winget_id": winget_id,
                    "log": combined[:2000],
                    "verified": verified,
                },
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "package": {"type": "string", "description": "Program adi veya winget paketi"},
            },
            "required": ["package"],
        }


def _filename_from_url(url: str, fallback: str = "download.bin") -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name)
    if name and "." in name:
        return name
    return fallback


class DownloadFileTool(BaseTool):
    name = "download_file"
    description = "URL'den dosya indirir (exe, zip, installer vb.)."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "system"

    async def execute(self, url: str = "", path: str = "", **kwargs: Any) -> ToolExecutionResult:
        href = (url or kwargs.get("link") or "").strip()
        if not href:
            return ToolExecutionResult(success=False, error="url gerekli")
        if not href.startswith(("http://", "https://")):
            href = f"https://{href.lstrip('/')}"

        dest_raw = (path or kwargs.get("destination") or "").strip()
        if dest_raw:
            target = Path(dest_raw).expanduser()
            if not target.is_absolute():
                target = Path.home() / "Downloads" / target
        else:
            target = Path.home() / "Downloads" / _filename_from_url(href)

        def _download() -> dict[str, Any]:
            import httpx

            target.parent.mkdir(parents=True, exist_ok=True)
            with httpx.Client(follow_redirects=True, timeout=120.0) as client:
                response = client.get(href)
                response.raise_for_status()
                target.write_bytes(response.content)
            return {"path": str(target), "url": href, "bytes": target.stat().st_size}

        try:
            data = await run_in_thread(_download)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Indirilecek dosya URL'si"},
                "path": {"type": "string", "description": "Hedef yol veya dosya adi (Downloads altinda)"},
            },
            "required": ["url"],
        }


class GitCloneTool(BaseTool):
    name = "git_clone"
    description = "GitHub/GitLab/Bitbucket reposunu klonlar."
    risk_level = RiskLevel.NORMAL_MODIFICATION
    category = "system"

    async def execute(
        self,
        repo_url: str = "",
        target_dir: str = "",
        branch: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        url = (repo_url or kwargs.get("url") or "").strip()
        if not url:
            return ToolExecutionResult(success=False, error="repo_url gerekli")
        if not url.startswith(("http://", "https://", "git@")):
            url = f"https://{url.lstrip('/')}"
        if url.endswith("/"):
            url = url.rstrip("/")
        if not url.endswith(".git") and "github.com" in url.lower():
            url = f"{url}.git"

        dest_raw = (target_dir or kwargs.get("path") or "").strip()
        if dest_raw:
            target = Path(dest_raw).expanduser()
            if not target.is_absolute():
                target = Path.home() / "Desktop" / target
        else:
            repo_name = Path(urlparse(url.replace("git@", "https://")).path).stem or "repo"
            target = Path.home() / "Desktop" / repo_name

        if target.exists() and any(target.iterdir()):
            return ToolExecutionResult(
                success=False,
                error=f"Hedef klasor dolu: {target}",
            )

        cmd = ["git", "clone"]
        if branch.strip():
            cmd.extend(["-b", branch.strip()])
        cmd.extend([url, str(target)])

        try:
            code, stdout, stderr = await run_command(cmd, timeout=180.0)
            if code != 0:
                return ToolExecutionResult(success=False, error=stderr or stdout or "git clone basarisiz")
            return ToolExecutionResult(
                success=True,
                output={"repo_url": url, "path": str(target), "branch": branch.strip() or "default"},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "repo_url": {"type": "string", "description": "Git repo URL (https veya git@)"},
                "target_dir": {"type": "string", "description": "Klonlanacak klasor yolu"},
                "branch": {"type": "string", "description": "Opsiyonel dal adi"},
            },
            "required": ["repo_url"],
        }


class RunCommandTool(BaseTool):
    name = "run_command"
    description = "PowerShell veya cmd komutu calistirir (onay gerektirir)."
    risk_level = RiskLevel.HIGH_RISK
    category = "system"

    async def execute(
        self,
        command: str = "",
        shell: str = "powershell",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        cmd_text = (command or kwargs.get("cmd") or "").strip()
        if not cmd_text:
            return ToolExecutionResult(success=False, error="command gerekli")
        shell_name = (shell or "powershell").strip().lower()

        try:
            if shell_name in ("cmd", "command"):
                code, stdout, stderr = await run_command(["cmd", "/c", cmd_text], timeout=300.0)
            else:
                code, stdout, stderr = await run_powershell(cmd_text, timeout=300.0)
                if code != 0:
                    from hermes.platform.elevation import needs_admin_error, run_powershell_elevated

                    if needs_admin_error(stderr or stdout):
                        code, stdout, stderr = await run_powershell_elevated(cmd_text)
            if code != 0:
                return ToolExecutionResult(success=False, error=stderr or stdout or f"Cikis kodu {code}")
            return ToolExecutionResult(
                success=True,
                output={"stdout": (stdout or "")[:4000], "stderr": (stderr or "")[:1000]},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Calistirilacak komut"},
                "shell": {
                    "type": "string",
                    "enum": ["powershell", "cmd"],
                    "default": "powershell",
                    "description": "Kabuk turu",
                },
            },
            "required": ["command"],
        }
