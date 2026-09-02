from __future__ import annotations

import json

import pytest

from hermes.mission.models import (
    MISSION_SCHEMA_VERSION,
    Mission,
    MissionStatus,
    MissionStep,
    MissionStepStatus,
)
from hermes.mission.selection import (
    is_fast_path_candidate,
    should_create_mission,
    should_route_to_mission,
)
from hermes.mission.store import MissionStore


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


def test_mission_model_roundtrip():
    mission = Mission.create("Standart PC kurulumu yap")
    mission.steps.append(
        MissionStep(step_id="step-1", title="Sistem analizi", status=MissionStepStatus.PENDING)
    )
    mission.memory_refs.append("episodic:setup-001")
    mission.working_context["hostname"] = "DESKTOP-TEST"

    restored = Mission.from_dict(mission.to_dict())
    assert restored.mission_id == mission.mission_id
    assert restored.user_goal == mission.user_goal
    assert restored.schema_version == MISSION_SCHEMA_VERSION
    assert restored.memory_refs == ["episodic:setup-001"]
    assert restored.working_context["hostname"] == "DESKTOP-TEST"
    assert len(restored.steps) == 1


def test_mission_progress_ratio():
    mission = Mission.create("test")
    assert mission.progress_ratio == 0.0

    mission.steps = [
        MissionStep(step_id="s1", title="a", status=MissionStepStatus.COMPLETED),
        MissionStep(step_id="s2", title="b", status=MissionStepStatus.PENDING),
    ]
    assert mission.progress_ratio == 0.5

    mission.status = MissionStatus.COMPLETED
    mission.steps = []
    assert mission.progress_ratio == 1.0


def test_fast_path_dns_not_mission():
    assert is_fast_path_candidate("dns degistir google yap") is True
    assert should_create_mission("dns degistir google yap") is False
    assert should_route_to_mission("dns degistir google yap") is False


@pytest.mark.parametrize(
    "message",
    [
        "Standart PC kurulumu yap",
        "Bu GitHub reposunu analiz et, kur ve calistir",
        "Bu bilgisayari analiz et ve guvenli sekilde optimize et",
        "Bu siteyi favoriye ekle ve masaustune uygulama olarak olustur",
    ],
)
def test_complex_goals_create_mission(message: str):
    assert should_create_mission(message) is True
    assert should_route_to_mission(message) is True


def test_mission_store_create_persist_load(mission_root):
    store = MissionStore()
    mission = store.create_mission("Standart PC kurulumu yap")
    assert mission.status == MissionStatus.PLANNING

    loaded = store.load(mission.mission_id)
    assert loaded is not None
    assert loaded.user_goal == mission.user_goal
    assert (mission_root / f"{mission.mission_id}.json").exists()


def test_mission_store_active_and_resume(mission_root):
    store = MissionStore()
    mission = store.create_mission("Repo analiz et")
    assert store.load_active() is not None
    assert store.load_active().mission_id == mission.mission_id

    store.update_status(mission.mission_id, MissionStatus.PAUSED)
    resumed = store.resume(mission.mission_id)
    assert resumed is not None
    assert resumed.status == MissionStatus.RUNNING

    store.update_status(mission.mission_id, MissionStatus.COMPLETED, summary="Tamamlandi")
    assert store.load_active() is None
    assert store.resume(mission.mission_id) is None


def test_mission_store_survives_restart(mission_root):
    store_a = MissionStore()
    mission = store_a.create_mission("Uzun gorev")
    store_a.add_step(mission.mission_id, "Adim 1")
    store_a.update_status(mission.mission_id, MissionStatus.PAUSED)

    store_b = MissionStore()
    loaded = store_b.load(mission.mission_id)
    assert loaded is not None
    assert loaded.status == MissionStatus.PAUSED
    assert len(loaded.steps) == 1

    resumed = store_b.resume(mission.mission_id)
    assert resumed is not None
    assert store_b.load_active() is not None


def test_mission_store_steps_and_tool_results(mission_root):
    store = MissionStore()
    mission = store.create_mission("Kurulum")
    store.add_step(mission.mission_id, "Chrome kur", tool_name="install_package")
    updated = store.update_step_status(
        mission.mission_id,
        "step-1",
        MissionStepStatus.COMPLETED,
        result_summary="Kuruldu",
    )
    assert updated is not None
    assert updated.steps[0].status == MissionStepStatus.COMPLETED

    store.record_tool_result(mission.mission_id, "install_package", success=True, output={"ok": True})
    loaded = store.load(mission.mission_id)
    assert loaded is not None
    assert len(loaded.tool_results) == 1


def test_mission_index_persisted(mission_root):
    store = MissionStore()
    mission = store.create_mission("Index test")
    index = json.loads((mission_root / "index.json").read_text(encoding="utf-8"))
    assert mission.mission_id in index["mission_ids"]
    assert index["active_mission_id"] == mission.mission_id


def test_ui_state_mission_snapshot():
    from hermes.ui.state import UIState

    state = UIState()
    state.set_mission_snapshot(
        mission_id="m-1",
        mission_status="active",
        mission_goal="PC kurulumu",
        mission_progress=0.25,
    )
    snap = state.snapshot()
    assert snap["mission_id"] == "m-1"
    assert snap["mission_status"] == "active"
    assert snap["mission_goal"] == "PC kurulumu"
    assert snap["mission_progress"] == 0.25

    state.clear_mission_snapshot()
    snap = state.snapshot()
    assert snap["mission_id"] is None
    assert snap["mission_progress"] == 0.0
