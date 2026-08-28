from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.tools.executor import ToolExecutor
from hermes.tools.pc_manager import PCManager, parse_pc_command
from hermes.tools.registry import create_default_registry
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.config.settings import RiskLevel


def test_parse_pc_command_json():
    name, args = parse_pc_command('{"name": "open_app", "arguments": {"app": "chrome"}}')
    assert name == "open_app"
    assert args == {"app": "chrome"}


def test_parse_pc_command_local_tool_line():
    name, args = parse_pc_command('LOCAL_TOOL {"name": "screenshot", "arguments": {}}')
    assert name == "screenshot"
    assert args == {}


def test_parse_pc_command_shorthand():
    name, args = parse_pc_command("open_app chrome")
    assert name == "open_app"
    assert "app" in args or "chrome" in str(args)


@pytest.mark.asyncio
async def test_pc_manager_process_open_app():
    registry = create_default_registry()
    executor = ToolExecutor(
        registry,
        PolicyEngine([RiskLevel.HIGH_RISK]),
        AuditLogger("/tmp/test-audit.log", []),
        ApprovalManager(),
    )
    pc = PCManager(registry, executor)
    result = await pc.process({"name": "echo", "arguments": {"message": "pc-test"}}, skip_approval=True)
    assert result.success is True
