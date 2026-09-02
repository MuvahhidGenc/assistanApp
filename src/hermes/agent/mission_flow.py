"""Mission lifecycle commands: resume, cancel, interrupt detection."""
from __future__ import annotations

import re
from dataclasses import dataclass

from hermes.context.conversational_context import ConversationalContext
from hermes.mission.models import Mission, MissionStatus
from hermes.mission.store import MissionStore

_RESUME = re.compile(
    r"^(?:devam\s+et|kald[ıi]ğ[ıi]n\s+yerden\s+devam\s+et|kaldigin\s+yerden\s+devam\s+et|"
    r"son\s+g[öo]reve\s+devam\s+et|bunu\s+tamamla|kalanlar[ıi]\s+yap|kalanlari\s+yap)\.?$",
    re.IGNORECASE,
)
_CANCEL = re.compile(
    r"^(?:iptal\s+et|durdur|vazge[çc]|bu\s+g[öo]revi\s+b[ıi]rak|g[öo]revi\s+iptal\s+et)\.?$",
    re.IGNORECASE,
)
_CONFIRM = re.compile(r"^tamam\.?$", re.IGNORECASE)


@dataclass
class MissionCommand:
    handled: bool = False
    response: str = ""
    resume_mission_id: str | None = None
    cancelled_mission_id: str | None = None
    source: str = ""


def is_mission_resume_message(message: str) -> bool:
    return bool(_RESUME.match((message or "").strip()))


def is_mission_cancel_message(message: str) -> bool:
    return bool(_CANCEL.match((message or "").strip()))


def is_confirmation_ack(message: str) -> bool:
    """True when user sends a bare confirmation that should resume WAITING_FOR_USER only."""
    return bool(_CONFIRM.match((message or "").strip()))


def is_independent_interrupt(message: str) -> bool:
    """True when message is a short standalone task that should suspend an active mission."""
    text = (message or "").strip()
    if not text:
        return False
    if is_mission_resume_message(text) or is_mission_cancel_message(text):
        return False
    if is_confirmation_ack(text):
        return False
    from hermes.mission.selection import is_fast_path_candidate, should_create_mission

    if is_fast_path_candidate(text):
        return True
    if len(text) <= 48 and not should_create_mission(text):
        return True
    return False


def _pick_resume_mission(store: MissionStore, ctx: ConversationalContext) -> Mission | None:
    active = store.load_active()
    if active and active.status in (
        MissionStatus.PAUSED,
        MissionStatus.WAITING_FOR_USER,
        MissionStatus.RUNNING,
        MissionStatus.PLANNING,
        MissionStatus.RECOVERING,
        MissionStatus.CREATED,
    ):
        return active
    if ctx.active_mission_id:
        loaded = store.load(ctx.active_mission_id)
        if loaded and loaded.status not in (
            MissionStatus.COMPLETED,
            MissionStatus.FAILED,
            MissionStatus.CANCELLED,
        ):
            return loaded
    suspended = store.load_suspended()
    if suspended:
        return suspended[-1]
    for mission_id in reversed(store.list_mission_ids()):
        mission = store.load(mission_id)
        if mission is None:
            continue
        if mission.status in (MissionStatus.PAUSED, MissionStatus.WAITING_FOR_USER):
            return mission
    return None


def handle_mission_commands(
    message: str,
    ctx: ConversationalContext,
    store: MissionStore,
) -> MissionCommand:
    text = (message or "").strip()
    if not text:
        return MissionCommand()

    if is_mission_cancel_message(text):
        target = store.load_active() or _pick_resume_mission(store, ctx)
        if target is None and ctx.active_mission_id:
            target = store.load(ctx.active_mission_id)
        if target is None:
            return MissionCommand(
                handled=True,
                response="Iptal edilecek aktif bir gorev bulamadim.",
                source="mission_cancel",
            )
        store.cancel_mission(target.mission_id, reason="Kullanici iptal etti.")
        if ctx.active_mission_id == target.mission_id:
            ctx.active_mission_id = None
            ctx.active_step_id = None
        ctx.suspended_mission_ids = [
            item for item in ctx.suspended_mission_ids if item != target.mission_id
        ]
        ctx.save()
        return MissionCommand(
            handled=True,
            response="Tamam, gorevi iptal ettim.",
            cancelled_mission_id=target.mission_id,
            source="mission_cancel",
        )

    if is_mission_resume_message(text):
        target = _pick_resume_mission(store, ctx)
        if target is None:
            return MissionCommand(
                handled=True,
                response="Devam edecek bir gorev bulamadim.",
                source="mission_resume",
            )
        preview = (target.user_goal or "")[:80]
        return MissionCommand(
            handled=True,
            response=f"Tamam, kaldigim yerden devam ediyorum: {preview}",
            resume_mission_id=target.mission_id,
            source="mission_resume",
        )

    if is_confirmation_ack(text):
        waiting = store.load_active()
        if waiting is None and ctx.active_mission_id:
            candidate = store.load(ctx.active_mission_id)
            if candidate is not None and candidate.status == MissionStatus.WAITING_FOR_USER:
                waiting = candidate
        if waiting is not None and waiting.status == MissionStatus.WAITING_FOR_USER:
            preview = (waiting.user_goal or "")[:80]
            return MissionCommand(
                handled=True,
                response=f"Tamam, devam ediyorum: {preview}",
                resume_mission_id=waiting.mission_id,
                source="mission_confirm",
            )
        return MissionCommand(handled=False, source="mission_confirm_ignored")

    return MissionCommand()


def context_snapshot_from_ctx(ctx: ConversationalContext) -> dict[str, str | None]:
    return {
        "active_file": ctx.active_file,
        "active_folder": ctx.active_folder,
        "last_created_file": ctx.last_created_file,
        "last_created_folder": ctx.last_created_folder,
        "last_opened_file": ctx.last_opened_file,
        "last_opened_folder": ctx.last_opened_folder,
        "last_action_summary": ctx.last_action_summary or None,
        "last_mission_summary": ctx.last_mission_summary or None,
    }


def restore_context_snapshot(ctx: ConversationalContext, snapshot: dict[str, object]) -> None:
    if not isinstance(snapshot, dict):
        return
    for key in (
        "active_file",
        "active_folder",
        "last_created_file",
        "last_created_folder",
        "last_opened_file",
        "last_opened_folder",
    ):
        value = snapshot.get(key)
        if value and isinstance(value, str):
            from pathlib import Path

            try:
                if Path(value).exists():
                    setattr(ctx, key, value)
            except OSError:
                setattr(ctx, key, value)
