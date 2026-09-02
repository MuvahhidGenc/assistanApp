"""Phase 8 — detect when a user message continues an active mission."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes.context.conversational_context import ConversationalContext
    from hermes.mission.store import MissionStore

_CONTINUATION_PATTERNS = (
    re.compile(r"^bir\s+de\b", re.IGNORECASE),
    re.compile(r"^ayrica\b|^ayrıca\b", re.IGNORECASE),
    re.compile(r"\bozetini\b|\bözetini\b", re.IGNORECASE),
    re.compile(r"onemli\s+olanlari|önemli\s+olanları", re.IGNORECASE),
    re.compile(r"^bunu\s+tamamla\b", re.IGNORECASE),
    re.compile(r"^kalanlari\b|^kalanları\b", re.IGNORECASE),
    re.compile(r"^simdi\s+de\b|^şimdi\s+de\b", re.IGNORECASE),
)

_NEW_GOAL_SIGNALS = (
    re.compile(r"\b(chrome|firefox|edge)\b.*\b(ac|aç)\b", re.IGNORECASE),
    re.compile(r"klas(?:o|ö)r(?:u|ü)?\s+(?:olustur|oluştur)", re.IGNORECASE),
    re.compile(r"masa[uü]st", re.IGNORECASE),
    re.compile(r"\b(kur|install|yukle|yükle)\b", re.IGNORECASE),
)


@dataclass
class ContinuationDecision:
    continue_mission_id: str | None = None
    is_continuation: bool = False
    reason: str = ""


def is_mission_continuation_message(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _CONTINUATION_PATTERNS)


def is_clear_new_goal(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _NEW_GOAL_SIGNALS)


def detect_mission_continuation(
    message: str,
    ctx: ConversationalContext,
    store: MissionStore,
) -> ContinuationDecision:
    """
    When an active mission exists, decide if the message extends it
    rather than starting a new independent task.
    """
    from hermes.agent.mission_flow import is_independent_interrupt
    from hermes.mission.models import MissionStatus

    text = (message or "").strip()
    if not text:
        return ContinuationDecision()

    active = store.load_active()
    if active is None and ctx.active_mission_id:
        active = store.load(ctx.active_mission_id)

    if active is None:
        for mission_id in reversed(ctx.suspended_mission_ids):
            candidate = store.load(mission_id)
            if candidate and candidate.status == MissionStatus.PAUSED:
                active = candidate
                break

    if active is None:
        return ContinuationDecision()

    if active.status in (
        MissionStatus.COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
    ):
        return ContinuationDecision()

    if is_clear_new_goal(text) and is_independent_interrupt(text):
        return ContinuationDecision(reason="independent_new_goal")

    if active.status == MissionStatus.WAITING_FOR_USER:
        return ContinuationDecision(
            continue_mission_id=active.mission_id,
            is_continuation=True,
            reason="waiting_for_user",
        )

    if is_mission_continuation_message(text):
        return ContinuationDecision(
            continue_mission_id=active.mission_id,
            is_continuation=True,
            reason="continuation_phrase",
        )

    return ContinuationDecision()
