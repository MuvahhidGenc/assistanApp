"""Production facade joining the three independent V3 memory stores."""

from __future__ import annotations

import re
from pathlib import Path

from hermes.memory.episodic import EpisodicMemory
from hermes.memory.long_term import LongTermMemory
from hermes.memory.session import SessionMemory, SessionTurn


_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class RuntimeMemory:
    """Own persistent memory paths without conflating them with world state."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.episodic = EpisodicMemory(self.directory / "episodic.json")
        self.long_term = LongTermMemory(self.directory / "long_term.json")
        self._session_id = ""
        self._session: SessionMemory | None = None

    @property
    def active_session_id(self) -> str:
        return self._session_id

    @property
    def session(self) -> SessionMemory | None:
        return self._session

    def activate_session(self, session_id: str) -> SessionMemory:
        normalized = str(session_id).strip()
        if not _SAFE_SESSION_ID.fullmatch(normalized):
            raise ValueError("Invalid memory session id")
        if self._session is not None and self._session_id == normalized:
            return self._session
        self._session_id = normalized
        self._session = SessionMemory(
            path=self.directory / "sessions" / f"{normalized}.json"
        )
        return self._session

    def session_turns(self, count: int = 12) -> tuple[SessionTurn, ...]:
        if self._session is None:
            return ()
        return self._session.last(count)

    def record_exchange(self, user_message: str, assistant_message: str) -> None:
        if self._session is None:
            raise RuntimeError("Memory session is not active")
        self._session.record_many(
            (
                ("user", user_message),
                ("assistant", assistant_message),
            )
        )

    def record_episode(
        self,
        *,
        summary: str,
        capabilities: tuple[str, ...],
    ) -> None:
        if not capabilities:
            return
        self.episodic.record(
            summary=summary,
            outcome="completed",
            capabilities=capabilities,
        )


__all__ = ["RuntimeMemory"]
