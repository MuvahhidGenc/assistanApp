from __future__ import annotations

import os  # noqa: F401
import platform
import socket
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.powershell import run_powershell_json


class GetSystemInfoTool(BaseTool):
    name = "get_system_info"
    description = (
        "Windows sistem bilgisi: OS, CPU, RAM, bilgisayar adı, kullanıcı, domain/workgroup."
    )
    risk_level = RiskLevel.READ_ONLY
    category = "system"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        try:
            info: dict[str, Any] = {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "processor": platform.processor(),
                "architecture": platform.machine(),
            }
            try:
                import psutil

                info["cpu_count"] = psutil.cpu_count(logical=True)
                info["cpu_physical"] = psutil.cpu_count(logical=False)
                mem = psutil.virtual_memory()
                info["ram_total_gb"] = round(mem.total / 1073741824, 2)
                info["ram_used_gb"] = round(mem.used / 1073741824, 2)
                info["ram_percent"] = mem.percent
                info["boot_time"] = psutil.boot_time()
            except ImportError:
                pass
            try:
                ps_info = await run_powershell_json(
                    "Get-CimInstance Win32_OperatingSystem | Select-Object "
                    "Caption,Version,BuildNumber,OSArchitecture,LastBootUpTime,"
                    "RegisteredUser,CSName,TotalVisibleMemorySize,FreePhysicalMemory"
                )
                if isinstance(ps_info, dict):
                    info["windows"] = ps_info
                user = await run_powershell_json(
                    "Get-CimInstance Win32_ComputerSystem | Select-Object "
                    "Name,UserName,Domain,DomainRole,Manufacturer,Model"
                )
                if isinstance(user, dict):
                    info["computer"] = user
            except Exception:
                pass
            return ToolExecutionResult(success=True, output=info, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class GetDiskInfoTool(BaseTool):
    name = "get_disk_info"
    description = "Disk sürücüleri: toplam/boş alan, kullanım yüzdesi, dosya sistemi."
    risk_level = RiskLevel.READ_ONLY
    category = "system"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        try:
            disks: list[dict[str, Any]] = []
            try:
                import psutil

                for part in psutil.disk_partitions(all=False):
                    if "cdrom" in (part.opts or "").lower():
                        continue
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                    except PermissionError:
                        continue
                    disks.append(
                        {
                            "drive": part.mountpoint,
                            "fstype": part.fstype,
                            "total_gb": round(usage.total / 1073741824, 2),
                            "used_gb": round(usage.used / 1073741824, 2),
                            "free_gb": round(usage.free / 1073741824, 2),
                            "percent": usage.percent,
                        }
                    )
            except ImportError:
                data = await run_powershell_json(
                    'Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | '
                    "Select-Object DeviceID,FileSystem,Size,FreeSpace"
                )
                rows = data if isinstance(data, list) else [data] if data else []
                for d in rows:
                    if not isinstance(d, dict):
                        continue
                    size = int(d.get("Size") or 0)
                    free = int(d.get("FreeSpace") or 0)
                    used = size - free
                    disks.append(
                        {
                            "drive": d.get("DeviceID"),
                            "fstype": d.get("FileSystem"),
                            "total_gb": round(size / 1073741824, 2),
                            "used_gb": round(used / 1073741824, 2),
                            "free_gb": round(free / 1073741824, 2),
                            "percent": round((used / size) * 100, 1) if size else 0,
                        }
                    )
            return ToolExecutionResult(success=True, output={"disks": disks}, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ListInstalledProgramsTool(BaseTool):
    name = "list_installed_programs"
    description = "Kurulu programları listeler (Add/Remove Programs kayıtları)."
    risk_level = RiskLevel.READ_ONLY
    category = "software"

    async def execute(
        self, filter_name: str = "", limit: int = 50, **kwargs: Any
    ) -> ToolExecutionResult:
        try:
            limit = min(max(limit, 1), 200)
            script = (
                "$paths = @('HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
                "'HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
                "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'); "
                "Get-ItemProperty $paths -ErrorAction SilentlyContinue | "
                "Where-Object { $_.DisplayName } | "
                "Select-Object DisplayName, DisplayVersion, Publisher, InstallDate | "
                f"Select-Object -First {limit}"
            )
            if filter_name:
                safe = filter_name.replace("'", "''")
                script = script.replace(
                    "Where-Object { $_.DisplayName }",
                    f"Where-Object {{ $_.DisplayName -and $_.DisplayName -like '*{safe}*' }}",
                )
            data = await run_powershell_json(script)
            programs = data if isinstance(data, list) else [data] if data else []
            return ToolExecutionResult(
                success=True,
                output={"count": len(programs), "programs": programs},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))
