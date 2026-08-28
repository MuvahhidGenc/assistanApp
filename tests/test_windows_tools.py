from unittest.mock import AsyncMock, patch

import pytest

from hermes.tools.windows.network_tools import DnsLookupTool, PingHostTool
from hermes.tools.windows.process_tools import ListServicesTool


@pytest.mark.asyncio
async def test_ping_host_mock():
    tool = PingHostTool()
    with patch(
        "hermes.tools.windows.network_tools.run_command",
        new_callable=AsyncMock,
        return_value=(0, "Reply from 8.8.8.8", ""),
    ):
        result = await tool.execute(host="8.8.8.8", count=2)
    assert result.success
    assert result.output["host"] == "8.8.8.8"


@pytest.mark.asyncio
async def test_dns_lookup_mock():
    tool = DnsLookupTool()
    with patch(
        "hermes.tools.windows.network_tools.run_powershell_json",
        new_callable=AsyncMock,
        return_value=[{"Name": "example.com", "Type": "A", "IPAddress": "93.184.216.34"}],
    ):
        result = await tool.execute(hostname="example.com")
    assert result.success
    assert result.output["records"][0]["IPAddress"] == "93.184.216.34"


@pytest.mark.asyncio
async def test_list_services_mock():
    tool = ListServicesTool()
    with patch(
        "hermes.tools.windows.process_tools.run_powershell_json",
        new_callable=AsyncMock,
        return_value=[{"Name": "wuauserv", "Status": 1, "DisplayName": "Windows Update"}],
    ):
        result = await tool.execute(filter_name="update", limit=5)
    assert result.success
    assert result.output["count"] == 1
