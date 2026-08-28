import pytest

from hermes.config.settings import RiskLevel
from hermes.security.policy_engine import PolicyDecision, PolicyEngine
from hermes.tools.registry import create_default_registry
from hermes.tools.windows.debug_tools import EchoTool
from hermes.tools.windows.system_tools import GetDiskInfoTool, GetSystemInfoTool


@pytest.mark.asyncio
async def test_echo_tool():
    tool = EchoTool()
    result = await tool.execute(message="hello")
    assert result.success
    assert result.output["echo"] == "hello"


@pytest.mark.asyncio
async def test_system_info_tool():
    tool = GetSystemInfoTool()
    result = await tool.execute()
    assert result.success
    assert "hostname" in result.output


@pytest.mark.asyncio
async def test_disk_info_tool():
    tool = GetDiskInfoTool()
    result = await tool.execute()
    assert result.success
    assert "disks" in result.output
    assert len(result.output["disks"]) >= 1


def test_default_registry():
    registry = create_default_registry()
    assert "get_system_info" in registry
    assert "get_network_config" in registry
    assert "list_services" in registry
    assert "read_registry" in registry
    assert len(registry) >= 22


def test_read_registry_is_read_only_in_policy():
    engine = PolicyEngine()
    result = engine.evaluate("read_registry")
    assert result.decision == PolicyDecision.ALLOW
    assert result.risk_level == RiskLevel.READ_ONLY
