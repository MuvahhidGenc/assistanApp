"""Session memory — the current conversation's turns.

Session memory holds the raw user/assistant turn pairs for the active
session. The runtime reads it to give the LLM conversational context;
nothing in here is allowed to influence a *decision* without going
through the World Model first.

Session memory persists to disk by default so the UI's chat history is
preserved across restarts. It deliberately stores plain text only —
no tool output blobs, no credentials, no filesystem paths that may
be sensitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes.memory.security import atomic_write_json, read_json_file, scrub_text


def _scrub_session_text(text: str) -> str:
    """Compatibility export for the shared memory security boundary."""
    return scrub_text(text)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionTurn:
    """One turn in the session: who spoke and what was said."""

    role: str                # "user" | "assistant" | "system"
    content: str
    turn_id: str
    recorded_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role,
            "content": self.content,
            "turn_id": self.turn_id,
            "recorded_at": self.recorded_at,
        }


class SessionMemory:
    """Append-only session memory with optional persistence."""

    def __init__(self, path: Path | None = None, max_turns: int = 200) -> None:
        self._path = path
        self._turns: list[SessionTurn] = []
        self._max_turns = max_turns
        if path is not None and path.exists():
            self._load()

    # ---- write ---------------------------------------------------------

    def record(self, role: str, content: str) -> SessionTurn:
        return self.record_many(((role, content),))[0]

    def record_many(
        self,
        entries: tuple[tuple[str, str], ...],
    ) -> tuple[SessionTurn, ...]:
        from uuid import uuid4

        turns = tuple(
            SessionTurn(
                role=role,
                content=_scrub_session_text(content),
                turn_id=f"t_{uuid4().hex[:12]}",
            )
            for role, content in entries
        )
        previous = list(self._turns)
        self._turns.extend(turns)
        if len(self._turns) > self._max_turns:
            self._turns = self._turns[-self._max_turns :]
        try:
            self._persist()
        except Exception:
            self._turns = previous
            raise
        return turns

    # ---- read ----------------------------------------------------------

    @property
    def turns(self) -> tuple[SessionTurn, ...]:
        return tuple(self._turns)

    def last(self, count: int = 1) -> tuple[SessionTurn, ...]:
        return tuple(self._turns[-count:])

    # ---- lifecycle -----------------------------------------------------

    def clear(self) -> None:
        previous = self._turns
        self._turns = []
        try:
            self._persist()
        except Exception:
            self._turns = previous
            raise

    # ---- internals -----------------------------------------------------

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "turns": [turn.to_dict() for turn in self._turns],
        }
        atomic_write_json(self._path, payload)

    def _load(self) -> None:
        data = read_json_file(self._path)
        if int(data.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported session memory schema version")
        turns: list[SessionTurn] = []
        for entry in data.get("turns", []):
            turns.append(
                SessionTurn(
                    role=str(entry.get("role", "")),
                    content=str(entry.get("content", "")),
                    turn_id=str(entry.get("turn_id", "")),
                    recorded_at=str(entry.get("recorded_at", "") or _utc_now_iso()),
                )
            )
        self._turns = turns

    def to_dict(self) -> dict[str, Any]:
        return {"turns": [turn.to_dict() for turn in self._turns]}