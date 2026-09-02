from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.local_intent import LocalIntent, summarize_local_result
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.models import MissionStep, StepAction
from hermes.mission.validator import validate_plan_steps
from hermes.security.policy_engine import PolicyEngine
from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.tools.execution_target import (
    ExecutionTarget,
    infer_message_execution_target,
    resolve_requested_execution_target,
    user_context_implies_client,
    validate_runtime_execution,
)
from hermes.tools.executor import ToolExecutor
from hermes.tools.manifest import LocalToolRequest, build_tool_manifest, parse_local_tool_request
from hermes.tools.registry import create_default_registry
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def executor(registry, tmp_path):
    policy = PolicyEngine([])
    audit = AuditLogger(str(tmp_path / "audit.log"), [])
    approval = ApprovalManager()
    return ToolExecutor(registry, policy, audit, approval)


def test_create_folder_defaults_to_client(registry):
    definition = next(d for d in registry.list_tools() if d.name == "create_folder")
    assert definition.execution_target == ExecutionTarget.CLIENT


def test_write_file_defaults_to_client(registry):
    definition = next(d for d in registry.list_tools() if d.name == "write_file")
    assert definition.execution_target == ExecutionTarget.CLIENT


def test_open_path_defaults_to_client(registry):
    definition = next(d for d in registry.list_tools() if d.name == "open_path")
    assert definition.execution_target == ExecutionTarget.CLIENT


def test_get_system_info_defaults_to_client(registry):
    definition = next(d for d in registry.list_tools() if d.name == "get_system_info")
    assert definition.execution_target == ExecutionTarget.CLIENT


@pytest.mark.asyncio
async def test_client_only_tool_cannot_execute_on_server_runtime(executor, registry):
    call = ToolCallRequest(name="open_path", arguments={"path": "C:\\x"})
    result = await executor.execute_tool_call(
        call,
        runtime=ExecutionTarget.SERVER,
    )
    assert result.success is False
    assert "client-only" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_client_runtime_rejects_server_runtime_for_client_tool(registry, tmp_path):
    policy = PolicyEngine([])
    audit = AuditLogger(str(tmp_path / "audit.log"), [])
    approval = ApprovalManager()
    tool_executor = ToolExecutor(registry, policy, audit, approval)
    call = ToolCallRequest(
        name="create_folder",
        arguments={"path": "Test"},
        execution_target="server",
    )
    result = await tool_executor.execute_tool_call(
        call,
        runtime=ExecutionTarget.SERVER,
        user_message="VPS'te Test klasoru olustur",
    )
    assert result.success is False
    assert "client-only" in (result.error or "").lower()


def test_explicit_vps_wording_allows_server_scope_for_server_tools(registry):
    with patch(
        "hermes.tools.execution_target.SERVER_ONLY_TOOLS",
        frozenset({"remote_shell"}),
    ):
        target = resolve_requested_execution_target(
            "remote_shell",
            registry,
            user_message="VPS'te komut calistir",
            envelope_target="server",
        )
    assert target == ExecutionTarget.SERVER


def test_masaustunde_always_resolves_to_client_target(registry):
    target = resolve_requested_execution_target(
        "create_folder",
        registry,
        user_message="Masaustunde HermesContextTest klasoru olustur",
        envelope_target="server",
    )
    assert target == ExecutionTarget.CLIENT
    assert user_context_implies_client("Masaustunde test klasoru olustur") is True


@pytest.mark.asyncio
async def test_open_path_client_execution_calls_local_tool(tmp_path):
    folder = tmp_path / "HermesContextTest"
    folder.mkdir()
    registry = create_default_registry()
    policy = PolicyEngine([])
    audit = AuditLogger(str(tmp_path / "audit.log"), [])
    approval = ApprovalManager()
    tool_executor = ToolExecutor(registry, policy, audit, approval)
    executed: list[str] = []

    original_get = registry.get

    def tracking_get(name: str):
        tool = original_get(name)
        if tool and name == "open_path":
            original_execute = tool.execute

            async def wrapped(**kwargs):
                executed.append(name)
                return await original_execute(**kwargs)

            tool.execute = wrapped  # type: ignore[method-assign]
        return tool

    registry.get = tracking_get  # type: ignore[method-assign]

    call = ToolCallRequest(name="open_path", arguments={"path": str(folder)})
    with patch("os.startfile"):
        result = await tool_executor.execute_tool_call(
            call,
            runtime=ExecutionTarget.CLIENT,
        )
    assert executed.count("open_path") >= 1
    assert result.success is True


def test_open_path_fake_success_cannot_produce_acildi():
    intent = LocalIntent(
        LocalToolRequest("open_path", {"path": "C:\\x"}),
        summary="Klasor acilacak",
    )
    unverified = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": "C:\\x", "opened": True, "verified": False, "is_directory": True},
    )
    text = summarize_local_result(intent, unverified)
    assert "Klasor acildi" not in text
    assert "Acildi" not in text or "dogrulayamadim" in text


def test_relative_klasoru_ac_resolves_active_folder(tmp_path):
    folder = tmp_path / "HermesContextTest"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder))
    result = ReferenceResolver().resolve("klasoru ac", ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    assert result.intent.request.arguments["path"] == str(folder.resolve())


def test_folder_open_uses_same_open_path_tool_as_file(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("x", encoding="utf-8")
    folder = tmp_path / "folder"
    folder.mkdir()

    file_result = ReferenceResolver().resolve(
        "dosyayi ac",
        ConversationalContext(active_file=str(file_path)),
    )
    folder_result = ReferenceResolver().resolve(
        "klasoru ac",
        ConversationalContext(active_folder=str(folder)),
    )
    assert file_result.intent.request.name == "open_path"
    assert folder_result.intent.request.name == "open_path"


def test_execution_target_persists_through_mission_step_validation(registry):
    raw = [
        {
            "step_id": "write_step",
            "title": "Write file",
            "action": "tool",
            "tool_name": "write_file",
            "tool_arguments": {"path": "a.txt", "content": "x"},
            "depends_on": [],
        }
    ]
    validated = validate_plan_steps(raw, registry)
    assert validated.ok
    assert validated.steps[0].execution_target == "client"


def test_planner_cannot_override_tool_execution_target(registry):
    raw = [
        {
            "step_id": "open_step",
            "title": "Open",
            "action": "tool",
            "tool_name": "open_path",
            "tool_arguments": {"path": "C:\\x"},
            "execution_target": "server",
            "depends_on": [],
        }
    ]
    validated = validate_plan_steps(raw, registry)
    assert not validated.ok
    assert any("cannot override execution_target" in err for err in validated.errors)


def test_manifest_includes_execution_target(registry):
    manifest = build_tool_manifest(registry)
    open_entry = next(item for item in manifest if item["name"] == "open_path")
    assert open_entry["execution_target"] == "client"


def test_local_tool_envelope_parses_execution_target():
    payload = (
        'LOCAL_TOOL {"name": "open_path", "execution_target": "client", '
        '"arguments": {"path": "C:\\\\test"}, "mission_id": "m1", "step_id": "s1"}'
    )
    parsed = parse_local_tool_request(payload)
    assert parsed is not None
    assert parsed.execution_target == "client"
    assert parsed.mission_id == "m1"
    assert parsed.step_id == "s1"


def test_infer_message_execution_target_vps_vs_desktop():
    assert infer_message_execution_target("VPS'te /tmp/test olustur") == ExecutionTarget.SERVER
    assert infer_message_execution_target("Masaustunde test olustur") == ExecutionTarget.CLIENT


def test_security_boundary_blocks_target_mismatch(registry):
    allowed, reason = validate_runtime_execution(
        "write_file",
        registry,
        runtime=ExecutionTarget.SERVER,
    )
    assert allowed is False
    assert "client-only" in reason.lower()


@pytest.mark.asyncio
async def test_orchestrator_open_folder_skips_server_create_run(tmp_path, settings):
    folder = tmp_path / "HermesContextTest"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder))
    ctx.save()

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server, mission_store=MagicMock())

    executed_tools: list[str] = []

    async def fake_execute(local_call, run_id, *, user_message=""):
        executed_tools.append(local_call.name)
        output = {"path": str(folder), "opened": True, "verified": True, "is_directory": True}
        if local_call.name == "list_windows":
            output = {"windows": [f"HermesContextTest - Explorer"]}
        return ToolResultPayload(
            tool_call_id="t1",
            success=True,
            output=output,
        )

    orchestrator._execute_local_tool = fake_execute  # type: ignore[method-assign]

    with patch("hermes.context.conversational_context.ConversationalContext.load", return_value=ctx):
        with patch("hermes.context.conversational_context.ConversationalContext.save"):
            with patch("hermes.client.session_store.append_conversation_turn"):
                text = await orchestrator.process_message("klasoru ac")

    server.create_run.assert_not_called()
    assert "open_path" in executed_tools
    assert "list_windows" not in executed_tools
    assert "Klasoru actim" in text or "actim" in text.casefold()
