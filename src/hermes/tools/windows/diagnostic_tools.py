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
        self, log_name: str = "System", count: int = 20, level: str = "", **kwargs: Any
    ) -> ToolExecutionResult:
        try:
            count = min(max(count, 1), 100)
            safe_log = log_name.replace("'", "''")
            filter_level = ""
            level_map = {"critical": 1, "error": 2, "warning": 3, "info": 4}
            lvl = level_map.get(level.lower()) if level else None
            if lvl:
                filter_level = f" | Where-Object {{ $_.Level -le {lvl} }}"
            script = (
                f"Get-WinEvent -LogName '{safe_log}' -MaxEvents {count}"
                f" -ErrorAction Stop |{filter_level} | Select-Object "
                "TimeCreated,Id,LevelDisplayName,ProviderName,Message"
            )
            data = await run_powershell_json(script)
            events: list[Any] = []
            if isinstance(data, dict):
                data = [data]
            if isinstance(data, list):
                for evt in data:
                    if isinstance(evt, dict) and evt.get("Message") is not None:
                        evt["Message"] = str(evt["Message"])[:500]
                    events.append(evt)
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

    async def execute(self, path: str = "", name: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not path:
            return ToolExecutionResult(success=False, error="path required")
        try:
            safe_path = path.replace("'", "''")
            safe_name = name.replace("'", "''")
            if safe_name:
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
