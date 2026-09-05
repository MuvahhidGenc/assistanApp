"""Canonical required goal + single verification authority."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.config.settings import RiskLevel
from hermes.context.entity_decision import Confidence
from hermes.intent.models import AgentIntent, canonical_required_capabilities
from hermes.intent.router import IntentRouter, RouteKind
from hermes.mission.engine import MissionEngine
from hermes.mission.goal_verification import (
    finalize_mission_goal,
    required_capabilities_met,
)
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.store import MissionStore
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.skills.executor import SkillExecutor
from hermes.tools.base import ToolExecutionResult
from hermes.tools.executor import ToolExecutor
from hermes.tools.manifest import LocalToolRequest
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def router(registry, tmp_path):
    executor = ToolExecutor(
        registry,
        PolicyEngine([], registry=registry),
        AuditLogger(tmp_path / "audit.log"),
        ApprovalManager(),
    )
    return IntentRouter(registry, SkillExecutor(registry, executor))


@pytest.fixture
def mission_root(tmp_path, monkeypatch):
    root = tmp_path / "missions"
    index_path = root / "index.json"
    monkeypatch.setattr("hermes.mission.store.missions_dir", lambda: root)
    monkeypatch.setattr("hermes.mission.store.missions_index_path", lambda: index_path)
    monkeypatch.setattr(
        "hermes.mission.store.ensure_user_dirs",
        lambda: root.mkdir(parents=True, exist_ok=True),
    )
    return root


@pytest.fixture
def executor(registry, tmp_path):
    return ToolExecutor(
        registry,
        PolicyEngine([RiskLevel.HIGH_RISK], registry=registry),
        AuditLogger(tmp_path / "audit.log"),
        ApprovalManager(),
    )


def _search_open_intent() -> AgentIntent:
    return AgentIntent.from_dict(
        {
            "goal": "indirmelerde bul ve ac",
            "required_capabilities": ["filesystem.search", "filesystem.open"],
            "plan": [
                {
                    "capability": "filesystem.search",
                    "inputs": {"path": "C:/Downloads", "pattern": "*"},
                }
            ],
            "confidence": 0.9,
        }
    )


def test_canonical_union_keeps_omitted_plan_capability():
    intent = _search_open_intent()
    assert canonical_required_capabilities(intent) == (
        "filesystem.search",
        "filesystem.open",
    )


def test_incomplete_plan_cannot_shrink_required_goal(router):
    """required=[search,open] + plan=[search] is not a search-only success."""
    plan = router.route(_search_open_intent(), confidence=Confidence.HIGH)

    if plan.kind is RouteKind.CAPABILITY_PLAN:
        caps = [step.metadata["capability"] for step in plan.steps]
        assert "filesystem.search" in caps
        assert "filesystem.open" in caps
        open_step = next(
            step for step in plan.steps if step.metadata["capability"] == "filesystem.open"
        )
        assert open_step.argument_bindings
    else:
        assert plan.kind in (RouteKind.QUESTION, RouteKind.UNSUPPORTED)
        assert "filesystem.open" in plan.unfillable_capabilities


def test_search_only_execution_is_not_completed_when_open_was_required():
    intent = _search_open_intent()
    mission = Mission(
        mission_id="m-shrink",
        user_goal=intent.goal,
        status=MissionStatus.RUNNING,
    )
    mission.working_context["required_capabilities"] = list(
        canonical_required_capabilities(intent)
    )
    mission.steps = [
        MissionStep(
            step_id="search",
            title="search",
            status=MissionStepStatus.COMPLETED,
            action=StepAction.TOOL,
            tool_name="search_files",
            verification_status="verified",
            metadata={"capability": "filesystem.search"},
        )
    ]
    result = finalize_mission_goal(mission, all_steps_done=True)
    assert result.status != "completed"
    assert result.goal_achieved is False


def test_required_capabilities_met_rejects_unknown():
    mission = Mission(
        mission_id="m-unknown-cap",
        user_goal="notepad ac",
        status=MissionStatus.RUNNING,
    )
    mission.working_context["required_capabilities"] = ["application.open"]
    mission.steps = [
        MissionStep(
            step_id="open",
            title="open",
            status=MissionStepStatus.COMPLETED,
            action=StepAction.TOOL,
            tool_name="open_app",
            verification_status="unknown",
            metadata={"capability": "application.open"},
        )
    ]
    assert required_capabilities_met(mission) is False
    result = finalize_mission_goal(mission, all_steps_done=True)
    assert result.status != "completed"
    assert result.goal_achieved is False


def _success_sentence(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(
        token in lowered
        for token in (
            "gorev tamamlandi",
            "mission tamamlandi",
            "tamam, bitti",
        )
    )


@pytest.mark.asyncio
async def test_open_app_unknown_is_not_mission_completed(
    executor, registry, mission_root, tmp_path, monkeypatch
):
    """Binary exists but pid/window cannot be confirmed → UNKNOWN ≠ COMPLETED."""
    binary = tmp_path / "app.exe"
    binary.write_bytes(b"mz")
    monkeypatch.setattr(
        "hermes.tools.windows.input_backend.find_app_window_title",
        lambda app, **kwargs: None,
    )
    output = {"app": "notepad", "path": str(binary)}
    registry.get("open_app").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output=output)
    )

    standalone = await executor.execute_tool_call(
        ToolCallRequest(name="open_app", arguments={"app": "notepad"})
    )
    assert standalone.success is True
    assert standalone.verification_status == "unknown"

    store = MissionStore()
    mission = store.create_mission("notepad ac")
    mission.working_context["required_capabilities"] = ["application.open"]
    mission.steps = [
        MissionStep(
            step_id="open",
            title="open",
            action=StepAction.TOOL,
            tool_name="open_app",
            tool_arguments={"app": "notepad"},
            metadata={"capability": "application.open"},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    verify_calls = []
    original = executor._verify_result

    async def spy(payload, tool_name, arguments, run_id):
        verify_calls.append(tool_name)
        return await original(payload, tool_name, arguments, run_id)

    executor._verify_result = spy
    engine = MissionEngine(store, registry, executor)
    engine._recovery.config.retry_backoff_seconds = 0
    result = await engine._execute_plan(mission)  # noqa: SLF001
    loaded = store.load(mission.mission_id)

    assert verify_calls == []
    assert loaded.steps[0].verification_status == "unknown"
    assert loaded.steps[0].status != MissionStepStatus.COMPLETED
    assert result.success is False
    assert loaded.status != MissionStatus.COMPLETED
    assert required_capabilities_met(loaded) is False
    assert _success_sentence(result.summary) is False
    assert loaded.status in (
        MissionStatus.FAILED,
        MissionStatus.WAITING_FOR_USER,
        MissionStatus.RECOVERING,
    )


@pytest.mark.asyncio
async def test_echo_generic_unknown_does_not_complete_mission(
    executor, registry, mission_root
):
    """No dedicated verifier: GenericVerifier UNKNOWN must not complete."""
    registry.get("echo").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output="pong")
    )
    standalone = await executor.execute_tool_call(
        ToolCallRequest(name="echo", arguments={"text": "pong"})
    )
    assert standalone.success is True
    assert standalone.verification_status is None

    store = MissionStore()
    mission = store.create_mission("echo")
    mission.working_context["required_capabilities"] = ["debug.echo"]
    mission.steps = [
        MissionStep(
            step_id="echo",
            title="echo",
            action=StepAction.TOOL,
            tool_name="echo",
            tool_arguments={"text": "pong"},
            metadata={"capability": "debug.echo"},
        )
    ]
    mission.plan_validated = True
    store.save(mission)
    engine = MissionEngine(store, registry, executor)
    engine._recovery.config.retry_backoff_seconds = 0
    result = await engine._execute_plan(mission)  # noqa: SLF001
    loaded = store.load(mission.mission_id)

    assert loaded.steps[0].verification_status == "unknown"
    assert loaded.steps[0].status != MissionStepStatus.COMPLETED
    assert result.success is False
    assert loaded.status != MissionStatus.COMPLETED
    assert required_capabilities_met(loaded) is False
    assert _success_sentence(result.summary) is False


@pytest.mark.asyncio
async def test_mission_local_callback_does_not_run_executor_verification(
    executor, registry, mission_root, tmp_path, monkeypatch
):
    target = tmp_path / "note.txt"
    target.write_text("hi", encoding="utf-8")
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    monkeypatch.setattr(
        "hermes.tools.verifiers.specific.resolve_user_path",
        lambda raw: Path(raw),
    )
    verify_calls: list[str] = []
    original = executor._verify_result

    async def spy(payload, tool_name, arguments, run_id):
        verify_calls.append(tool_name)
        return await original(payload, tool_name, arguments, run_id)

    executor._verify_result = spy

    async def mission_exec(request: LocalToolRequest, run_id: str):
        return await executor.execute_tool_call(
            ToolCallRequest(name=request.name, arguments=request.arguments),
            run_id=run_id,
            verify=False,
        )

    store = MissionStore()
    mission = store.create_mission("oku")
    mission.steps = [
        MissionStep(
            step_id="read",
            title="read",
            action=StepAction.TOOL,
            tool_name="read_file",
            tool_arguments={"path": str(target)},
            metadata={"capability": "filesystem.read"},
        )
    ]
    mission.plan_validated = True
    store.save(mission)
    engine = MissionEngine(
        store, registry, executor, execute_local_tool=mission_exec
    )
    result = await engine._execute_plan(mission)  # noqa: SLF001
    loaded = store.load(mission.mission_id)
    assert result.success is True
    assert verify_calls == []
    assert loaded.steps[0].verification_status == "verified"


@pytest.mark.asyncio
async def test_standalone_executor_still_verifies_by_default(executor, tmp_path, registry):
    missing = tmp_path / "ghost.txt"
    registry.get("read_file").execute = AsyncMock(
        return_value=ToolExecutionResult(
            success=True,
            output={"path": str(missing), "content": "forged", "exists": True},
        )
    )
    result = await executor.execute_tool_call(
        ToolCallRequest(name="read_file", arguments={"path": str(missing)})
    )
    assert result.success is False
    assert result.verification_status == "failed"

    skipped = await executor.execute_tool_call(
        ToolCallRequest(name="read_file", arguments={"path": str(missing)}),
        verify=False,
    )
    assert skipped.success is True
    assert skipped.verification_status is None


@pytest.mark.asyncio
async def test_orchestrator_mission_wrapper_does_not_verify(tmp_path, monkeypatch):
    from hermes.agent.orchestrator import AgentOrchestrator
    from hermes.config.settings import AppSettings
    from hermes.server.models import Session

    settings = AppSettings.load()
    server = MagicMock()
    server.create_session = AsyncMock(return_value=Session(id="sess-1"))
    orch = AgentOrchestrator(settings, server)
    verify_calls: list[str] = []
    original = orch._executor._verify_result

    async def spy(payload, tool_name, arguments, run_id):
        verify_calls.append(tool_name)
        return await original(payload, tool_name, arguments, run_id)

    orch._executor._verify_result = spy
    orch._executor._registry.get("echo").execute = AsyncMock(
        return_value=ToolExecutionResult(success=True, output="pong")
    )
    payload = await orch._execute_mission_local_tool(
        LocalToolRequest(name="echo", arguments={"text": "pong"}),
        "run-mission",
    )
    assert payload.success is True
    assert verify_calls == []

    await orch._execute_local_tool(
        LocalToolRequest(name="echo", arguments={"text": "pong"}),
        "run-fast",
    )
    assert verify_calls == ["echo"]
