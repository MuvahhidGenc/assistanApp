"""Correlation IDs for end-to-end Client TTS pipeline tracing."""
from __future__ import annotations

import contextvars
from typing import Any

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tts_correlation_id", default=None
)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str) -> contextvars.Token:
    return _correlation_id.set(value)


def reset_correlation_id(token: contextvars.Token) -> None:
    _correlation_id.reset(token)


def trace_fields(**extra: Any) -> dict[str, Any]:
    """Merge correlation_id into structured log fields when set."""
    fields = dict(extra)
    cid = get_correlation_id()
    if cid:
        fields.setdefault("correlation_id", cid)
        fields.setdefault("request_id", cid)
    return fields
