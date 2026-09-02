"""Phase 7 mission lifecycle regression tests."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes.agent.mission_flow import (
    handle_mission_commands,
    is_independent_interrupt,
    is_mission_cancel_message,
    is_mission_resume_message,
)
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.mission.audit import MissionAuditor
from hermes.mission.engine import MissionEngine
from hermes.mission.models import (
    MISSION_SCHEMA_VERSION,
    Mission,
    MissionStatus,
    MissionStep,
    MissionStepStatus,
    StepAction,
)
from hermes.mission.store import MissionStore
from hermes.mission.validator import validate_mission_steps
from hermes.tools.registry import create_default_registry


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
def settings() -> AppSettings:
    return AppSettings()


def test_mission_status_model_fields():
    mission = Mission.create("PDF dosyalarini bul ve kopyala")
    assert mission.status == MissionStatus.CREATED
    mission.steps = [
        MissionStep(step_id="s1", title="Ara", status=MissionStepStatus.COMPLETED),
        MissionStep(step_id="s2", title="Kopyala", status=MissionStepStatus.PENDING),
    ]
    assert mission.total_steps == 2
    assert mission.completed_steps == 1
    assert mission.original_goal == mission.user_goal


def test_mission_store_create_suspend_cancel_resume(mission_root):
    store = MissionStore()
    mission = store.create_mission("Uzun gorev")
    assert mission.status == MissionStatus.PLANNING

    store.snapshot_context(mission.mission_id, {"active_folder": r"C:\test"})
    suspended = store.suspend_mission(mission.mission_id, reason="Chrome ac")
    assert suspended is not None
    assert suspended.status == MissionStatus.PAUSED
    assert mission.mission_id in store.list_suspended_ids()

    resumed = store.resume(mission.mission_id)
    assert resumed is not None
    assert resumed.status == MissionStatus.RUNNING

    cancelled = store.cancel_mission(mission.mission_id)
    assert cancelled is not None
    assert cancelled.status == MissionStatus.CANCELLED


def test_mission_resume_and_cancel_commands(mission_root):
    store = MissionStore()
    ctx = ConversationalContext()
    mission = store.create_mission("PDF arastir")
    ctx.active_mission_id = mission.mission_id
    store.suspend_mission(mission.mission_id)

    assert is_mission_resume_message("devam et")
    resume = handle_mission_commands("devam et", ctx, store)
    assert resume.resume_mission_id == mission.mission_id

    assert is_mission_cancel_message("iptal et")
    cancel = handle_mission_commands("iptal et", ctx, store)
    assert cancel.handled
    assert cancel.cancelled_mission_id == mission.mission_id


def test_independent_interrupt_detects_chrome():
    assert is_independent_interrupt("Chrome'u ac") is True
    assert is_independent_interrupt("PDF bul ve kopyala") is False


def test_plan_validation_rejects_unknown_tool():
    registry = create_default_registry()
    steps = [
        MissionStep(step_id="s1", title="Bad", tool_name="not_a_real_tool", tool_arguments={}),
    ]
    result = validate_mission_steps(steps, registry)
    assert result.ok is False
    assert any("unknown tool" in err for err in result.errors)


def test_mission_schema_version_bumped():
    mission = Mission.create("test")
    payload = mission.to_dict()
    assert payload["schema_version"] == MISSION_SCHEMA_VERSION
    assert payload["status"] in {MissionStatus.CREATED.value, MissionStatus.RUNNING.value}


@pytest.mark.asyncio
async def test_engine_skips_completed_step(mission_root):
    store = MissionStore()
    registry = create_default_registry()
    executor = MagicMock()
    mission = store.create_mission("Test")
    mission.plan_validated = True
    mission.steps = [
        MissionStep(
            step_id="s1",
            title="Done",
            tool_name="list_windows",
            tool_arguments={},
            status=MissionStepStatus.COMPLETED,
            verification_status="verified",
        ),
        MissionStep(
            step_id="s2",
            title="Pending",
            action=StepAction.LOGICAL,
            status=MissionStepStatus.PENDING,
            depends_on=["s1"],
        ),
    ]
    mission.status = MissionStatus.RUNNING
    store.save(mission)
    mission = store.load(mission.mission_id)
    assert mission is not None

    calls = {"count": 0}

    async def fake_execute(local_call, run_id):
        calls["count"] += 1
        from hermes.server.models import ToolResultPayload

        return ToolResultPayload(tool_call_id="1", success=True, output={"windows": []})

    engine = MissionEngine(
        store,
        registry,
        executor,
        execute_local_tool=fake_execute,
    )
    result = await engine._execute_plan(mission)  # noqa: SLF001
    assert result.success
    assert calls["count"] == 0


@pytest.mark.asyncio
async def test_orchestrator_resume_after_suspend(settings, mission_root, monkeypatch):
    store = MissionStore()
    ctx = ConversationalContext()
    mission = store.create_mission("PDF bul ve Raporlar klasorune kopyala")
    mission.plan_validated = True
    mission.steps = [
        MissionStep(
            step_id="s1",
            title="Klasor olustur",
            tool_name="create_folder",
            tool_arguments={"path": "C:/temp/Raporlar"},
            status=MissionStepStatus.COMPLETED,
        ),
        MissionStep(
            step_id="s2",
            title="Kopyala",
            tool_name="list_windows",
            tool_arguments={},
            status=MissionStepStatus.PENDING,
        ),
    ]
    mission.status = MissionStatus.PAUSED
    store.save(mission)
    ctx.suspended_mission_ids = [mission.mission_id]

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server, mission_store=store)

    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr("hermes.context.conversational_context.ConversationalContext.save", lambda self: None)
    monkeypatch.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)

    async def fake_run(mission_id: str):
        from hermes.mission.engine import EngineResult

        loaded = store.load(mission_id)
        assert loaded is not None
        loaded.steps[1].status = MissionStepStatus.COMPLETED
        loaded.status = MissionStatus.COMPLETED
        store.save(loaded)
        return EngineResult(
            handled=True,
            success=True,
            summary="Mission tamamlandi.",
            mission_id=mission_id,
        )

    orchestrator._run_mission_engine = fake_run  # type: ignore[method-assign]

    text = await orchestrator.process_message("devam et")
    assert "devam" in text.casefold() or "tamamlandi" in text.casefold()


@pytest.mark.asyncio
async def test_orchestrator_cancel_active_mission(settings, mission_root, monkeypatch):
    store = MissionStore()
    ctx = ConversationalContext()
    mission = store.create_mission("Uzun gorev")
    mission.status = MissionStatus.RUNNING
    store.save(mission)
    ctx.active_mission_id = mission.mission_id

    orchestrator = AgentOrchestrator(settings, MagicMock(), mission_store=store)
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr("hermes.context.conversational_context.ConversationalContext.save", lambda self: None)
    monkeypatch.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)

    text = await orchestrator.process_message("iptal et")
    assert "iptal" in text.casefold()
    loaded = store.load(mission.mission_id)
    assert loaded is not None
    assert loaded.status == MissionStatus.CANCELLED


def test_context_persistence_and_reconcile(tmp_path):
    missing = tmp_path / "gone.txt"
    ctx = ConversationalContext(
        active_file=str(missing),
        last_created_file=str(missing),
        suspended_mission_ids=["abc123"],
    )
    ctx.reconcile_with_filesystem()
    assert ctx.active_file is None
    assert ctx.last_created_file is None
    payload = ctx.to_dict()
    restored = ConversationalContext.from_dict(payload)
    assert restored.suspended_mission_ids == ["abc123"]


def test_mission_auditor_events():
    auditor = MissionAuditor("mid-1")
    auditor.plan_validated(step_count=2)
    auditor.recovery_started("s1", strategy="retry")
    auditor.recovery_completed("s1")
    auditor.mission_suspended(reason="interrupt")
    auditor.mission_cancelled(reason="user")
    auditor.user_interrupted(new_goal="Chrome ac")
