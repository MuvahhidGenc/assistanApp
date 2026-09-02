from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.mission.engine import MissionEngine
from hermes.mission.models import MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.recovery import (
    ErrorCategory,
    RecoveryConfig,
    RecoveryEngine,
    RecoveryOutcome,
    classify_error,
    step_is_fatal,
)
from hermes.mission.recovery.context import FailureKind
from hermes.mission.store import MissionStore
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


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


def test_classify_errors():
    assert classify_error(error_text="temporarily busy") == ErrorCategory.TRANSIENT
    assert classify_error(error_text="already installed") == ErrorCategory.ALREADY_EXISTS
    assert classify_error(error_text="permission denied") == ErrorCategory.PERMISSION_DENIED
    assert classify_error(error_text="authentication required for private repo") == ErrorCategory.AUTHENTICATION_REQUIRED
    assert classify_error(
        failure_kind="verification",
        verification_details={"reason": "dns_mismatch"},
    ) == ErrorCategory.VERIFICATION_FAILURE


def test_step_fatal_default():
    dns = MissionStep(step_id="d", title="dns", tool_name="set_dns")
    install = MissionStep(step_id="i", title="telegram", tool_name="install_program")
    clone = MissionStep(step_id="c", title="clone", tool_name="git_clone")
    assert step_is_fatal(dns) is True
    assert step_is_fatal(clone) is True
    assert step_is_fatal(install) is False


@pytest.mark.asyncio
async def test_retry_success(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission("Retry test")
    mission.steps = [
        MissionStep(
            step_id="folder",
            title="Folder",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": "test-retry"},
            verification={"required": True},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    calls = {"n": 0}

    target = Path.home() / "Desktop" / "test-retry"

    async def execute_side_effect(tool_call, run_id="", skip_approval=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return ToolResultPayload(tool_call_id="1", success=False, error="timeout")
        target.mkdir(parents=True, exist_ok=True)
        return ToolResultPayload(tool_call_id="2", success=True, output={"path": str(target)})

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock(side_effect=execute_side_effect)

    config = RecoveryConfig(retry_backoff_seconds=0, total_recovery_budget=5)
    engine = MissionEngine(store, registry, executor, recovery_config=config)
    result = await engine.run(mission.mission_id, MagicMock())
    assert result.success is True
    assert calls["n"] >= 1


@pytest.mark.asyncio
async def test_retry_exhausted_waiting_for_user(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission("Fatal retry")
    mission.steps = [
        MissionStep(
            step_id="dns",
            title="DNS",
            action=StepAction.TOOL,
            tool_name="set_dns",
            tool_arguments={"preset": "google"},
            verification={"required": True},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock(
        return_value=ToolResultPayload(tool_call_id="1", success=False, error="timeout")
    )

    config = RecoveryConfig(retry_backoff_seconds=0, total_recovery_budget=1, max_same_strategy_attempts=1)
    engine = MissionEngine(store, registry, executor, recovery_config=config)
    result = await engine.run(mission.mission_id, MagicMock())
    assert result.waiting_for_user or result.success is False
    loaded = store.load(mission.mission_id)
    assert loaded.status in (MissionStatus.WAITING_FOR_USER, MissionStatus.FAILED)


@pytest.mark.asyncio
async def test_already_installed_idempotency(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission("Install idempotent")
    mission.steps = [
        MissionStep(
            step_id="install",
            title="Chrome",
            action=StepAction.TOOL,
            tool_name="install_program",
            tool_arguments={"package": "chrome"},
            verification={"required": True},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW

    async def side_effect(tool_call, run_id="", skip_approval=False):
        if tool_call.name == "install_program":
            return ToolResultPayload(tool_call_id="1", success=False, error="already installed")
        if tool_call.name == "list_installed_programs":
            return ToolResultPayload(
                tool_call_id="2",
                success=True,
                output={"programs": [{"DisplayName": "Google Chrome"}], "count": 1},
            )
        return ToolResultPayload(tool_call_id="3", success=True, output={})

    executor.execute_tool_call = AsyncMock(side_effect=side_effect)
    config = RecoveryConfig(retry_backoff_seconds=0)
    engine = MissionEngine(store, registry, executor, recovery_config=config)
    result = await engine.run(mission.mission_id, MagicMock())
    assert result.success is True


@pytest.mark.asyncio
async def test_non_fatal_step_skipped(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission("Optional install")
    mission.steps = [
        MissionStep(
            step_id="install",
            title="Telegram",
            action=StepAction.TOOL,
            tool_name="install_program",
            tool_arguments={"package": "telegram"},
            metadata={"fatal": False},
        ),
        MissionStep(
            step_id="done",
            title="Logical",
            action=StepAction.LOGICAL,
        ),
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock(
        return_value=ToolResultPayload(tool_call_id="1", success=False, error="winget failed")
    )
    config = RecoveryConfig(retry_backoff_seconds=0, total_recovery_budget=0)
    engine = MissionEngine(store, registry, executor, recovery_config=config)
    result = await engine.run(mission.mission_id, MagicMock())
    loaded = store.load(mission.mission_id)
    assert loaded.steps[0].status == MissionStepStatus.SKIPPED
    assert result.success is True


@pytest.mark.asyncio
async def test_recovery_state_persistence(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission("Persist recovery")
    mission.steps = [
        MissionStep(
            step_id="w",
            title="Write",
            action=StepAction.TOOL,
            tool_name="write_file",
            tool_arguments={"path": "x.txt", "content": "hi"},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock(
        return_value=ToolResultPayload(tool_call_id="1", success=False, error="path missing")
    )
    config = RecoveryConfig(retry_backoff_seconds=0, total_recovery_budget=1)
    engine = MissionEngine(store, registry, executor, recovery_config=config)
    await engine.run(mission.mission_id, MagicMock())

    loaded = MissionStore().load(mission.mission_id)
    assert len(loaded.recovery_attempts) >= 1
    assert loaded.recovery_strategy is not None


@pytest.mark.asyncio
async def test_resume_during_recovery(mission_root):
    store = MissionStore()
    mission = store.create_mission("Wait")
    mission.status = MissionStatus.WAITING_FOR_USER
    mission.waiting_for_user_reason = "Auth needed"
    store.save(mission)

    resumed = store.resume_from_user(mission.mission_id, "evet devam")
    assert resumed is not None
    assert resumed.status == MissionStatus.RUNNING
    assert resumed.waiting_for_user_reason is None
    assert len(resumed.user_interventions) == 1


@pytest.mark.asyncio
async def test_auth_required_waiting_for_user(registry):
    from hermes.mission.models import Mission

    engine = RecoveryEngine(registry=registry, config=RecoveryConfig(retry_backoff_seconds=0))
    mission = Mission.create("Clone private")
    step = MissionStep(
        step_id="clone",
        title="Clone",
        tool_name="git_clone",
        tool_arguments={"repo_url": "https://github.com/x/y.git"},
    )

    outcome = await engine.attempt_recovery(
        mission,
        step,
        failure_kind=FailureKind.EXECUTION,
        error_text="authentication required for private repo",
        last_result=None,
        verification_details=None,
        run_id="r1",
        execute_tool=AsyncMock(),
        verify=AsyncMock(return_value={"verification_status": "verified"}),
    )
    assert outcome.waiting_for_user is True
    assert mission.status == MissionStatus.WAITING_FOR_USER


@pytest.mark.asyncio
async def test_risk_escalation_requires_approval(registry):
    engine = RecoveryEngine(registry=registry, config=RecoveryConfig(retry_backoff_seconds=0))
    from hermes.mission.models import Mission

    mission = Mission.create("Install")
    step = MissionStep(
        step_id="i",
        title="Install",
        tool_name="install_program",
        tool_arguments={"package": "chrome"},
        risk_level="low_risk",
    )
    policy = MagicMock()
    policy.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.REQUIRE_APPROVAL
    engine.evaluate_policy = lambda name, args: policy

    outcome = await engine.attempt_recovery(
        mission,
        step,
        failure_kind=FailureKind.EXECUTION,
        error_text="winget failed",
        last_result=None,
        verification_details=None,
        run_id="r1",
        execute_tool=AsyncMock(),
        verify=AsyncMock(return_value={"verification_status": "verified"}),
    )
    assert outcome.requires_approval or outcome.recovered or outcome.non_fatal_skip or outcome.waiting_for_user
