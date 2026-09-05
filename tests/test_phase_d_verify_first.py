"""Phase D — verification is no longer a mission-only privilege.

Before this phase, a tool executed outside a mission (the fast path, RPC) was
trusted purely on its own `success` flag. These tests pin the new behaviour:
observable state decides, and a mission still uses its own richer verifier.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.config.settings import RiskLevel
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.server.models import ToolCallRequest
from hermes.tools.base import ToolExecutionResult
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry


@pytest.fixture
def executor(tmp_path):
    """Approval is gated on high risk only, so these tests exercise verification.

    These tools are normal_modification; leaving the default policy in place
    would stop them at the approval gate before any verifier ran.
    """
    registry = create_default_registry()
    return ToolExecutor(
        registry,
        PolicyEngine([RiskLevel.HIGH_RISK], registry=registry),
        AuditLogger(tmp_path / "audit.log"),
        ApprovalManager(),
    )


def _call(name: str, **arguments) -> ToolCallRequest:
    return ToolCallRequest(name=name, arguments=arguments)


@pytest.mark.asyncio
async def test_a_lying_tool_is_caught_outside_a_mission(executor, tmp_path):
    """write_file claims success but nothing lands on disk."""
    target = tmp_path / "ghost.txt"
    tool = executor._registry.get("write_file")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"path": str(target)})
    )

    result = await executor.execute_tool_call(
        _call("write_file", path=str(target), content="merhaba")
    )

    assert not target.exists()
    assert result.success is False
    assert result.verification_status == "failed"
    assert result.error


@pytest.mark.asyncio
async def test_a_truthful_tool_is_confirmed_not_just_trusted(executor, tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("merhaba", encoding="utf-8")
    tool = executor._registry.get("write_file")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"path": str(target)})
    )

    result = await executor.execute_tool_call(
        _call("write_file", path=str(target), content="merhaba")
    )

    assert result.success is True
    assert result.verified is True
    assert result.verification_status == "verified"
    assert result.verification_method


@pytest.mark.asyncio
async def test_create_folder_is_verified_against_the_filesystem(executor, tmp_path):
    target = tmp_path / "missing_folder"
    tool = executor._registry.get("create_folder")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"path": str(target)})
    )

    result = await executor.execute_tool_call(_call("create_folder", path=str(target)))

    assert result.success is False
    assert result.verification_status == "failed"


@pytest.mark.asyncio
async def test_a_lying_open_path_is_caught_outside_a_mission(executor, tmp_path):
    missing = tmp_path / "ghost_folder"
    tool = executor._registry.get("open_path")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(
            success=True, output={"path": str(missing), "verified": True}
        )
    )

    result = await executor.execute_tool_call(_call("open_path", path=str(missing)))

    assert result.success is False
    assert result.verification_status == "failed"


@pytest.mark.asyncio
async def test_a_lying_read_file_is_caught_outside_a_mission(executor, tmp_path):
    missing = tmp_path / "ghost.txt"
    tool = executor._registry.get("read_file")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            output={"path": str(missing), "content": "forged", "exists": True},
        )
    )

    result = await executor.execute_tool_call(_call("read_file", path=str(missing)))

    assert result.success is False
    assert result.verification_status == "failed"


@pytest.mark.asyncio
async def test_a_lying_open_app_is_caught_outside_a_mission(executor, tmp_path):
    missing = tmp_path / "chrome.exe"
    tool = executor._registry.get("open_app")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            output={
                "app": "chrome",
                "path": str(missing),
                "verified": True,
                "window_title": "Chrome",
            },
        )
    )

    result = await executor.execute_tool_call(_call("open_app", app="chrome"))

    assert result.success is False
    assert result.verification_status == "failed"
    """The generic verifier only restates tool output, so it is not run here."""
    tool = executor._registry.get("echo")
    tool.execute = AsyncMock(return_value=ToolExecutionResult(success=True, output="pong"))

    result = await executor.execute_tool_call(_call("echo", text="pong"))

    assert result.success is True
    assert result.verification_status is None


@pytest.mark.asyncio
async def test_an_already_failed_tool_is_not_relabelled(executor, tmp_path):
    tool = executor._registry.get("write_file")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(success=False, error="disk dolu")
    )

    result = await executor.execute_tool_call(
        _call("write_file", path=str(tmp_path / "x.txt"), content="a")
    )

    assert result.success is False
    assert result.error == "disk dolu"


@pytest.mark.asyncio
async def test_verification_can_be_declined_by_the_caller(executor, tmp_path):
    """Missions verify with step context and an observe callback of their own."""
    target = tmp_path / "ghost.txt"
    tool = executor._registry.get("write_file")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"path": str(target)})
    )

    result = await executor.execute_tool_call(
        _call("write_file", path=str(target), content="a"), verify=False
    )

    assert result.success is True
    assert result.verification_status is None


@pytest.mark.asyncio
async def test_a_broken_verifier_never_blocks_execution(executor, tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("a", encoding="utf-8")
    tool = executor._registry.get("write_file")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output={"path": str(target)})
    )
    broken = MagicMock()
    broken.get.side_effect = RuntimeError("verifier exploded")
    executor._verifiers = broken

    result = await executor.execute_tool_call(
        _call("write_file", path=str(target), content="a")
    )

    assert result.success is True


@pytest.mark.asyncio
async def test_tool_reported_verification_reaches_the_caller(executor):
    """Fields the tool already filled in used to be dropped by the executor."""
    tool = executor._registry.get("echo")
    tool.execute = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            output="pong",
            verified=True,
            verification_status="verified",
            verification_method="self_report",
        )
    )

    result = await executor.execute_tool_call(_call("echo", text="pong"))

    assert result.verified is True
    assert result.verification_method == "self_report"


def test_mission_engine_opts_out_of_executor_verification():
    """Guards against silently double-verifying every mission step."""
    source = Path("src/hermes/mission/engine.py").read_text(encoding="utf-8")
    calls = re.findall(r"execute_tool_call\((.*?)\)", source, flags=re.DOTALL)

    assert calls
    assert all("verify=False" in arguments for arguments in calls)


def test_verifier_context_does_not_import_the_mission_engine():
    """A cycle here silently turned executor verification into a no-op."""
    subprocess.run(
        [sys.executable, "-c", "import hermes.tools.executor"],
        check=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
