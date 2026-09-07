"""Episodic memory — past tasks and their outcomes.

Episodic memory holds complete events ("the user asked us to install X on
2026-08-12 and the install completed successfully after 3 retries"). It
is the runtime's recall for "have we done this before?".

Records are *summaries* — the runtime does not store raw tool output, only
the structured outcome. Credentials, secrets, and arbitrary user text
are forbidden at the writer level.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_FORBIDDEN_PATTERNS = (
    re.compile(r"password\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"secret\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"token\s*[:=][^\s,;]+", re.IGNORECASE),
    re.compile(r"\bBEGIN [A-Z ]*PRIVATE KEY\b"),
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrub_secrets(text: str) -> str:
    """Replace anything that looks like a credential with a redacted marker.

    Episodic memory must never carry secrets. The scrubber is deliberately
    conservative: if it *might* be a credential, redact it.
    """
    scrubbed = text
    for pattern in _FORBIDDEN_PATTERNS:
        scrubbed = pattern.sub("[REDACTED]", scrubbed)
    return scrubbed


@dataclass(frozen=True)
class EpisodicRecord:
    """One past task summary."""

    episode_id: str
    summary: str
    outcome: str               # "completed" | "failed" | "abandoned" | "partial"
    capabilities: tuple[str, ...]
    recorded_at: str = field(default_factory=_utc_now_iso)
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "summary": self.summary,
            "outcome": self.outcome,
            "capabilities": list(self.capabilities),
            "recorded_at": self.recorded_at,
            "tags": list(self.tags),
        }


class EpisodicMemory:
    """Append-only episodic memory with optional disk persistence."""

    def __init__(self, path: Path | None = None, max_records: int = 200) -> None:
        self._path = path
        self._records: list[EpisodicRecord] = []
        self._max_records = max_records
        if path is not None and path.exists():
            self._load()

    def record(
        self,
        *,
        summary: str,
        outcome: str,
        capabilities: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
    ) -> EpisodicRecord:
        from uuid import uuid4

        scrubbed = _scrub_secrets(summary)
        record = EpisodicRecord(
            episode_id=f"ep_{uuid4().hex[:12]}",
            summary=scrubbed,
            outcome=outcome,
            capabilities=capabilities,
            tags=tags,
        )
        self._records.append(record)
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records :]
        self._persist()
        return record

    @property
    def records(self) -> tuple[EpisodicRecord, ...]:
        return tuple(self._records)

    def query(self, *, outcome: str | None = None, capability: str | None = None) -> tuple[EpisodicRecord, ...]:
        def matches(record: EpisodicRecord) -> bool:
            if outcome and record.outcome != outcome:
                return False
            if capability and capability not in record.capabilities:
                return False
            return True

        return tuple(record for record in self._records if matches(record))

    def clear(self) -> None:
        self._records = []
        self._persist()

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "records": [record.to_dict() for record in self._records],
        }
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        records: list[EpisodicRecord] = []
        for entry in data.get("records", []):
            records.append(
                EpisodicRecord(
                    episode_id=str(entry.get("episode_id", "")),
                    summary=str(entry.get("summary", "")),
                    outcome=str(entry.get("outcome", "")),
                    capabilities=tuple(entry.get("capabilities") or ()),
                    recorded_at=str(entry.get("recorded_at", "") or _utc_now_iso()),
                    tags=tuple(entry.get("tags") or ()),
                )
            )
        self._records = records