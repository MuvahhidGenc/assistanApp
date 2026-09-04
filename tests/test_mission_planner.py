from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.mission.context import build_planning_context, build_planning_prompt
from hermes.mission.engine import MissionEngine
from hermes.mission.models import MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import (
    MissionPlanner,
    build_heuristic_plan,
)
from hermes.mission.store import MissionStore
from hermes.mission.validator import extract_plan_json, validate_plan_steps
from hermes.server.client import HermesServerError
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


def _valid_ai_plan() -> str:
    return json.dumps(
        {
            "goal": "Sistem durumunu incele",
            "subgoals": ["Sistem bilgisini topla"],
            "plan": [{"capability": "system.inspect", "inputs": {}}],
            "required_capabilities": ["system.inspect"],
            "mode": "task",
            "confidence": 0.9,
            "expected_outcome": "OS bilgisi",
        }
    )


def _mock_chat_client(content: str):
    client = MagicMock()
    client.chat = AsyncMock(
        return_value={"choices": [{"message": {"content": content}}]}
    )
    return client


@pytest.mark.asyncio
async def test_simple_mission_ai_plan(registry, mission_root):
    store = MissionStore()
    mission = store.create_mission("Standart PC kurulumu yap")
    planner = MissionPlanner(_mock_chat_client(_valid_ai_plan()), registry)
    result = await planner.create_plan(mission)
    assert result.success is True
    assert result.source == "capability_resolver"
    assert result.steps
    assert result.steps[0].metadata.get("source") == "intent_router"
    assert result.steps[0].metadata.get("capability") == "system.inspect"
    provided = {item.name for item in registry.find_by_capability("system.inspect")}
    assert result.steps[0].tool_name in provided


@pytest.mark.asyncio
async def test_multi_step_mission_execution(registry, mission_root):
    store = MissionStore()
    mission = store.create_mission("PC kurulumu")
    mission.steps = [
        MissionStep(
            step_id="s1",
            title="Sistem",
            action=StepAction.TOOL,
            tool_name="get_system_info",
            tool_arguments={},
            verification={"required": True, "method": "output_present"},
        ),
        MissionStep(
            step_id="s2",
            title="Mantik",
            action=StepAction.LOGICAL,
            depends_on=["s1"],
        ),
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = __import__(
        "hermes.security.policy_engine", fromlist=["PolicyDecision"]
    ).PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock(
        return_value=ToolResultPayload(tool_call_id="tc-1", success=True, output={"hostname": "PC"})
    )

    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MissionPlanner(_mock_chat_client(""), registry))
    assert result.handled is True
    assert result.success is True
    assert len(result.step_summaries) == 2


def test_invalid_ai_plan_json(registry):
    raw = extract_plan_json("Bu bir plan degil")
    assert raw == []
    validation = validate_plan_steps(raw, registry)
    assert validation.ok is False


def test_unknown_tool_in_plan(registry):
    validation = validate_plan_steps(
        [
            {
                "step_id": "bad",
                "title": "Bad tool",
                "action": "tool",
                "tool_name": "nonexistent_tool_xyz",
                "tool_arguments": {},
            }
        ],
        registry,
    )
    assert validation.ok is False
    assert any("unknown tool" in err for err in validation.errors)


def test_invalid_arguments_in_plan(registry):
    validation = validate_plan_steps(
        [
            {
                "step_id": "run",
                "title": "Run",
                "action": "tool",
                "tool_name": "run_command",
                "tool_arguments": {},
            }
        ],
        registry,
    )
    assert validation.ok is False
    assert any("missing required" in err for err in validation.errors)


@pytest.mark.asyncio
async def test_planner_timeout_falls_back_to_heuristic(registry, mission_root):
    client = MagicMock()

    async def slow_chat(_request):
        await asyncio.sleep(2)
        return {"choices": [{"message": {"content": "{}"}}]}

    client.chat = slow_chat
    planner = MissionPlanner(client, registry, timeout_seconds=0.01)
    mission = MissionStore().create_mission("Standart PC kurulumu yap")
    result = await planner.create_plan(mission)
    assert result.success is True
    assert result.source == "heuristic"
    assert len(result.steps) >= 2


@pytest.mark.asyncio
async def test_planner_failure_falls_back_to_heuristic(registry, mission_root):
    client = MagicMock()
    client.chat = AsyncMock(side_effect=HermesServerError("connection failed"))
    planner = MissionPlanner(client, registry)
    mission = MissionStore().create_mission("Standart PC kurulumu yap")
    result = await planner.create_plan(mission)
    assert result.success is True
    assert result.source == "heuristic"


@pytest.mark.asyncio
async def test_cached_plan_skips_ai_call(registry, mission_root):
    store = MissionStore()
    mission = store.create_mission("Cached mission")
    mission.steps = [
        MissionStep(step_id="s1", title="Step", action=StepAction.LOGICAL),
    ]
    mission.plan_validated = True
    store.save(mission)

    client = _mock_chat_client(_valid_ai_plan())
    planner = MissionPlanner(client, registry)
    result = await planner.create_plan(mission)
    assert result.source == "cached"
    client.chat.assert_not_called()


@pytest.mark.asyncio
async def test_resumed_plan_without_replan(registry, mission_root):
    store = MissionStore()
    mission = store.create_mission("Resume mission")
    mission.steps = [
        MissionStep(
            step_id="inspect",
            title="Inspect",
            action=StepAction.TOOL,
            tool_name="get_system_info",
            tool_arguments={},
            status=MissionStepStatus.COMPLETED,
        ),
        MissionStep(
            step_id="verify",
            title="Verify",
            action=StepAction.LOGICAL,
            depends_on=["inspect"],
        ),
    ]
    mission.plan_validated = True
    mission.plan_source = "ai"
    store.save(mission)

    client = MagicMock()
    client.chat = AsyncMock()
    planner = MissionPlanner(client, registry)
    engine = MissionEngine(store, registry, MagicMock())
    result = await engine.run(mission.mission_id, planner)
    assert result.handled is True
    client.chat.assert_not_called()


@pytest.mark.asyncio
async def test_security_rejection_blocks_execution(registry, mission_root):
    store = MissionStore()
    mission = store.create_mission("Tehlikeli islem")
    mission.steps = [
        MissionStep(
            step_id="deny",
            title="Format",
            action=StepAction.TOOL,
            tool_name="format_disk",
            tool_arguments={},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    from hermes.security.policy_engine import PolicyDecision, PolicyEngine

    executor = MagicMock()
    executor._policy = PolicyEngine()
    real_policy = executor._policy.evaluate("format_disk", {})
    assert real_policy.decision == PolicyDecision.DENY

    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MissionPlanner(_mock_chat_client(""), registry))
    assert result.handled is True
    assert result.success is False
    assert "GUVENLIK" in result.summary or "kismen" in result.summary.lower()


def test_planning_context_structure(registry):
    context = build_planning_context("Standart PC kurulumu yap", registry)
    prompt = build_planning_prompt(context)
    assert "Standart PC kurulumu yap" in prompt
    assert "available_capabilities" in prompt
    assert "available_tools" not in prompt
    assert "tool_name" not in prompt
    assert "standard_pc_setup" in prompt or "skill_id" in prompt
    assert len(context.tools) >= 40
    assert "system.inspect" in context.capabilities


def test_heuristic_plan_for_pc_setup(registry):
    steps = build_heuristic_plan("Standart PC kurulumu yap", registry)
    validation = validate_plan_steps([s.to_dict() for s in steps], registry)
    assert validation.ok is True
    assert any(step.action == StepAction.LOGICAL for step in validation.steps)


@pytest.mark.asyncio
async def test_invalid_ai_plan_triggers_heuristic(registry, mission_root):
    client = _mock_chat_client('{"steps":[{"step_id":"x","title":"x","action":"tool","tool_name":"missing_tool"}]}')
    planner = MissionPlanner(client, registry)
    mission = MissionStore().create_mission("Standart PC kurulumu yap")
    result = await planner.create_plan(mission)
    assert result.success is True
    assert result.source == "heuristic"


@pytest.mark.asyncio
async def test_engine_fallback_recommended_when_planner_fails(registry, mission_root, monkeypatch):
    store = MissionStore()
    mission = store.create_mission("Bilinmeyen gorev")

    async def fail_plan(_mission, *, force_replan=False):
        return __import__(
            "hermes.mission.planner", fromlist=["PlannerResult"]
        ).PlannerResult(success=False, steps=[], source="heuristic", error="failed")

    planner = MagicMock()
    planner.create_plan = fail_plan
    engine = MissionEngine(store, registry, MagicMock())
    result = await engine.run(mission.mission_id, planner)
    assert result.handled is False
    assert result.fallback_recommended is True
