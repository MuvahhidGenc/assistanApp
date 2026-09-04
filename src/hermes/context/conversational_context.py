from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hermes.client.session_store import load_client_state, save_client_state

_MAX_RECENT = 10
_ENTITY_TYPES = frozenset(
    {
        "file",
        "folder",
        "application",
        "url",
        "browser_tab",
        "browser_page",
        "browser_window",
        "text_content",
        "screen_text",
        "github_repo",
        "process",
        "service",
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_path(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return str(Path(text).resolve())
    except (OSError, ValueError):
        return text.replace("\\", "/")


def _dedupe_append(items: list[str], value: str | None, *, limit: int = _MAX_RECENT) -> list[str]:
    if not value:
        return items
    normalized = _normalize_path(value) or value
    filtered = [item for item in items if item != normalized]
    filtered.insert(0, normalized)
    return filtered[:limit]


@dataclass
class EntityRecord:
    entity_type: str
    identifier: str
    label: str | None = None
    recorded_at: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, str]:
        payload = {
            "entity_type": self.entity_type,
            "identifier": self.identifier,
            "recorded_at": self.recorded_at,
        }
        if self.label:
            payload["label"] = self.label
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityRecord:
        return cls(
            entity_type=str(data.get("entity_type") or "file"),
            identifier=str(data.get("identifier") or ""),
            label=str(data.get("label") or "") or None,
            recorded_at=str(data.get("recorded_at") or _utc_now()),
        )


@dataclass
class ConversationalContext:
    active_mission_id: str | None = None
    active_step_id: str | None = None
    active_folder: str | None = None
    active_file: str | None = None
    last_created_file: str | None = None
    last_modified_file: str | None = None
    last_created_folder: str | None = None
    last_renamed_file: str | None = None
    created_files: list[str] = field(default_factory=list)
    last_tool_name: str | None = None
    last_tool_result: dict[str, Any] | None = None
    last_application: str | None = None
    last_opened_folder: str | None = None
    last_opened_file: str | None = None
    last_action_summary: str = ""
    last_mission_summary: str = ""
    last_task_result: str = ""
    last_intent: dict[str, Any] | None = None
    last_route_kind: str = ""
    last_user_message: str = ""
    suspended_mission_ids: list[str] = field(default_factory=list)
    recent_disambiguation_options: list[dict[str, str]] = field(default_factory=list)
    recent_verified_actions: list[str] = field(default_factory=list)
    recent_actions: list[dict[str, Any]] = field(default_factory=list)
    current_task: str | None = None
    current_objective: str | None = None
    last_successful_mission_id: str | None = None
    last_failed_mission_id: str | None = None
    last_verified_file: str | None = None
    last_verified_folder: str | None = None
    last_browser_page_text: str | None = None
    last_url: str | None = None
    last_browser_url: str | None = None
    recent_files: list[str] = field(default_factory=list)

    @property
    def active_application(self) -> str | None:
        return self.last_application

    @active_application.setter
    def active_application(self, value: str | None) -> None:
        self.last_application = value

    recent_folders: list[str] = field(default_factory=list)
    recent_tool_targets: list[dict[str, Any]] = field(default_factory=list)
    recent_entities: list[EntityRecord] = field(default_factory=list)
    updated_at: str = field(default_factory=_utc_now)

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_mission_id": self.active_mission_id,
            "active_step_id": self.active_step_id,
            "active_folder": self.active_folder,
            "active_file": self.active_file,
            "last_created_file": self.last_created_file,
            "last_modified_file": self.last_modified_file,
            "last_created_folder": self.last_created_folder,
            "last_renamed_file": self.last_renamed_file,
            "created_files": self.created_files,
            "last_tool_name": self.last_tool_name,
            "last_tool_result": self.last_tool_result,
            "last_application": self.last_application,
            "last_opened_folder": self.last_opened_folder,
            "last_opened_file": self.last_opened_file,
            "last_action_summary": self.last_action_summary,
            "last_mission_summary": self.last_mission_summary,
            "last_task_result": self.last_task_result,
            "last_intent": dict(self.last_intent) if self.last_intent else None,
            "last_route_kind": self.last_route_kind,
            "last_user_message": self.last_user_message,
            "suspended_mission_ids": self.suspended_mission_ids,
            "recent_disambiguation_options": self.recent_disambiguation_options,
            "recent_verified_actions": self.recent_verified_actions,
            "recent_actions": self.recent_actions,
            "current_task": self.current_task,
            "current_objective": self.current_objective,
            "last_successful_mission_id": self.last_successful_mission_id,
            "last_failed_mission_id": self.last_failed_mission_id,
            "last_verified_file": self.last_verified_file,
            "last_verified_folder": self.last_verified_folder,
            "last_browser_page_text": self.last_browser_page_text,
            "last_url": self.last_url,
            "last_browser_url": self.last_browser_url,
            "recent_files": self.recent_files,
            "recent_folders": self.recent_folders,
            "recent_tool_targets": self.recent_tool_targets,
            "recent_entities": [item.to_dict() for item in self.recent_entities],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationalContext:
        entities_raw = data.get("recent_entities") or []
        entities = [
            EntityRecord.from_dict(item)
            for item in entities_raw
            if isinstance(item, dict)
        ]
        return cls(
            active_mission_id=data.get("active_mission_id"),
            active_step_id=data.get("active_step_id"),
            active_folder=data.get("active_folder"),
            active_file=data.get("active_file"),
            last_created_file=data.get("last_created_file"),
            last_modified_file=data.get("last_modified_file"),
            last_created_folder=data.get("last_created_folder"),
            last_renamed_file=data.get("last_renamed_file"),
            created_files=[str(item) for item in (data.get("created_files") or [])],
            last_tool_name=data.get("last_tool_name"),
            last_tool_result=dict(data.get("last_tool_result") or {})
            if isinstance(data.get("last_tool_result"), dict)
            else None,
            last_application=data.get("last_application"),
            last_opened_folder=data.get("last_opened_folder"),
            last_opened_file=data.get("last_opened_file"),
            last_action_summary=str(data.get("last_action_summary") or ""),
            last_mission_summary=str(data.get("last_mission_summary") or ""),
            last_task_result=str(data.get("last_task_result") or ""),
            last_intent=dict(data.get("last_intent") or {})
            if isinstance(data.get("last_intent"), dict)
            else None,
            last_route_kind=str(data.get("last_route_kind") or ""),
            last_user_message=str(data.get("last_user_message") or ""),
            suspended_mission_ids=[
                str(item) for item in (data.get("suspended_mission_ids") or []) if str(item).strip()
            ],
            recent_disambiguation_options=[
                dict(item)
                for item in (data.get("recent_disambiguation_options") or [])
                if isinstance(item, dict)
            ],
            recent_verified_actions=[
                str(item) for item in (data.get("recent_verified_actions") or []) if str(item).strip()
            ],
            recent_actions=[
                dict(item) for item in (data.get("recent_actions") or []) if isinstance(item, dict)
            ],
            current_task=data.get("current_task"),
            current_objective=data.get("current_objective"),
            last_successful_mission_id=data.get("last_successful_mission_id"),
            last_failed_mission_id=data.get("last_failed_mission_id"),
            last_verified_file=data.get("last_verified_file"),
            last_verified_folder=data.get("last_verified_folder"),
            last_browser_page_text=data.get("last_browser_page_text"),
            last_url=data.get("last_url"),
            last_browser_url=data.get("last_browser_url"),
            recent_files=[str(item) for item in (data.get("recent_files") or [])],
            recent_folders=[str(item) for item in (data.get("recent_folders") or [])],
            recent_tool_targets=[
                dict(item) for item in (data.get("recent_tool_targets") or []) if isinstance(item, dict)
            ],
            recent_entities=entities,
            updated_at=str(data.get("updated_at") or _utc_now()),
        )

    @classmethod
    def load(cls) -> ConversationalContext:
        raw = load_client_state().get("conversational_context")
        if isinstance(raw, dict):
            return cls.from_dict(raw)
        return cls()

    def reconcile_with_filesystem(self) -> None:
        for attr in (
            "active_file",
            "last_created_file",
            "last_modified_file",
            "last_opened_file",
            "last_renamed_file",
            "last_verified_file",
        ):
            path = getattr(self, attr, None)
            if not path:
                continue
            try:
                if not Path(str(path)).exists():
                    setattr(self, attr, None)
            except OSError:
                setattr(self, attr, None)
        for attr in (
            "active_folder",
            "last_created_folder",
            "last_opened_folder",
            "last_verified_folder",
        ):
            path = getattr(self, attr, None)
            if not path:
                continue
            try:
                if not Path(str(path)).is_dir():
                    setattr(self, attr, None)
            except OSError:
                setattr(self, attr, None)
        self.recent_files = [
            item
            for item in self.recent_files
            if self._path_exists(item, expect_file=True)
        ]
        self.recent_folders = [
            item
            for item in self.recent_folders
            if self._path_exists(item, expect_file=False)
        ]
        self.created_files = [
            item for item in self.created_files if self._path_exists(item, expect_file=True)
        ]
        if self.recent_files:
            newest = self.recent_files[0]
            if self._path_exists(newest, expect_file=True):
                self.last_verified_file = newest
                self.active_file = newest
                if not self.last_created_file:
                    self.last_created_file = newest
        if self.recent_folders:
            newest_folder = self.recent_folders[0]
            if self._path_exists(newest_folder, expect_file=False):
                self.last_verified_folder = newest_folder
                self.active_folder = newest_folder
                if not self.last_created_folder:
                    self.last_created_folder = newest_folder

    @staticmethod
    def _path_exists(raw: str, *, expect_file: bool) -> bool:
        try:
            path = Path(str(raw))
            return path.is_file() if expect_file else path.is_dir()
        except OSError:
            return False

    def snapshot_for_mission(self) -> dict[str, str | None]:
        return {
            "active_file": self.active_file,
            "active_folder": self.active_folder,
            "last_created_file": self.last_created_file,
            "last_created_folder": self.last_created_folder,
            "last_renamed_file": self.last_renamed_file,
            "last_opened_file": self.last_opened_file,
            "last_opened_folder": self.last_opened_folder,
            "last_url": self.last_url,
            "last_browser_url": self.last_browser_url,
            "last_action_summary": self.last_action_summary or None,
            "last_mission_summary": self.last_mission_summary or None,
            "recent_verified_actions": list(self.recent_verified_actions[:6]),
        }

    def save(self) -> None:
        self.touch()
        state = load_client_state()
        state["conversational_context"] = self.to_dict()
        save_client_state(state)

    def record_entity(
        self,
        entity_type: str,
        identifier: str,
        *,
        label: str | None = None,
    ) -> None:
        if entity_type not in _ENTITY_TYPES:
            return
        normalized = _normalize_path(identifier) or identifier
        record = EntityRecord(entity_type=entity_type, identifier=normalized, label=label)
        self.recent_entities = [
            item
            for item in self.recent_entities
            if not (item.entity_type == entity_type and item.identifier == normalized)
        ]
        self.recent_entities.insert(0, record)
        self.recent_entities = self.recent_entities[: _MAX_RECENT * 2]

    def resolved_references(self) -> dict[str, str]:
        refs: dict[str, str] = {}
        if self.active_file:
            refs["target_file"] = self.active_file
        if self.active_folder:
            refs["target_folder"] = self.active_folder
        if self.last_created_file:
            refs["last_created_file"] = self.last_created_file
        if self.last_renamed_file:
            refs["last_renamed_file"] = self.last_renamed_file
        if self.last_created_folder:
            refs["last_created_folder"] = self.last_created_folder
        if self.last_opened_file:
            refs["last_opened_file"] = self.last_opened_file
        if self.last_opened_folder:
            refs["last_opened_folder"] = self.last_opened_folder
        if self.last_url:
            refs["last_url"] = self.last_url
        if self.last_browser_url:
            refs["last_browser_url"] = self.last_browser_url
        return refs

    def record_user_message(self, message: str) -> None:
        self.last_user_message = (message or "").strip()
        self.touch()

    def record_intent(
        self,
        intent: dict[str, Any] | None,
        *,
        route_kind: str = "",
        result: str = "",
    ) -> None:
        """Remember the last understood goal so a follow-up can use it."""
        self.last_intent = dict(intent) if intent else None
        self.last_route_kind = (route_kind or "").strip()
        text = (result or "").strip()
        if text:
            self.last_task_result = text[:2000]
        self.touch()

    def record_action_summary(self, summary: str) -> None:
        text = (summary or "").strip()
        if text:
            self.last_action_summary = text[:500]
            self.touch()

    def record_mission_summary(self, summary: str) -> None:
        text = (summary or "").strip()
        if text:
            self.last_mission_summary = text[:500]
            self.touch()

    def record_disambiguation_options(self, options: list[dict[str, str]]) -> None:
        self.recent_disambiguation_options = options[:6]
        self.touch()

    def record_verified_action(self, summary: str) -> None:
        text = (summary or "").strip()
        if not text:
            return
        self.recent_verified_actions = [text, *self.recent_verified_actions[:7]]
        self.last_action_summary = text[:500]
        self.record_structured_action("verified", text)
        self.touch()

    def record_structured_action(
        self,
        action_type: str,
        summary: str,
        *,
        path: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        from hermes.context.agent_context import action_record

        record = action_record(action_type, summary, path=path, tool_name=tool_name)
        self.recent_actions = [record, *self.recent_actions[: _MAX_RECENT]]
        self.touch()

    def set_current_task(self, task: str | None) -> None:
        text = (task or "").strip()
        self.current_task = text[:500] if text else None
        self.touch()

    def set_current_objective(self, objective: str | None) -> None:
        text = (objective or "").strip()
        self.current_objective = text[:500] if text else None
        if text:
            self.current_task = text[:500]
        self.touch()

    def record_mission_outcome(self, mission_id: str, *, success: bool) -> None:
        if success:
            self.last_successful_mission_id = mission_id
        else:
            self.last_failed_mission_id = mission_id
        if success:
            self.current_task = None
        self.touch()

    def natural_action_summary(self) -> str:
        mission_summary = (self.last_mission_summary or "").strip()
        if mission_summary and not re.search(
            r"\b(open_app|write_file|create_folder|rename_path|open_path|tool)\b",
            mission_summary,
            re.IGNORECASE,
        ):
            return mission_summary
        actions = [item.strip() for item in self.recent_verified_actions if item.strip()]
        if actions:
            return ". ".join(actions[:6]) + "."
        summary = (self.last_action_summary or "").strip()
        return summary

    def update_from_tool(
        self, tool_name: str, output: Any, *, success: bool, verified: bool = False
    ) -> None:
        if not success:
            return
        if isinstance(output, dict) and output.get("verified") is True:
            verified = True
        if not verified:
            return
        self.last_tool_name = tool_name
        if isinstance(output, dict):
            self.last_tool_result = {
                key: output[key]
                for key in list(output.keys())[:20]
            }
        else:
            self.last_tool_result = {"value": output}

        path_value = None
        if isinstance(output, dict):
            path_value = output.get("path") or output.get("target") or output.get("destination")

        if tool_name == "create_folder" and path_value:
            folder = _normalize_path(str(path_value))
            if folder:
                self.active_folder = folder
                self.last_created_folder = folder
                self.last_verified_folder = folder
                self.recent_folders = _dedupe_append(self.recent_folders, folder)
                self.record_entity("folder", folder, label=Path(folder).name)

        elif tool_name in ("write_file", "create_file") and path_value:
            file_path = _normalize_path(str(path_value))
            if file_path:
                self.active_file = file_path
                self.last_created_file = file_path
                self.last_modified_file = file_path
                self.last_verified_file = file_path
                self.created_files = _dedupe_append(self.created_files, file_path)
                self.recent_files = _dedupe_append(self.recent_files, file_path)
                parent = str(Path(file_path).parent)
                self.active_folder = _normalize_path(parent) or parent
                self.last_verified_folder = self.active_folder
                self.recent_folders = _dedupe_append(self.recent_folders, self.active_folder)
                self.record_entity("file", file_path, label=Path(file_path).name)

        elif tool_name == "open_path" and path_value:
            opened = _normalize_path(str(path_value))
            if opened:
                if Path(opened).is_dir() or opened.endswith(("/", "\\")):
                    self.active_folder = opened
                    self.last_opened_folder = opened
                    self.last_verified_folder = opened
                    self.recent_folders = _dedupe_append(self.recent_folders, opened)
                    self.record_entity("folder", opened, label=Path(opened).name)
                else:
                    self.active_file = opened
                    self.last_opened_file = opened
                    self.last_verified_file = opened
                    self.recent_files = _dedupe_append(self.recent_files, opened)
                    self.record_entity("file", opened, label=Path(opened).name)

        elif tool_name == "read_file" and isinstance(output, dict):
            file_path = _normalize_path(str(output.get("path") or path_value or ""))
            if file_path:
                self.active_file = file_path
                self.last_opened_file = file_path
                self.recent_files = _dedupe_append(self.recent_files, file_path)
                self.record_entity("file", file_path, label=Path(file_path).name)

        elif tool_name in ("copy_file", "move_file", "rename_path") and isinstance(output, dict):
            dest = output.get("destination") or output.get("path")
            source = output.get("source")
            if dest:
                dest_path = _normalize_path(str(dest))
                verified_rename = tool_name == "rename_path" and output.get("verified") is True
                if dest_path and (Path(dest_path).is_file() or verified_rename):
                    self.active_file = dest_path
                    self.last_created_file = dest_path
                    self.last_modified_file = dest_path
                    self.last_opened_file = dest_path
                    self.last_verified_file = dest_path
                    if verified_rename:
                        self.last_renamed_file = dest_path
                    self.created_files = _dedupe_append(self.created_files, dest_path)
                    if source:
                        old = _normalize_path(str(source))
                        if old:
                            self.recent_files = [
                                item for item in self.recent_files if item != old
                            ]
                    self.recent_files = _dedupe_append(self.recent_files, dest_path)
                    self.record_entity("file", dest_path, label=Path(dest_path).name)
                elif dest_path:
                    self.active_folder = dest_path
                    self.recent_folders = _dedupe_append(self.recent_folders, dest_path)

        elif tool_name == "delete_path" and isinstance(output, dict):
            deleted_path = _normalize_path(str(output.get("path") or path_value or ""))
            if deleted_path:
                if self.active_file == deleted_path:
                    self.active_file = None
                if self.last_opened_file == deleted_path:
                    self.last_opened_file = None
                if self.last_created_file == deleted_path:
                    self.last_created_file = None
                if self.last_modified_file == deleted_path:
                    self.last_modified_file = None
                self.recent_files = [item for item in self.recent_files if item != deleted_path]

        elif tool_name == "search_files" and isinstance(output, dict):
            matches = output.get("matches") or []
            if isinstance(matches, list) and len(matches) == 1 and isinstance(matches[0], dict):
                match_path = matches[0].get("path")
                if match_path:
                    self.active_file = _normalize_path(str(match_path))
            elif len(matches) > 1:
                options = [
                    {"label": str(item.get("name") or ""), "path": str(item.get("path") or "")}
                    for item in matches[:4]
                    if isinstance(item, dict)
                ]
                self.record_disambiguation_options(
                    [{"label": opt["label"], "message": f"{opt['label']} dosyasini ac"} for opt in options if opt["label"]]
                )

        elif tool_name == "open_app" and isinstance(output, dict):
            app_name = str(output.get("app") or output.get("name") or "").strip()
            if app_name:
                self.last_application = app_name
                self.record_entity("application", app_name, label=app_name)

        elif tool_name == "install_program" and isinstance(output, dict):
            package = str(output.get("package") or output.get("winget_id") or "").strip()
            if package:
                self.last_application = package
                self.record_entity("application", package, label=package)

        elif tool_name == "git_clone" and path_value:
            clone_path = _normalize_path(str(path_value))
            if clone_path:
                self.recent_tool_targets = _dedupe_append_dict(
                    self.recent_tool_targets,
                    {"tool": "git_clone", "path": clone_path},
                )
                self.record_entity("github_repo", clone_path, label=Path(clone_path).name)

        elif tool_name == "open_url" and isinstance(output, dict):
            url = str(output.get("url") or "").strip()
            if url:
                self.last_url = url
                self.last_browser_url = url
                self.record_entity("url", url, label=url[:80])
                self.record_entity("browser_page", url, label=url[:80])

        elif tool_name == "read_screen_text" and isinstance(output, dict):
            from hermes.mission.reality_verification import normalize_visible_page_text

            text, _error = normalize_visible_page_text(output)
            window_title = str(output.get("window_title") or output.get("title") or "").strip()
            if window_title:
                self.record_entity("browser_window", window_title, label=window_title[:80])
            if text:
                self.last_browser_page_text = text[:4000]
                self.record_entity("screen_text", text[:500], label="Sayfa metni")
                self.record_entity("browser_page", text[:500], label="Sayfa icerigi")
                payload = dict(output)
                payload["content_type"] = "screen_text"
                self.last_tool_result = payload

        if path_value:
            self.recent_tool_targets = _dedupe_append_dict(
                self.recent_tool_targets,
                {"tool": tool_name, "path": _normalize_path(str(path_value)) or str(path_value)},
            )
        self.touch()

    def sync_from_mission(self, mission: Any) -> None:
        from hermes.mission.models import MissionStepStatus

        self.active_mission_id = getattr(mission, "mission_id", None)
        wc = getattr(mission, "working_context", None) or {}
        if isinstance(wc, dict):
            last_created = wc.get("last_created_file")
            if last_created:
                self.last_created_file = str(last_created)
            refs = wc.get("resolved_references")
            if isinstance(refs, dict):
                if refs.get("last_created_file"):
                    self.last_created_file = str(refs["last_created_file"])
            created = wc.get("created_files")
            if isinstance(created, list):
                for item in created:
                    normalized = _normalize_path(str(item)) or str(item)
                    if normalized:
                        self.created_files = _dedupe_append(self.created_files, normalized)
            if self.last_created_file:
                self.created_files = _dedupe_append(
                    self.created_files, self.last_created_file
                )
            for key in ("last_url", "last_browser_url"):
                value = str(wc.get(key) or (refs or {}).get(key) or "").strip()
                if value:
                    setattr(self, key, value)
        for step in getattr(mission, "steps", []) or []:
            if step.status != MissionStepStatus.COMPLETED:
                continue
            if not step.tool_name:
                continue
            output = step.metadata.get("tool_output")
            if output is None and isinstance(step.observation, dict):
                output = step.observation.get("data") or step.observation
            self.update_from_tool(step.tool_name, output, success=True, verified=True)


def _dedupe_append_dict(items: list[dict[str, Any]], value: dict[str, Any]) -> list[dict[str, Any]]:
    key = (value.get("tool"), value.get("path"))
    filtered = [item for item in items if (item.get("tool"), item.get("path")) != key]
    filtered.insert(0, value)
    return filtered[:_MAX_RECENT]
