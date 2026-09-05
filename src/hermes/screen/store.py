"""Process-local last ScreenState.

Tools do not receive conversational context; the observe path writes here so
resolve can read the latest snapshot without a second OCR. Mission
working_context and ConversationalContext also persist a copy.
"""
from __future__ import annotations

from hermes.screen.models import ScreenState

_LAST: ScreenState | None = None


def remember_screen_state(state: ScreenState | None) -> None:
    global _LAST
    _LAST = state


def last_screen_state() -> ScreenState | None:
    return _LAST


def clear_screen_state() -> None:
    global _LAST
    _LAST = None
