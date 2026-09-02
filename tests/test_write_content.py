from __future__ import annotations

import json

import pytest

from hermes.agent.local_intent import guess_file_action
from hermes.mission.write_content import plan_composite_file_sequence
from hermes.mission.context import build_planning_context, build_planning_prompt
from hermes.mission.models import MissionStep, StepAction
from hermes.mission.planner import MissionPlanner
from hermes.mission.store import MissionStore
from hermes.mission.validator import validate_plan_steps
from hermes.mission.write_content import (
    extract_literal_write_content,
    normalize_plan_steps_write_content,
    normalize_write_file_arguments,
    resolve_write_file_content,
)
from hermes.tools.registry import create_default_registry
from unittest.mock import AsyncMock, MagicMock

HERMES_TEST2_GOAL = (
    "Masaüstünde HermesTest2 klasörü oluştur. İçine test.txt dosyası oluştur "
    "ve dosyanın içine TAM OLARAK `123456789` yaz. Başka hiçbir şey yazma."
)


@pytest.fixture
def registry():
    return create_default_registry()


def test_extract_literal_write_content_from_backticks():
    hint = extract_literal_write_content(HERMES_TEST2_GOAL)
    assert hint.literal_content == "123456789"
    assert hint.literal_source == "backticks"
    assert any("başka" in phrase.casefold() for phrase in hint.meta_instructions)


def test_resolve_write_file_content_regression():
    content = resolve_write_file_content(HERMES_TEST2_GOAL)
    assert content == "123456789"
    assert "Başka hiçbir şey yazma" not in content
    assert "başka hiçbir şey yazma" not in content.casefold()


def test_normalize_write_file_arguments_fixes_bad_planner_content():
    bad_args = {
        "path": "HermesTest2/test.txt",
        "content": ". Başka hiçbir şey yazma.",
    }
    normalized, diagnostic = normalize_write_file_arguments(HERMES_TEST2_GOAL, bad_args)
    assert normalized["content"] == "123456789"
    assert diagnostic["normalized"] is True
    assert "Başka hiçbir şey yazma" not in str(normalized["content"])


def test_normalize_plan_steps_write_content_regression(registry):
    raw_steps = [
        {
            "step_id": "create_folder",
            "title": "Klasor olustur",
            "action": "tool",
            "tool_name": "create_folder",
            "tool_arguments": {"path": "Desktop/HermesTest2"},
            "depends_on": [],
            "risk_level": "normal_modification",
        },
        {
            "step_id": "write_test_file",
            "title": "test.txt yaz",
            "action": "tool",
            "tool_name": "write_file",
            "tool_arguments": {
                "path": "Desktop/HermesTest2/test.txt",
                "content": ". Başka hiçbir şey yazma.",
            },
            "depends_on": ["create_folder"],
            "risk_level": "normal_modification",
        },
    ]
    validation = validate_plan_steps(raw_steps, registry)
    assert validation.ok is True
    steps, diagnostics = normalize_plan_steps_write_content(HERMES_TEST2_GOAL, validation.steps)
    write_step = next(step for step in steps if step.tool_name == "write_file")
    assert write_step.tool_arguments["content"] == "123456789"
    assert len(diagnostics) == 1
    assert diagnostics[0]["final_content"] == "123456789"


def test_planning_context_includes_write_file_hints(registry):
    context = build_planning_context(HERMES_TEST2_GOAL, registry)
    hints = context.relevant_context.get("write_file_argument_hints")
    assert hints is not None
    assert hints["required_content"] == "123456789"
    prompt = build_planning_prompt(context)
    assert "write_file" in prompt
    assert "meta talimatlari" in prompt or "meta talimat" in prompt


@pytest.mark.asyncio
async def test_planner_normalizes_bad_write_file_content(registry, tmp_path, monkeypatch):
    root = tmp_path / "missions"
    index_path = root / "index.json"
    monkeypatch.setattr("hermes.mission.store.missions_dir", lambda: root)
    monkeypatch.setattr("hermes.mission.store.missions_index_path", lambda: index_path)
    monkeypatch.setattr(
        "hermes.mission.store.ensure_user_dirs",
        lambda: root.mkdir(parents=True, exist_ok=True),
    )

    ai_plan = json.dumps(
        {
            "steps": [
                {
                    "step_id": "create_folder",
                    "title": "HermesTest2 klasoru",
                    "action": "tool",
                    "tool_name": "create_folder",
                    "tool_arguments": {"path": "Desktop/HermesTest2"},
            "depends_on": [],
            "risk_level": "normal_modification",
        },
        {
            "step_id": "write_txt",
            "title": "test.txt icerigi",
            "action": "tool",
            "tool_name": "write_file",
            "tool_arguments": {
                "path": "Desktop/HermesTest2/test.txt",
                "content": ". Başka hiçbir şey yazma.",
            },
            "depends_on": ["create_folder"],
            "risk_level": "normal_modification",
                },
            ]
        }
    )
    client = MagicMock()
    client.chat = AsyncMock(return_value={"choices": [{"message": {"content": ai_plan}}]})
    store = MissionStore()
    mission = store.create_mission(HERMES_TEST2_GOAL)
    planner = MissionPlanner(client, registry)
    result = await planner.create_plan(mission)
    assert result.success is True
    write_step = next(step for step in result.steps if step.tool_name == "write_file")
    assert write_step.tool_arguments["content"] == "123456789"
    assert result.argument_diagnostics
    assert result.argument_diagnostics[0]["normalized"] is True


def test_guess_file_action_defers_to_composite_sequence():
    intent = guess_file_action(HERMES_TEST2_GOAL)
    assert intent is None
    from hermes.mission.write_content import plan_composite_file_sequence

    steps = plan_composite_file_sequence(HERMES_TEST2_GOAL)
    assert len(steps) >= 1
    if steps[0].request.name == "create_folder":
        write_step = steps[1]
    else:
        write_step = steps[0]
    assert write_step.request.name == "write_file"
    assert write_step.request.arguments["content"] == "123456789"
    assert "Başka hiçbir şey yazma" not in write_step.request.arguments["content"]


def test_local_write_file_part_content_only():
    part = "dosyanın içine TAM OLARAK `123456789` yaz. Başka hiçbir şey yazma."
    intent = guess_file_action(
        part,
        resolved_references={"target_file": "Desktop/HermesTest2/test.txt"},
    )
    assert intent is not None
    assert intent.request.name == "write_file"
    assert intent.request.arguments["content"] == "123456789"
