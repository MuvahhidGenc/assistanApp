from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.powershell import run_powershell_json


class QueryEventLogTool(BaseTool):
    name = "query_event_log"
    description = "Windows Event Log kayıtlarını okur (System/Application)."
    risk_level = RiskLevel.READ_ONLY
    category = "diagnostic"

    async def execute(
        self,
        log_name: str = "System",
        count: int = 20,
        level: str = "",
        **kwargs: Any,
    ) -> ToolExecutionResult:
        try:
            count = min(max(count, 1), 100)
            safe_log = log_name.replace("'", "''")
            filter_level = ""
            if level:
                # 1=Critical 2=Error 3=Warning 4=Info
                level_map = {
                    "critical": 1,
                    "error": 2,
                    "warning": 3,
                    "info": 4,
                }
                lvl = level_map.get(level.lower())
                if lvl:
                    filter_level = f" | Where-Object {{ $_.Level -le {lvl} }}"

            data = await run_powershell_json(
                f"Get-WinEvent -LogName '{safe_log}' -MaxEvents {count} -ErrorAction Stop | "
                f"{filter_level} | "
                "Select-Object TimeCreated,Id,LevelDisplayName,ProviderName,Message"
            )
            events = data if isinstance(data, list) else ([data] if data else [])
            for evt in events:
                if isinstance(evt, dict) and evt.get("Message"):
                    evt["Message"] = str(evt["Message"])[:500]
            return ToolExecutionResult(
                success=True,
                output={"log": log_name, "count": len(events), "events": events},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ReadRegistryTool(BaseTool):
    name = "read_registry"
    description = "Windows registry değerini salt okunur okur."
    risk_level = RiskLevel.READ_ONLY
    category = "registry"

    async def execute(self, path: str, name: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not path:
            return ToolExecutionResult(success=False, error="path required")
        try:
            safe_path = path.replace("'", "''")
            if name:
                safe_name = name.replace("'", "''")
                script = (
                    f"Get-ItemProperty -Path '{safe_path}' -Name '{safe_name}' "
                    "-ErrorAction Stop | Select-Object *"
                )
            else:
                script = f"Get-ItemProperty -Path '{safe_path}' -ErrorAction Stop | Select-Object *"

            data = await run_powershell_json(script)
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))
