"""Long-term memory — user-level facts that survive across sessions.

Long-term memory holds preferences, environment quirks, the user's name,
language preference, and similar facts. It is *not* a credential store:
the writer layer refuses secrets by design.

Storage is simple key/value, but every value carries its provenance and
an optional `expires_at` so the runtime can retire stale facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes.memory.security import (
    atomic_write_json,
    is_sensitive_key,
    read_json_file,
    scrub_text,
)

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_secret_key(key: str) -> bool:
    return is_sensitive_key(key)


def _scrub_value(value: str) -> str:
    return scrub_text(value)


@dataclass(frozen=True)
class LongTermFact:
    """A single long-term fact about the user or environment."""

    key: str
    value: str
    provenance: str = ""
    recorded_at: str = field(default_factory=_utc_now_iso)
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "provenance": self.provenance,
            "recorded_at": self.recorded_at,
            "expires_at": self.expires_at,
        }


class LongTermMemory:
    """Append-or-replace long-term memory with optional persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._facts: dict[str, LongTermFact] = {}
        if path is not None and path.exists():
            self._load()

    def remember(
        self,
        key: str,
        value: str,
        *,
        provenance: str = "",
        expires_at: str | None = None,
    ) -> LongTermFact:
        return self.remember_many(
            ((key, value),),
            provenance=provenance,
            expires_at=expires_at,
        )[0]

    def remember_many(
        self,
        entries: tuple[tuple[str, str], ...],
        *,
        provenance: str = "",
        expires_at: str | None = None,
    ) -> tuple[LongTermFact, ...]:
        facts: list[LongTermFact] = []
        for key, value in entries:
            if _is_secret_key(key):
                raise ValueError(
                    f"Refusing to remember {key!r}: long-term memory does not store secrets."
                )
            facts.append(
                LongTermFact(
                    key=key,
                    value=_scrub_value(value),
                    provenance=provenance,
                    expires_at=expires_at,
                )
            )
        previous = dict(self._facts)
        for fact in facts:
            self._facts[fact.key] = fact
        try:
            self._persist()
        except Exception:
            self._facts = previous
            raise
        return tuple(facts)

    def forget(self, key: str) -> bool:
        previous = self._facts.pop(key, None)
        if previous is None:
            return False
        try:
            self._persist()
        except Exception:
            self._facts[key] = previous
            raise
        return True

    def recall(self, key: str) -> LongTermFact | None:
        fact = self._facts.get(key)
        if fact is None:
            return None
        if fact.expires_at and fact.expires_at <= _utc_now_iso():
            return None
        return fact

    def all_facts(self) -> tuple[LongTermFact, ...]:
        return tuple(self._facts[key] for key in sorted(self._facts))

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "facts": {key: fact.to_dict() for key, fact in self._facts.items()},
        }
        atomic_write_json(self._path, payload)

    def _load(self) -> None:
        data = read_json_file(self._path)
        if int(data.get("schema_version", 0)) != 1:
            raise ValueError("Unsupported long-term memory schema version")
        facts = data.get("facts", {})
        loaded: dict[str, LongTermFact] = {}
        for key, entry in facts.items():
            loaded[str(key)] = LongTermFact(
                key=str(key),
                value=str(entry.get("value", "")),
                provenance=str(entry.get("provenance", "")),
                recorded_at=str(entry.get("recorded_at", "") or _utc_now_iso()),
                expires_at=entry.get("expires_at"),
            )
        self._facts = loaded