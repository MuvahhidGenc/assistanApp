from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hermes.config.paths import ensure_user_dirs, missions_dir, missions_index_path
from hermes.mission.models import (
    MISSION_SCHEMA_VERSION,
    Mission,
    MissionStatus,
    MissionStep,
    MissionStepStatus,
    _utc_now,
)


class MissionStore:
    """Persist, load, and resume versioned mission state."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or missions_dir()
        self._index_path = (root / "index.json") if root else missions_index_path()

    def _ensure_dirs(self) -> None:
        ensure_user_dirs()
        self._root.mkdir(parents=True, exist_ok=True)

    def _mission_path(self, mission_id: str) -> Path:
        return self._root / f"{mission_id}.json"

    def _load_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {
                "schema_version": MISSION_SCHEMA_VERSION,
                "active_mission_id": None,
                "suspended_mission_ids": [],
                "mission_ids": [],
            }
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {
            "schema_version": MISSION_SCHEMA_VERSION,
            "active_mission_id": None,
            "suspended_mission_ids": [],
            "mission_ids": [],
        }

    def _save_index(self, index: dict[str, Any]) -> None:
        self._ensure_dirs()
        index["schema_version"] = MISSION_SCHEMA_VERSION
        with self._index_path.open("w", encoding="utf-8") as handle:
            json.dump(index, handle, indent=2)

    def save(self, mission: Mission) -> None:
        mission.touch()
        self._ensure_dirs()
        path = self._mission_path(mission.mission_id)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(mission.to_dict(), handle, indent=2)

        index = self._load_index()
        mission_ids = [str(item) for item in (index.get("mission_ids") or [])]
        if mission.mission_id not in mission_ids:
            mission_ids.append(mission.mission_id)
        index["mission_ids"] = mission_ids
        if mission.status in (
            MissionStatus.RUNNING,
            MissionStatus.ACTIVE,
            MissionStatus.PLANNING,
            MissionStatus.PAUSED,
            MissionStatus.WAITING_FOR_USER,
            MissionStatus.RECOVERING,
            MissionStatus.CREATED,
        ):
            index["active_mission_id"] = mission.mission_id
        elif index.get("active_mission_id") == mission.mission_id and mission.status in (
            MissionStatus.COMPLETED,
            MissionStatus.FAILED,
            MissionStatus.CANCELLED,
        ):
            index["active_mission_id"] = None
        suspended = [str(item) for item in (index.get("suspended_mission_ids") or [])]
        if mission.status == MissionStatus.PAUSED and mission.mission_id not in suspended:
            suspended.append(mission.mission_id)
        if mission.status in (MissionStatus.RUNNING, MissionStatus.RECOVERING, MissionStatus.WAITING_FOR_USER):
            suspended = [item for item in suspended if item != mission.mission_id]
        index["suspended_mission_ids"] = suspended[-10:]
        self._save_index(index)

    def load(self, mission_id: str) -> Mission | None:
        path = self._mission_path(mission_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return Mission.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def list_mission_ids(self) -> list[str]:
        index = self._load_index()
        return [str(item) for item in (index.get("mission_ids") or [])]

    def load_active(self) -> Mission | None:
        index = self._load_index()
        active_id = str(index.get("active_mission_id") or "").strip()
        if not active_id:
            return None
        mission = self.load(active_id)
        if mission is None:
            return None
        if mission.status in (MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED):
            return None
        return mission

    def list_suspended_ids(self) -> list[str]:
        index = self._load_index()
        return [str(item) for item in (index.get("suspended_mission_ids") or [])]

    def load_suspended(self) -> list[Mission]:
        missions: list[Mission] = []
        for mission_id in reversed(self.list_suspended_ids()):
            mission = self.load(mission_id)
            if mission is not None and mission.status == MissionStatus.PAUSED:
                missions.append(mission)
        return missions

    def cancel_mission(self, mission_id: str, *, reason: str = "") -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        if mission.status in (MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED):
            return mission
        mission.status = MissionStatus.CANCELLED
        mission.last_error = reason[:500] if reason else mission.last_error
        mission.summary = reason[:4000] if reason else mission.summary
        mission.touch()
        self.save(mission)
        return mission

    def suspend_mission(self, mission_id: str, *, reason: str = "") -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        if mission.status in (MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED):
            return None
        mission.status = MissionStatus.PAUSED
        if reason:
            mission.important_decisions.append(
                {"type": "suspended", "reason": reason[:500], "at": _utc_now()}
            )
        mission.touch()
        self.save(mission)
        return mission

    def snapshot_context(self, mission_id: str, snapshot: dict[str, Any]) -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        mission.context_snapshot = dict(snapshot)
        mission.working_context["context_snapshot"] = dict(snapshot)
        mission.touch()
        self.save(mission)
        return mission

    def resume(self, mission_id: str) -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        if mission.status in (MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED):
            return None
        mission.status = MissionStatus.RUNNING
        mission.touch()
        self.save(mission)
        return mission

    def resume_from_user(self, mission_id: str, user_response: str) -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        if mission.status != MissionStatus.WAITING_FOR_USER:
            return self.resume(mission_id)
        mission.user_interventions.append(
            {
                "type": "user_response",
                "response": user_response[:2000],
                "at": _utc_now(),
            }
        )
        mission.status = MissionStatus.RUNNING
        mission.waiting_for_user_reason = None
        mission.recovery_finished_at = _utc_now()
        mission.touch()
        self.save(mission)
        return mission

    def create_mission(
        self,
        user_goal: str,
        *,
        steps: list[MissionStep] | None = None,
        working_context: dict[str, Any] | None = None,
    ) -> Mission:
        mission = Mission.create(user_goal, initial_steps=steps or [])
        if working_context:
            mission.working_context.update(working_context)
        if mission.status == MissionStatus.CREATED and not steps:
            mission.status = MissionStatus.PLANNING
        self.save(mission)
        return mission

    def update_status(
        self,
        mission_id: str,
        status: MissionStatus,
        *,
        summary: str | None = None,
    ) -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        mission.status = status
        if summary is not None:
            mission.summary = summary[:4000]
        mission.touch()
        self.save(mission)
        return mission

    def add_step(
        self,
        mission_id: str,
        title: str,
        *,
        tool_name: str | None = None,
        tool_arguments: dict[str, Any] | None = None,
    ) -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        step = MissionStep(
            step_id=f"step-{len(mission.steps) + 1}",
            title=title,
            tool_name=tool_name,
            tool_arguments=tool_arguments or {},
        )
        mission.steps.append(step)
        if mission.current_step_id is None:
            mission.current_step_id = step.step_id
        if mission.status == MissionStatus.PLANNING:
            mission.status = MissionStatus.RUNNING
        mission.touch()
        self.save(mission)
        return mission

    def update_step_status(
        self,
        mission_id: str,
        step_id: str,
        status: MissionStepStatus,
        *,
        result_summary: str | None = None,
    ) -> Mission | None:
        mission = self.load(mission_id)
        if mission is None:
            return None
        for step in mission.steps:
            if step.step_id != step_id:
                continue
            step.status = status
            if status == MissionStepStatus.RUNNING and not step.started_at:
                step.started_at = _utc_now()
            if status in (MissionStepStatus.COMPLETED, MissionStepStatus.FAILED, MissionStepStatus.SKIPPED):
                step.completed_at = _utc_now()
            if result_summary is not None:
                step.result_summary = result_summary[:2000]
            mission.current_step_id = step_id
            mission.touch()
            self.save(mission)
            return mission
        return None

    def record_tool_result(
        self,
        mission_id: str,
        tool_name: str,
        *,
        success: bool,
        output: Any = None,
        error: str | None = None,
        mission: Mission | None = None,
    ) -> Mission | None:
        if mission is not None and mission.mission_id == mission_id:
            mission.tool_results.append(
                {
                    "tool_name": tool_name,
                    "success": success,
                    "output": output,
                    "error": error,
                    "recorded_at": _utc_now(),
                }
            )
            mission.touch()
            self.save(mission)
            return mission

        loaded = self.load(mission_id)
        if loaded is None:
            return None
        loaded.tool_results.append(
            {
                "tool_name": tool_name,
                "success": success,
                "output": output,
                "error": error,
                "recorded_at": _utc_now(),
            }
        )
        loaded.touch()
        self.save(loaded)
        return loaded
