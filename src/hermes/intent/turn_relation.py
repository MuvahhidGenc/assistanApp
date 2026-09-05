"""How the new user turn relates to the current objective.

This is the semantic owner for continue / revise / cancel / new_task.
It does not grow per-sentence regex. Constraint signals come from the
existing goal parser and path extractors; wording variants belong to the
understanding layer when it is reachable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from hermes.context.task_state import TaskParameters, normalize_format


class TurnKind(StrEnum):
    NEW_TASK = "new_task"
    REVISE = "revise"
    CONTINUE = "continue"
    CANCEL = "cancel"


@dataclass
class TurnRelation:
    kind: TurnKind
    parameters: TaskParameters = field(default_factory=TaskParameters)
    source: str = ""
    replay_goal: str = ""
    invalidate_path: str | None = None


def has_negative_polarity(message: str) -> bool:
    """Prohibitive morphology on the main verb, not a per-site synonym list."""
    tokens = [item for item in (message or "").replace("'", " ").split() if item]
    if not tokens:
        return False
    last = tokens[-1].casefold().strip(".,!?")
    if last in {"ama", "ama,", "değil", "degil"}:
        return False
    return last.endswith("ma") or last.endswith("me")


def classify_turn_relation(
    message: str,
    ctx: Any,
    *,
    parsed: Any | None = None,
    intent: Any | None = None,
    waiting_mission: Any | None = None,
) -> TurnRelation:
    """Decide what this turn does to the current objective.

    Preference: LLM-declared relation on `intent`, then structural signals
    already produced by parse_goal / pending classification. History slots
    never become the active target here.
    """
    text = (message or "").strip()
    if not text:
        return TurnRelation(kind=TurnKind.NEW_TASK, source="empty")

    from hermes.agent.mission_flow import is_mission_cancel_message

    if is_mission_cancel_message(text):
        return TurnRelation(kind=TurnKind.CANCEL, source="mission_cancel")

    llm = _from_intent(intent, ctx)
    if llm is not None:
        return llm

    # Waiting: cancel / revise own the turn before spatial continue, so
    # "onu test12 icine yap" is not swallowed as a pending choice answer.
    if waiting_mission is not None:
        from hermes.agent.mission_continuation import (
            PendingReplyKind,
            classify_pending_user_message,
        )

        pending = classify_pending_user_message(text, waiting_mission)
        if pending is PendingReplyKind.CANCEL:
            return TurnRelation(kind=TurnKind.CANCEL, source="pending_cancel")
        revision = _structural_revision(text, ctx, parsed)
        if revision is not None:
            return revision
        if pending is PendingReplyKind.CONTINUE:
            return TurnRelation(kind=TurnKind.CONTINUE, source="pending_continue")
        if pending is PendingReplyKind.NEW_TASK:
            return TurnRelation(kind=TurnKind.NEW_TASK, source="pending_new_task")

    from hermes.screen.reference import is_screen_perception_task, looks_like_screen_reference

    if looks_like_screen_reference(text) or is_screen_perception_task(text):
        return TurnRelation(kind=TurnKind.NEW_TASK, source="screen_task")

    revision = _structural_revision(text, ctx, parsed)
    if revision is not None:
        return revision
    return TurnRelation(kind=TurnKind.NEW_TASK, source="default_new_task")


def _from_intent(intent: Any, ctx: Any) -> TurnRelation | None:
    if intent is None:
        return None
    raw = getattr(intent, "relation", "") or ""
    if isinstance(intent, dict):
        raw = str(intent.get("relation") or "")
    kind = _parse_kind(raw)
    if kind is None:
        return None
    revisions = getattr(intent, "revisions", None)
    if revisions is None and isinstance(intent, dict):
        revisions = intent.get("revisions")
    params = TaskParameters.from_dict(revisions if isinstance(revisions, dict) else {})
    if kind is TurnKind.REVISE:
        params = _fill_revision_defaults(params, ctx)
    return TurnRelation(kind=kind, parameters=params, source="llm")


def _parse_kind(raw: str) -> TurnKind | None:
    token = str(raw or "").strip().casefold()
    mapping = {
        "new_task": TurnKind.NEW_TASK,
        "revise": TurnKind.REVISE,
        "revision": TurnKind.REVISE,
        "continue": TurnKind.CONTINUE,
        "cancel": TurnKind.CANCEL,
    }
    return mapping.get(token)


def _has_current_objective(ctx: Any) -> bool:
    if ctx is None:
        return False
    if getattr(ctx, "current_objective", None):
        return True
    intent = getattr(ctx, "last_intent", None)
    return isinstance(intent, dict) and bool(intent.get("goal"))


def _current_goal(ctx: Any) -> str:
    if ctx is None:
        return ""
    if getattr(ctx, "current_objective", None):
        return str(ctx.current_objective)
    intent = getattr(ctx, "last_intent", None)
    if isinstance(intent, dict):
        return str(intent.get("goal") or "")
    return ""


def _document_task(ctx: Any) -> bool:
    params = getattr(ctx, "task_parameters", None)
    if isinstance(params, dict) and params.get("action") == "create_document":
        return True
    if hasattr(params, "action") and getattr(params, "action", "") == "create_document":
        return True
    intent = getattr(ctx, "last_intent", None)
    if not isinstance(intent, dict):
        return False
    caps = " ".join(str(item) for item in (intent.get("required_capabilities") or []))
    return any(token in caps for token in ("document.", "filesystem.write", "filesystem.create"))


def _structural_revision(text: str, ctx: Any, parsed: Any) -> TurnRelation | None:
    if not _has_current_objective(ctx):
        return None

    params = TaskParameters()
    fmt = ""
    destination = ""
    if parsed is not None:
        fmt = normalize_format(
            getattr(parsed, "file_type", "") or (getattr(parsed, "constraints", {}) or {}).get("file_type")
        )
        actions = [str(item) for item in (getattr(parsed, "required_actions", None) or [])]
        operations = [str(item) for item in (getattr(parsed, "operations", None) or [])]
    else:
        actions = []
        operations = []

    # Parsed system folders (Videos from "videolardan") are not task containers.
    known_folder = _mentioned_known_folder(text, ctx)
    destination = known_folder or ""

    action_names = {str(item).split(".")[-1] for item in actions}
    if action_names & {"open", "rename", "list", "delete", "copy", "move", "search"}:
        return None
    if "create_folder" in operations and destination and not known_folder:
        return None

    current_fmt = ""
    existing = getattr(ctx, "task_parameters", None)
    if isinstance(existing, dict):
        current_fmt = normalize_format(existing.get("format"))
    elif existing is not None:
        current_fmt = normalize_format(getattr(existing, "format", ""))

    constraint_only = (not actions or action_names <= {"unknown"}) and bool(fmt or destination)
    format_changed = bool(fmt) and fmt != current_fmt
    format_on_document = format_changed and _document_task(ctx) and not action_names & {"create"}
    retarget = bool(known_folder) and not action_names & {"create"}
    if constraint_only and fmt and fmt == current_fmt and not destination:
        return None

    if not (constraint_only or format_on_document or retarget):
        return None

    params.format = fmt
    params.destination = destination
    params = _fill_revision_defaults(params, ctx)
    invalidate = None
    if known_folder:
        current_file = _current_file(ctx)
        if current_file:
            try:
                if Path(current_file).parent.resolve() != Path(known_folder).resolve():
                    invalidate = current_file
            except OSError:
                invalidate = current_file
    return TurnRelation(
        kind=TurnKind.REVISE,
        parameters=params,
        source="structural",
        replay_goal=_current_goal(ctx),
        invalidate_path=invalidate,
    )


def _fill_revision_defaults(params: TaskParameters, ctx: Any) -> TaskParameters:
    existing = TaskParameters.from_dict(
        ctx.task_parameters if isinstance(getattr(ctx, "task_parameters", None), dict) else None
    )
    if hasattr(ctx, "task_parameters") and hasattr(ctx.task_parameters, "to_dict"):
        existing = ctx.task_parameters
    merged = existing.merge(params)
    if not merged.destination:
        getter = getattr(ctx, "focus_container", None)
        container = getter() if callable(getter) else None
        merged.destination = str(container or "") or existing.destination
    if not merged.source_path:
        focus = getattr(ctx, "active_focus", None)
        if focus is not None and getattr(focus, "type", "") == "file":
            merged.source_path = str(focus.identifier)
        elif merged.target:
            merged.source_path = merged.target
    if not merged.filename and merged.source_path:
        merged.filename = Path(merged.source_path).name
    return merged


def _current_file(ctx: Any) -> str | None:
    focus = getattr(ctx, "active_focus", None)
    if focus is not None and getattr(focus, "type", "") == "file":
        return str(focus.identifier)
    return getattr(ctx, "active_file", None)


def _mentioned_known_folder(text: str, ctx: Any) -> str | None:
    from hermes.context.agent_context import find_folder_by_hint, normalize_existing_path
    from hermes.context.folder_reference import extract_named_folder_name

    compact = "".join((text or "").split()).casefold()
    candidates: list[str] = []
    getter = getattr(ctx, "focus_container", None)
    container = getter() if callable(getter) else None
    for path in (
        getattr(ctx, "last_created_folder", None),
        getattr(ctx, "active_folder", None),
        container,
        *(getattr(ctx, "recent_folders", None) or []),
    ):
        if path and path not in candidates:
            candidates.append(str(path))
    for path in candidates:
        name = Path(path).name
        if name and "".join(name.split()).casefold() in compact:
            return find_folder_by_hint(ctx, name) or normalize_existing_path(path)
    named = extract_named_folder_name(text)
    if named:
        return find_folder_by_hint(ctx, named)
    return None
