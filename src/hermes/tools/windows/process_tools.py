from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.powershell import run_powershell_json


class ListProcessesTool(BaseTool):
    name = "list_processes"
    description = "Çalışan işlemleri CPU ve bellek kullanımına göre listeler."
    risk_level = RiskLevel.READ_ONLY
    category = "process"

    async def execute(self, limit: int = 20, sort_by: str = "cpu", **kwargs: Any) -> ToolExecutionResult:
        try:
            limit = min(max(limit, 1), 100)
            sort_prop = "CPU" if sort_by.lower() == "cpu" else "WorkingSet"
            data = await run_powershell_json(
                f"Get-Process | Sort-Object {sort_prop} -Descending | "
                f"Select-Object -First {limit} Id,ProcessName,CPU,"
                "@{N='MemoryMB';E={[math]::Round($_.WorkingSet64/1MB,1)}},Path"
            )
            processes = data if isinstance(data, list) else ([data] if data else [])
            return ToolExecutionResult(
                success=True,
                output={"count": len(processes), "processes": processes},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class ListServicesTool(BaseTool):
    name = "list_services"
    description = "Windows servislerini durumlarıyla listeler."
    risk_level = RiskLevel.READ_ONLY
    category = "service"

    async def execute(
        self,
        filter_name: str = "",
        status: str = "",
        limit: int = 50,
        **kwargs: Any,
    ) -> ToolExecutionResult:
        try:
            limit = min(max(limit, 1), 200)
            script_parts = ["Get-Service"]
            if filter_name:
                safe = filter_name.replace("'", "''")
                script_parts.append(
                    f"Where-Object {{ $_.Name -like '*{safe}*' -or $_.DisplayName -like '*{safe}*' }}"
                )
            if status:
                safe_status = status.replace("'", "''")
                script_parts.append(f"Where-Object {{ $_.Status -eq '{safe_status}' }}")
            script_parts.append(
                f"Select-Object -First {limit} Name,DisplayName,Status,StartType"
            )
            data = await run_powershell_json(" | ".join(script_parts))
            services = data if isinstance(data, list) else ([data] if data else [])
            return ToolExecutionResult(
                success=True,
                output={"count": len(services), "services": services},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class GetServiceStatusTool(BaseTool):
    name = "get_service_status"
    description = "Belirli bir Windows servisinin durumunu sorgular."
    risk_level = RiskLevel.READ_ONLY
    category = "service"

    async def execute(self, service_name: str, **kwargs: Any) -> ToolExecutionResult:
        if not service_name:
            return ToolExecutionResult(success=False, error="service_name required")
        try:
            safe = service_name.replace("'", "''")
            data = await run_powershell_json(
                f"Get-Service -Name '{safe}' -ErrorAction Stop | "
                "Select-Object Name,DisplayName,Status,StartType,"
                "@{N='DependentServices';E={($_.DependentServices.Name -join ',')}}"
            )
            return ToolExecutionResult(success=True, output=data, verified=True)
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))
