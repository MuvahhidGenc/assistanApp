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

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SESSION_FORBIDDEN_PATTERNS = (
    re.compile(r"password\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"secret\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"token\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"xox[abp]-[A-Za-z0-9-]{16,}"),
)


def _scrub_session_text(text: str) -> str:
    """Redact obvious secret-shaped values from a session turn.

    Sessions can hold arbitrary user text. When the user pastes an
    API key or password, the runtime must not let it sit in plain text
    on disk. The scrubber replaces the secret-looking substring with
    ``[REDACTED]`` so the user can still see the shape of what was
    pasted, but the secret bytes are gone.
    """
    if not text:
        return text
    for pattern in _SESSION_FORBIDDEN_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


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
        from uuid import uuid4

        turn = SessionTurn(
            role=role,
            content=_scrub_session_text(content),
            turn_id=f"t_{uuid4().hex[:12]}",
        )
        self._turns.append(turn)
        if len(self._turns) > self._max_turns:
            # Drop oldest turns to keep memory bounded.
            self._turns = self._turns[-self._max_turns :]
        self._persist()
        return turn

    # ---- read ----------------------------------------------------------

    @property
    def turns(self) -> tuple[SessionTurn, ...]:
        return tuple(self._turns)

    def last(self, count: int = 1) -> tuple[SessionTurn, ...]:
        return tuple(self._turns[-count:])

    # ---- lifecycle -----------------------------------------------------

    def clear(self) -> None:
        self._turns = []
        self._persist()

    # ---- internals -----------------------------------------------------

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "turns": [turn.to_dict() for turn in self._turns],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
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