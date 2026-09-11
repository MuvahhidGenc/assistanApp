"""Per-command latency / observability trace.

One TurnTrace owns a user command from capture through TTS. Stages are
optional; missing stages stay zero. Never stores API keys or raw audio.
"""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class TurnTrace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.monotonic)
    path: str = ""  # fast_local | intent | mission | legacy | conversation
    stages_ms: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(
        default_factory=lambda: {
            "llm_calls": 0,
            "network_calls": 0,
            "tool_calls": 0,
        }
    )
    model: str = ""
    notes: list[str] = field(default_factory=list)
    _open: dict[str, float] = field(default_factory=dict, repr=False)

    def mark(self, stage: str) -> None:
        """Start timing a stage (idempotent restart)."""
        self._open[stage] = time.monotonic()

    def end(self, stage: str) -> float:
        started = self._open.pop(stage, None)
        if started is None:
            return 0.0
        elapsed = (time.monotonic() - started) * 1000.0
        self.stages_ms[stage] = round(self.stages_ms.get(stage, 0.0) + elapsed, 1)
        return elapsed

    def note(self, text: str) -> None:
        if text:
            self.notes.append(text[:160])

    def incr(self, name: str, amount: int = 1) -> None:
        self.counters[name] = int(self.counters.get(name, 0)) + amount

    @property
    def total_ms(self) -> float:
        return round((time.monotonic() - self.started_at) * 1000.0, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "path": self.path,
            "total_ms": self.total_ms,
            "stages_ms": dict(self.stages_ms),
            "counters": dict(self.counters),
            "model": self.model,
            "notes": list(self.notes[:12]),
        }


_current: ContextVar[TurnTrace | None] = ContextVar("hermes_turn_trace", default=None)


def current_trace() -> TurnTrace | None:
    return _current.get()


def start_turn_trace(*, path: str = "") -> tuple[TurnTrace, Token]:
    trace = TurnTrace(path=path)
    token = _current.set(trace)
    return trace, token


def reset_turn_trace(token: Token) -> None:
    _current.reset(token)


@contextmanager
def turn_stage(stage: str) -> Iterator[None]:
    trace = current_trace()
    if trace is None:
        yield
        return
    trace.mark(stage)
    try:
        yield
    finally:
        trace.end(stage)
