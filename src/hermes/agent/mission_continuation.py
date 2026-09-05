"""Phase 8 — detect when a user message continues an active mission."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

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


class PendingReplyKind(StrEnum):
    CONTINUE = "continue"
    NEW_TASK = "new_task"
    CANCEL = "cancel"


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


def classify_pending_user_message(message: str, mission: Any = None) -> PendingReplyKind:
    """Is this a choice among pending work, a new goal, or a cancel?

    Uses existing reference/intent features. A WAITING mission only resumes
    when the message is a selection, not when it starts another task.
    """
    from hermes.agent.application_catalog import is_web_or_app_open_message
    from hermes.agent.local_intent import guess_local_action, match_local_intent
    from hermes.agent.mission_flow import is_mission_cancel_message
    from hermes.screen.reference import (
        extract_reference_features,
        is_screen_perception_task,
        requests_screen_rescan,
    )

    text = (message or "").strip()
    if not text:
        return PendingReplyKind.NEW_TASK
    if is_mission_cancel_message(text):
        return PendingReplyKind.CANCEL

    features = extract_reference_features(text)
    if features.wants_navigate or is_web_or_app_open_message(text):
        return PendingReplyKind.NEW_TASK
    if requests_screen_rescan(text):
        return PendingReplyKind.NEW_TASK
    if features.type_hints and features.text_tokens:
        return PendingReplyKind.NEW_TASK

    local = match_local_intent(text) or guess_local_action(text)
    if local is not None and local.request.name not in {"click_text", "click"}:
        return PendingReplyKind.NEW_TASK

    if mission is not None:
        if features.ordinal is not None or features.spatial or (
            features.deictic and not features.type_hints
        ):
            return PendingReplyKind.CONTINUE
        if re.fullmatch(r"\d{1,2}\s*\.?", text):
            return PendingReplyKind.CONTINUE

    if is_screen_perception_task(text):
        return PendingReplyKind.NEW_TASK
    return PendingReplyKind.NEW_TASK


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
        kind = classify_pending_user_message(text, active)
        if kind is PendingReplyKind.CONTINUE:
            return ContinuationDecision(
                continue_mission_id=active.mission_id,
                is_continuation=True,
                reason="waiting_for_user",
            )
        return ContinuationDecision(reason=str(kind))

    if is_mission_continuation_message(text):
        return ContinuationDecision(
            continue_mission_id=active.mission_id,
            is_continuation=True,
            reason="continuation_phrase",
        )

    return ContinuationDecision()
