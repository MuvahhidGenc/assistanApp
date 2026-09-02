from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.mission.engine import MissionEngine
from hermes.mission.models import MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import (
    MissionPlanner,
    build_dynamic_system_info_file_plan,
    ensure_dynamic_file_plan,
    finalize_dynamic_file_plan,
)
from hermes.mission.selection import is_fast_path_candidate, should_route_to_mission
from hermes.mission.step_context import (
    SOURCE_FIELD_PREPARED_CONTENT,
    SOURCE_FIELD_TOOL_OUTPUT_FORMATTED,
    format_system_info_file_content,
    is_placeholder_content,
    record_step_tool_output,
    resolve_step_tool_arguments,
    resolve_write_file_content,
)
from hermes.mission.store import MissionStore
from hermes.mission.write_content import (
    is_literal_composite_file_mission,
    plan_composite_file_sequence,
    requires_tool_output_dependency,
)
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry
from tests.test_write_content import HERMES_TEST2_GOAL

SYSTEM_INFO_GOAL = (
    "Masaüstünde HermesAgentTest klasörü oluştur. İçine sistem.txt dosyası oluştur. "
    "Bilgisayarımın Windows sürümünü, bilgisayar adını, işlemci bilgisini ve RAM miktarını "
    "öğrenip dosyaya yaz. İşlemleri sırayla yap ve her adımın gerçekten başarılı olduğunu doğrula."
)

SAMPLE_SYSTEM_INFO = {
    "hostname": "DESKTOP-TEST",
    "platform": "Windows-11-10.0.26200-SP0",
    "processor": "Intel64 Family 6 Model 142 Stepping 12, GenuineIntel",
    "cpu_count": 8,
    "ram_total_gb": 16.0,
    "windows": {"Caption": "Microsoft Windows 11 Pro", "Version": "10.0.26200", "BuildNumber": "26200"},
    "computer": {"Name": "DESKTOP-TEST"},
}


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


def test_requires_tool_output_dependency_for_system_info_goal():
    assert requires_tool_output_dependency(SYSTEM_INFO_GOAL) is True
    assert requires_tool_output_dependency(HERMES_TEST2_GOAL) is False


def test_literal_composite_routing_preferences():
    assert is_literal_composite_file_mission(HERMES_TEST2_GOAL) is True
    assert is_literal_composite_file_mission(SYSTEM_INFO_GOAL) is False
    assert len(plan_composite_file_sequence(SYSTEM_INFO_GOAL)) == 0
    steps = plan_composite_file_sequence(HERMES_TEST2_GOAL)
    assert len(steps) >= 1
    assert steps[-1].request.name == "write_file"
    assert steps[-1].request.arguments["content"] == "123456789"


def test_dynamic_goal_routes_to_mission_not_fast_path():
    assert should_route_to_mission(SYSTEM_INFO_GOAL) is True
    assert is_fast_path_candidate(SYSTEM_INFO_GOAL) is False
    assert should_route_to_mission(HERMES_TEST2_GOAL) is False
    assert is_fast_path_candidate(HERMES_TEST2_GOAL) is True


def test_build_dynamic_system_info_file_plan_structure(registry):
    steps = build_dynamic_system_info_file_plan(SYSTEM_INFO_GOAL, registry)
    assert len(steps) == 4
    assert steps[0].tool_name == "create_folder"
    assert steps[1].tool_name == "get_system_info"
    assert steps[2].action == StepAction.LOGICAL
    assert steps[3].tool_name == "write_file"
    assert steps[3].metadata.get("content_from_step") == "prepare_system_file_content"
    assert steps[3].argument_bindings[0]["source_field"] == SOURCE_FIELD_PREPARED_CONTENT
    assert steps[3].tool_arguments.get("content") in ("", None) or is_placeholder_content(
        str(steps[3].tool_arguments.get("content"))
    )


def test_ensure_dynamic_file_plan_replaces_under_specified_plan(registry):
    bad_plan = [
        MissionStep(
            step_id="create_folder",
            title="Folder",
            tool_name="create_folder",
            tool_arguments={"path": "Desktop/HermesAgentTest"},
        ),
        MissionStep(
            step_id="write_file",
            title="Write",
            tool_name="write_file",
            tool_arguments={"path": "Desktop/HermesAgentTest/sistem.txt", "content": "HERMES tarafindan olusturuldu."},
            depends_on=["create_folder"],
        ),
    ]
    repaired = ensure_dynamic_file_plan(SYSTEM_INFO_GOAL, bad_plan, registry)
    tool_names = [step.tool_name for step in repaired if step.action == StepAction.TOOL]
    assert "get_system_info" in tool_names
    write_step = next(step for step in repaired if step.tool_name == "write_file")
    assert is_placeholder_content(str(write_step.tool_arguments.get("content", "")))


def test_format_system_info_file_content_includes_required_fields():
    content = format_system_info_file_content(SAMPLE_SYSTEM_INFO)
    assert "DESKTOP-TEST" in content
    assert "Windows" in content
    assert "Intel" in content or "Islemci" in content
    assert "16.0" in content
    assert "HERMES tarafindan olusturuldu" not in content


def test_resolve_write_file_content_from_prepared_logical_step():
    from hermes.mission.models import Mission

    mission = Mission.create(SYSTEM_INFO_GOAL)
    mission.steps = [
        MissionStep(
            step_id="prepare_system_file_content",
            title="Prepare",
            action=StepAction.LOGICAL,
            metadata={"prepared_content": "line1\nline2"},
        ),
        MissionStep(
            step_id="write_system_file",
            title="Write",
            tool_name="write_file",
            tool_arguments={"path": "Desktop/HermesAgentTest/sistem.txt", "content": ""},
            depends_on=["prepare_system_file_content"],
            metadata={"content_from_step": "prepare_system_file_content"},
        ),
    ]
    write_step = mission.steps[1]
    resolved = resolve_step_tool_arguments(write_step, mission)
    assert resolved["content"] == "line1\nline2"
    assert not is_placeholder_content(str(resolved["content"]))


@pytest.mark.asyncio
async def test_planner_repairs_bad_dynamic_plan(registry, mission_root):
    bad_plan = json.dumps(
        {
            "steps": [
                {
                    "step_id": "create_folder",
                    "title": "Folder",
                    "action": "tool",
                    "tool_name": "create_folder",
                    "tool_arguments": {"path": "Desktop/HermesAgentTest"},
                    "depends_on": [],
                    "risk_level": "normal_modification",
                },
                {
                    "step_id": "write_file",
                    "title": "Write",
                    "action": "tool",
                    "tool_name": "write_file",
                    "tool_arguments": {
                        "path": "Desktop/HermesAgentTest/sistem.txt",
                        "content": "HERMES tarafindan olusturuldu.",
                    },
                    "depends_on": ["create_folder"],
                    "risk_level": "normal_modification",
                },
            ]
        }
    )
    client = MagicMock()
    client.chat = AsyncMock(return_value={"choices": [{"message": {"content": bad_plan}}]})
    store = MissionStore()
    mission = store.create_mission(SYSTEM_INFO_GOAL)
    result = await MissionPlanner(client, registry).create_plan(mission)
    assert result.success is True
    tool_names = [step.tool_name for step in result.steps if step.tool_name]
    assert "get_system_info" in tool_names
    write_step = next(step for step in result.steps if step.tool_name == "write_file")
    assert write_step.metadata.get("content_from_step")


@pytest.mark.asyncio
async def test_engine_dynamic_system_info_write_flow(registry, mission_root, monkeypatch, tmp_path):
    from hermes.security.policy_engine import PolicyDecision

    desktop = tmp_path / "Desktop"
    folder = desktop / "HermesAgentTest"
    file_path = folder / "sistem.txt"

    monkeypatch.setattr("hermes.mission.step_context.Path.home", lambda: tmp_path)

    store = MissionStore()
    mission = store.create_mission(SYSTEM_INFO_GOAL)
    mission.steps = build_dynamic_system_info_file_plan(SYSTEM_INFO_GOAL, registry)
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW

    async def fake_execute(call, run_id="", skip_approval=False):
        if call.name == "create_folder":
            folder.mkdir(parents=True, exist_ok=True)
            return ToolResultPayload(tool_call_id="1", success=True, output={"path": str(folder)})
        if call.name == "get_system_info":
            return ToolResultPayload(tool_call_id="2", success=True, output=dict(SAMPLE_SYSTEM_INFO))
        if call.name == "write_file":
            path = call.arguments.get("path")
            content = call.arguments.get("content")
            assert not is_placeholder_content(str(content))
            assert "DESKTOP-TEST" in str(content)
            target = file_path if "HermesAgentTest" in str(path) else tmp_path / str(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
            return ToolResultPayload(tool_call_id="3", success=True, output={"path": str(target), "size": len(str(content))})
        return ToolResultPayload(tool_call_id="x", success=False, error="unknown")

    executor.execute_tool_call = AsyncMock(side_effect=fake_execute)

    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MissionPlanner(MagicMock(), registry))
    assert result.success is True
    assert file_path.exists()
    written = file_path.read_text(encoding="utf-8")
    assert "DESKTOP-TEST" in written
    assert "Windows" in written
    assert "16.0" in written
    assert "HERMES tarafindan olusturuldu" not in written

    loaded = store.load(mission.mission_id)
    assert loaded is not None
    write_step = next(step for step in loaded.steps if step.tool_name == "write_file")
    assert write_step.status == MissionStepStatus.COMPLETED


@pytest.mark.asyncio
async def test_engine_blocks_placeholder_write_without_prior_output(registry, mission_root):
    from hermes.security.policy_engine import PolicyDecision

    store = MissionStore()
    mission = store.create_mission(SYSTEM_INFO_GOAL)
    mission.steps = [
        MissionStep(
            step_id="write_only",
            title="Write",
            tool_name="write_file",
            tool_arguments={"path": "Desktop/x/sistem.txt", "content": "HERMES tarafindan olusturuldu."},
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock()

    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MissionPlanner(MagicMock(), registry))
    assert result.success is False
    executor.execute_tool_call.assert_not_called()


def test_finalize_dynamic_file_plan_wires_ai_plan_without_logical_kind(registry):
    ai_plan = [
        MissionStep(
            step_id="step_create_folder",
            title="Klasor olustur",
            tool_name="create_folder",
            tool_arguments={"path": "Desktop/HermesAgentTest"},
        ),
        MissionStep(
            step_id="step_get_system_info",
            title="Sistem bilgisi",
            tool_name="get_system_info",
            tool_arguments={},
            depends_on=["step_create_folder"],
        ),
        MissionStep(
            step_id="step_prepare_content",
            title="Sistem bilgilerini dosya icerigi olarak hazirla",
            action=StepAction.LOGICAL,
            depends_on=["step_get_system_info"],
        ),
        MissionStep(
            step_id="step_write_file",
            title="Dosyaya yaz",
            tool_name="write_file",
            tool_arguments={
                "path": "Desktop/HermesAgentTest/sistem.txt",
                "content": "HERMES tarafindan olusturuldu.",
            },
            depends_on=["step_prepare_content"],
        ),
    ]
    finalized = finalize_dynamic_file_plan(SYSTEM_INFO_GOAL, ai_plan)
    logical = next(step for step in finalized if step.action == StepAction.LOGICAL)
    write_step = next(step for step in finalized if step.tool_name == "write_file")
    assert logical.metadata.get("logical_kind") == "format_system_info_for_file"
    assert write_step.argument_bindings[0]["source_step_id"] == "step_prepare_content"
    assert write_step.argument_bindings[0]["source_field"] == SOURCE_FIELD_PREPARED_CONTENT
    assert is_placeholder_content(str(write_step.tool_arguments.get("content", "")))


def test_resolve_write_file_content_from_tool_output_binding():
    from hermes.mission.models import Mission

    mission = Mission.create(SYSTEM_INFO_GOAL)
    mission.steps = [
        MissionStep(
            step_id="collect_system_info",
            title="Info",
            tool_name="get_system_info",
            status=MissionStepStatus.COMPLETED,
            metadata={"tool_output": dict(SAMPLE_SYSTEM_INFO)},
        ),
        MissionStep(
            step_id="write_system_file",
            title="Write",
            tool_name="write_file",
            tool_arguments={"path": "Desktop/HermesAgentTest/sistem.txt", "content": ""},
            depends_on=["collect_system_info"],
            argument_bindings=[
                {
                    "argument": "content",
                    "source_step_id": "collect_system_info",
                    "source_field": SOURCE_FIELD_TOOL_OUTPUT_FORMATTED,
                }
            ],
        ),
    ]
    record_step_tool_output(mission, mission.steps[0], dict(SAMPLE_SYSTEM_INFO))
    content = resolve_write_file_content(mission.steps[1], mission)
    assert content is not None
    assert "DESKTOP-TEST" in content
    resolved = resolve_step_tool_arguments(mission.steps[1], mission)
    assert resolved["content"] == content


def test_record_tool_result_preserves_step_outputs(registry, mission_root):
    from hermes.mission.models import Mission

    store = MissionStore()
    mission = store.create_mission(SYSTEM_INFO_GOAL)
    step = MissionStep(step_id="collect_system_info", title="Info", tool_name="get_system_info")
    record_step_tool_output(mission, step, dict(SAMPLE_SYSTEM_INFO))
    store.save(mission)

    store.record_tool_result(
        mission.mission_id,
        "get_system_info",
        success=True,
        output=dict(SAMPLE_SYSTEM_INFO),
        mission=mission,
    )

    reloaded = store.load(mission.mission_id)
    assert reloaded is not None
    outputs = reloaded.working_context.get("step_outputs") or {}
    assert "collect_system_info" in outputs or step.step_id in outputs


@pytest.mark.asyncio
async def test_engine_ai_plan_without_logical_kind_still_writes(registry, mission_root, monkeypatch, tmp_path):
    from hermes.security.policy_engine import PolicyDecision

    desktop = tmp_path / "Desktop"
    folder = desktop / "HermesAgentTest"
    file_path = folder / "sistem.txt"
    monkeypatch.setattr("hermes.mission.step_context.Path.home", lambda: tmp_path)

    ai_plan = [
        MissionStep(
            step_id="step_create_folder",
            title="Klasor",
            tool_name="create_folder",
            tool_arguments={"path": str(folder)},
        ),
        MissionStep(
            step_id="step_get_system_info",
            title="Sistem bilgisi",
            tool_name="get_system_info",
            depends_on=["step_create_folder"],
        ),
        MissionStep(
            step_id="step_prepare_content",
            title="Sistem bilgilerini dosya icerigi olarak hazirla",
            action=StepAction.LOGICAL,
            depends_on=["step_get_system_info"],
        ),
        MissionStep(
            step_id="step_write_file",
            title="Yaz",
            tool_name="write_file",
            tool_arguments={"path": str(file_path), "content": ""},
            depends_on=["step_prepare_content"],
        ),
    ]
    mission_steps = finalize_dynamic_file_plan(SYSTEM_INFO_GOAL, ai_plan)

    store = MissionStore()
    mission = store.create_mission(SYSTEM_INFO_GOAL)
    mission.steps = mission_steps
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW

    async def fake_execute(call, run_id="", skip_approval=False):
        if call.name == "create_folder":
            folder.mkdir(parents=True, exist_ok=True)
            return ToolResultPayload(tool_call_id="1", success=True, output={"path": str(folder)})
        if call.name == "get_system_info":
            return ToolResultPayload(tool_call_id="2", success=True, output=dict(SAMPLE_SYSTEM_INFO))
        if call.name == "write_file":
            content = str(call.arguments.get("content") or "")
            assert not is_placeholder_content(content)
            assert "DESKTOP-TEST" in content
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return ToolResultPayload(tool_call_id="3", success=True, output={"path": str(file_path)})
        return ToolResultPayload(tool_call_id="x", success=False, error="unknown")

    executor.execute_tool_call = AsyncMock(side_effect=fake_execute)
    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MissionPlanner(MagicMock(), registry))
    assert result.success is True
    assert file_path.exists()
    assert "DESKTOP-TEST" in file_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_engine_unresolved_write_dependency_fails(registry, mission_root):
    from hermes.security.policy_engine import PolicyDecision

    store = MissionStore()
    mission = store.create_mission(SYSTEM_INFO_GOAL)
    mission.steps = [
        MissionStep(
            step_id="write_system_file",
            title="Write",
            tool_name="write_file",
            tool_arguments={"path": "Desktop/HermesAgentTest/sistem.txt", "content": ""},
            argument_bindings=[
                {
                    "argument": "content",
                    "source_step_id": "missing_prepare_step",
                    "source_field": SOURCE_FIELD_PREPARED_CONTENT,
                }
            ],
        )
    ]
    mission.plan_validated = True
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    executor.execute_tool_call = AsyncMock()

    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MissionPlanner(MagicMock(), registry))
    assert result.success is False
    executor.execute_tool_call.assert_not_called()
    loaded = store.load(mission.mission_id)
    assert loaded is not None
    assert loaded.status.value == "failed"
