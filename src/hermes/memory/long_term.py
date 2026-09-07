"""Long-term memory — user-level facts that survive across sessions.

Long-term memory holds preferences, environment quirks, the user's name,
language preference, and similar facts. It is *not* a credential store:
the writer layer refuses secrets by design.

Storage is simple key/value, but every value carries its provenance and
an optional `expires_at` so the runtime can retire stale facts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_FORBIDDEN_KEYS = (
    "password",
    "api_key",
    "apikey",
    "secret",
    "token",
    "private_key",
    "ssh_key",
)


_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),         # OpenAI-style keys
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),        # GitHub PAT
    re.compile(r"xox[abp]-[A-Za-z0-9-]{16,}"),  # Slack tokens
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_secret_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(forbidden in lowered for forbidden in _FORBIDDEN_KEYS)


def _scrub_value(value: str) -> str:
    scrubbed = value
    for pattern in _FORBIDDEN_VALUE_PATTERNS:
        scrubbed = pattern.sub("[REDACTED]", scrubbed)
    return scrubbed


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
        if _is_secret_key(key):
            raise ValueError(
                f"Refusing to remember {key!r}: long-term memory does not store secrets."
            )
        scrubbed = _scrub_value(value)
        fact = LongTermFact(
            key=key,
            value=scrubbed,
            provenance=provenance,
            expires_at=expires_at,
        )
        self._facts[key] = fact
        self._persist()
        return fact

    def forget(self, key: str) -> bool:
        existed = self._facts.pop(key, None) is not None
        if existed:
            self._persist()
        return existed

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
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
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