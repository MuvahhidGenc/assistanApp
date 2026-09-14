from unittest.mock import AsyncMock, patch

import pytest

from hermes.tools.windows.network_tools import DnsLookupTool, PingHostTool
from hermes.tools.windows.pc_actions import RunCommandTool, _unix_syntax_error
from hermes.tools.windows.process_tools import ListServicesTool


@pytest.mark.parametrize(
    "command",
    [
        'mkdir -p "C:/Users/x/Desktop/tevhid" && echo hi > f.txt',
        "rm -rf C:/temp",
        "sudo apt-get update",
    ],
)
def test_unix_style_command_guard(command):
    hint = _unix_syntax_error(command)
    assert hint
    assert "POSIX" in hint or "posix" in hint.casefold()


@pytest.mark.parametrize(
    "command",
    [
        "New-Item -ItemType Directory -Path C:/Users/x/Desktop/abc",
        "Start-Process notepad",
        "Get-Service -Name spooler",
    ],
)
def test_windows_command_not_guarded(command):
    assert _unix_syntax_error(command) == ""


@pytest.mark.asyncio
async def test_run_command_rejects_unix_syntax_fast():
    tool = RunCommandTool()
    result = await tool.execute(
        command='mkdir -p "C:/Users/x/Desktop/tevhid" && echo hi > f.txt'
    )
    assert not result.success
    assert "create_folder" in result.error or "write_file" in result.error


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
