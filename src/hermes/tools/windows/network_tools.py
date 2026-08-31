from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.windows.powershell import run_command, run_powershell_json


class GetNetworkConfigTool(BaseTool):
    name = "get_network_config"
    description = "Network adapter, IP, DNS, gateway, DHCP bilgilerini okur."
    risk_level = RiskLevel.READ_ONLY
    category = "network"

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        try:
            adapters = await run_powershell_json(
                "Get-NetIPConfiguration | Select-Object InterfaceAlias,InterfaceDescription,"
                "@{N='IPv4';E={$_.IPv4Address.IPAddress}},"
                "@{N='Gateway';E={$_.IPv4DefaultGateway.NextHop}},"
                "@{N='DNS';E={($_.DNSServer.ServerAddresses -join ',')}},IPv4DefaultGateway"
            )
            dns = await run_powershell_json(
                "Get-DnsClientServerAddress -AddressFamily IPv4 | "
                "Select-Object InterfaceAlias,ServerAddresses"
            )
            if not isinstance(adapters, (list, dict)):
                adapters = []
            return ToolExecutionResult(
                success=True,
                output={"adapters": adapters, "dns_servers": dns},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class PingHostTool(BaseTool):
    name = "ping_host"
    description = "Bir host'a ping atarak bağlantıyı test eder."
    risk_level = RiskLevel.READ_ONLY
    category = "network"

    async def execute(self, host: str = "8.8.8.8", count: int = 4, **kwargs: Any) -> ToolExecutionResult:
        try:
            count = min(max(count, 1), 10)
            code, stdout, stderr = await run_command(["ping", "-n", str(count), host])
            return ToolExecutionResult(
                success=code == 0,
                output={"host": host, "returncode": code, "output": stdout, "stderr": stderr},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class DnsLookupTool(BaseTool):
    name = "dns_lookup"
    description = "DNS sorgusu yapar (A/AAAA kayıtları)."
    risk_level = RiskLevel.READ_ONLY
    category = "network"

    async def execute(self, hostname: str = "", **kwargs: Any) -> ToolExecutionResult:
        if not hostname:
            return ToolExecutionResult(success=False, error="hostname required")
        try:
            safe = hostname.replace("'", "''")
            data = await run_powershell_json(
                f"Resolve-DnsName -Name '{safe}' -ErrorAction Stop | Select-Object Name,Type,IPAddress"
            )
            records = data if isinstance(data, list) else [data] if data else []
            return ToolExecutionResult(
                success=True,
                output={"hostname": hostname, "records": records},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class TracerouteTool(BaseTool):
    name = "traceroute"
    description = "Hedef host'a traceroute (tracert) çalıştırır."
    risk_level = RiskLevel.READ_ONLY
    category = "network"

    async def execute(self, host: str = "", max_hops: int = 20, **kwargs: Any) -> ToolExecutionResult:
        if not host:
            return ToolExecutionResult(success=False, error="host required")
        try:
            max_hops = min(max(max_hops, 1), 30)
            code, stdout, stderr = await run_command(["tracert", "-h", str(max_hops), host])
            return ToolExecutionResult(
                success=code == 0,
                output={"host": host, "returncode": code, "output": stdout, "stderr": stderr},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))


class CheckPortTool(BaseTool):
    name = "check_port"
    description = "Belirtilen host:port TCP bağlantısını test eder."
    risk_level = RiskLevel.READ_ONLY
    category = "network"

    async def execute(
        self, host: str = "", port: int = 0, timeout_seconds: int = 5, **kwargs: Any
    ) -> ToolExecutionResult:
        if not host or not port:
            return ToolExecutionResult(success=False, error="host and port required")
        try:
            safe_host = host.replace("'", "''")
            data = await run_powershell_json(
                f"Test-NetConnection -ComputerName '{safe_host}' -Port {int(port)} "
                "-WarningAction SilentlyContinue | "
                "Select-Object ComputerName,RemotePort,TcpTestSucceeded,PingSucceeded"
            )
            open_port = bool(data.get("TcpTestSucceeded")) if isinstance(data, dict) else False
            return ToolExecutionResult(
                success=True,
                output={"host": host, "port": port, "open": open_port, "details": data},
                verified=True,
            )
        except Exception as exc:
            return ToolExecutionResult(success=False, error=str(exc))
