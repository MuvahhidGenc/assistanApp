"""Presentation-only speech shaping for authoritative V3 responses.

This module deliberately does not infer intent, select tools, or reinterpret
the user's request. Semantic decisions belong to ``ReasoningRuntime``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any


_TOOL_NAMES = (
    "write_file",
    "create_file",
    "open_path",
    "create_folder",
    "list_windows",
    "list_directory",
    "delete_path",
    "open_app",
    "run_command",
    "execution_target",
    "tool_call",
    "verified",
    "verification",
)
_TECHNICAL_STATUS_MARKERS = (
    "anladim, isleme basliyorum",
    "referans cozuluyor",
    "referans netlestirme",
    "yerel tool",
    "tool calistir",
    "dogrulaniyor",
    "kontrol ediyorum",
    "tool sonucu",
    "execution_target",
    "planliyorum",
    "planlıyorum",
)
_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\[^\s,.;!?]+)|(?:/Users/[^\s,.;!?]+)|"
    r"(?:/home/[^\s,.;!?]+)",
    re.IGNORECASE,
)
_FAILED_OUTCOMES = frozenset({"failed", "partial", "unsupported", "cancelled"})
class TTSEvent(StrEnum):
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    NEEDS_CLARIFICATION = "needs_clarification"


def is_technical_status(text: str) -> bool:
    """Return whether a progress/debug line is unsuitable for speech."""
    blob = (text or "").strip().casefold()
    if not blob:
        return True
    if any(marker in blob for marker in _TECHNICAL_STATUS_MARKERS):
        return True
    if any(tool in blob for tool in _TOOL_NAMES):
        return True
    return bool(_PATH_PATTERN.search(text or "") or "{" in blob or "}" in blob)


def sanitize_for_tts(text: str) -> str:
    """Remove paths and internal execution vocabulary from display text."""
    cleaned = _PATH_PATTERN.sub("", (text or "").strip())
    for tool in _TOOL_NAMES:
        cleaned = re.sub(re.escape(tool), "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip(" ,.;")


def synthesize_task_started(user_message: str) -> str | None:
    """Return a generic progress line without interpreting user semantics."""
    if not (user_message or "").strip():
        return None
    return "İşlemi gerçekleştiriyorum."


def synthesize_task_completed(
    user_message: str,
    response_text: str,
    tool_results: list[dict[str, Any]] | None = None,
    *,
    outcome: str | None = None,
) -> str | None:
    """Speak the authoritative V3 response without classifying the request."""
    del user_message, tool_results
    text = sanitize_for_tts(response_text)
    if not text or is_technical_status(text):
        return None

    first = text.split("\n", 1)[0].strip()
    if not first:
        return None
    if (outcome or "").strip().casefold() in _FAILED_OUTCOMES:
        return "İşi tamamlayamadım."
    return first[:160].rstrip()


def should_speak_event(
    event: TTSEvent,
    *,
    elapsed_ms: float = 0.0,
    start_delay_ms: float = 850.0,
) -> bool:
    """Speak delayed start events only for work that is still running."""
    if event != TTSEvent.TASK_STARTED:
        return True
    return elapsed_ms >= start_delay_ms


def is_duplicate_speech(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    return previous.strip().casefold() == current.strip().casefold()
