"""Generic step-output → next-step input binding, without tool-name switches."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes.mission.engine import MissionEngine
from hermes.mission.models import MissionStep, MissionStepStatus, StepAction
from hermes.mission.step_context import (
    record_search_files_context,
    resolve_argument_binding,
    resolve_step_tool_arguments,
)
from hermes.mission.store import MissionStore
from hermes.tools.capabilities import (
    bindable_field,
    extract_structured_value,
    select_tool_accepting,
)
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


def test_extract_structured_value_from_search_matches():
    output = {
        "folder": "C:/Downloads",
        "pattern": "*.pdf",
        "count": 1,
        "matches": [{"path": "C:/Downloads/a.pdf", "name": "a.pdf"}],
        "verified": True,
    }
    assert extract_structured_value(output, "path") == "C:/Downloads/a.pdf"
    assert extract_structured_value(output, "url") is None


def test_extract_structured_value_from_verified_output_wrapper():
    output = {
        "verified_output": {
            "matches": [{"path": "C:/x/report.txt", "name": "report.txt"}],
            "count": 1,
        }
    }
    assert extract_structured_value(output, "path") == "C:/x/report.txt"


def test_search_to_open_is_a_field_binding_not_a_tool_pair(registry):
    assert bindable_field("filesystem.search", "filesystem.open", registry) == "path"
    assert select_tool_accepting(registry, "filesystem.open", "path") == "open_path"
    assert bindable_field("filesystem.list", "browser.navigate", registry) is None


def test_resolve_argument_binding_uses_verified_search_path(mission_root):
    store = MissionStore()
    mission = store.create_mission("bul ve ac")
    search = MissionStep(
        step_id="search",
        title="search",
        status=MissionStepStatus.COMPLETED,
        action=StepAction.TOOL,
        tool_name="search_files",
        metadata={"capability": "filesystem.search"},
    )
    mission.steps = [search]
    record_search_files_context(
        mission,
        search,
        {
            "folder": "C:/Downloads",
            "pattern": "*",
            "count": 1,
            "matches": [{"path": "C:/Downloads/a.pdf", "name": "a.pdf"}],
        },
    )
    found = resolve_argument_binding(
        mission,
        {
            "argument": "path",
            "source_step_id": "search",
            "source_field": "path",
        },
    )
    assert found == "C:/Downloads/a.pdf"


def test_empty_search_does_not_invent_a_path(mission_root):
    store = MissionStore()
    mission = store.create_mission("bul ve ac")
    search = MissionStep(
        step_id="search",
        title="search",
        status=MissionStepStatus.COMPLETED,
        action=StepAction.TOOL,
        tool_name="search_files",
    )
    mission.steps = [search]
    record_search_files_context(
        mission,
        search,
        {"folder": "C:/Downloads", "pattern": "*", "count": 0, "matches": []},
    )
    found = resolve_argument_binding(
        mission,
        {
            "argument": "path",
            "source_step_id": "search",
            "source_field": "path",
        },
    )
    assert found is None


@pytest.mark.asyncio
async def test_unresolved_binding_fails_the_step_instead_of_completing(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission("pdf bul ve ac")
    mission.working_context["required_capabilities"] = [
        "filesystem.search",
        "filesystem.open",
    ]
    search = MissionStep(
        step_id="search",
        title="search",
        status=MissionStepStatus.COMPLETED,
        action=StepAction.TOOL,
        tool_name="search_files",
        verification_status="verified",
        metadata={"capability": "filesystem.search"},
    )
    open_step = MissionStep(
        step_id="open",
        title="open",
        action=StepAction.TOOL,
        tool_name="open_path",
        tool_arguments={},
        depends_on=["search"],
        argument_bindings=[
            {
                "argument": "path",
                "source_step_id": "search",
                "source_field": "path",
            }
        ],
        metadata={"capability": "filesystem.open"},
    )
    mission.steps = [search, open_step]
    mission.plan_validated = True
    record_search_files_context(
        mission,
        search,
        {"folder": str(mission_root), "pattern": "*", "count": 0, "matches": []},
    )
    store.save(mission)

    engine = MissionEngine(store, registry, MagicMock())
    result = await engine._execute_plan(mission)  # noqa: SLF001

    loaded = store.load(mission.mission_id)
    assert result.success is False
    assert loaded.status.value != "completed"
    assert loaded.steps[1].status == MissionStepStatus.FAILED
    assert "girdi uretilemedi" in (loaded.steps[1].result_summary or "")


def test_resolve_step_tool_arguments_fills_bound_path(mission_root):
    store = MissionStore()
    mission = store.create_mission("ac")
    search = MissionStep(
        step_id="search",
        title="search",
        status=MissionStepStatus.COMPLETED,
        tool_name="search_files",
    )
    open_step = MissionStep(
        step_id="open",
        title="open",
        tool_name="open_path",
        tool_arguments={},
        argument_bindings=[
            {
                "argument": "path",
                "source_step_id": "search",
                "source_field": "path",
            }
        ],
    )
    mission.steps = [search, open_step]
    record_search_files_context(
        mission,
        search,
        {
            "folder": "C:/Downloads",
            "pattern": "*",
            "count": 1,
            "matches": [{"path": "C:/Downloads/a.pdf", "name": "a.pdf"}],
        },
    )
    args = resolve_step_tool_arguments(open_step, mission)
    assert args["path"] == "C:/Downloads/a.pdf"
