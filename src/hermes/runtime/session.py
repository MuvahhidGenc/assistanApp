"""V3 session persistence — save/load the agent state across restarts.

The V3 runtime owns a single in-process state (the ``WorldModel`` plus
the orchestrator's per-turn bookkeeping). Long-running tasks need to
survive restarts. This module provides a tiny durable store that
snapshots the world model and a per-turn execution summary to disk and
can restore it on the next run.

The store is intentionally minimal: one JSON file per session, no
indexing, no transactions. Production deployments that need stronger
durability can swap the file backend for a database without changing
the public API.
"""

from __future__ import annotations

import uuid
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes.memory.security import atomic_write_json, read_json_file
from hermes.world_model import WorldModel


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_dir() -> Path:
    from hermes.config.paths import client_state_dir

    return client_state_dir() / "v3_sessions"


@dataclass
class SessionState:
    """A persisted snapshot of a V3 session.

    The snapshot is small on purpose: it holds the world model, the
    correlation id of the most recent turn, and a high-level objective.
    Replaying the Execution Log reconstructs the full event history.
    """

    session_id: str
    objective: str
    correlation_id: str = ""
    world_state: dict[str, Any] = field(default_factory=dict)
    task_state: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    references: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed: bool = False
    last_summary: str = ""
    saved_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "objective": self.objective,
            "correlation_id": self.correlation_id,
            "world_state": self.world_state,
            "task_state": self.task_state,
            "evidence": self.evidence,
            "references": self.references,
            "completed": self.completed,
            "last_summary": self.last_summary,
            "saved_at": self.saved_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SessionState":
        return SessionState(
            session_id=str(data.get("session_id", "")),
            objective=str(data.get("objective", "")),
            correlation_id=str(data.get("correlation_id", "")),
            world_state=dict(data.get("world_state") or {}),
            task_state=dict(data.get("task_state") or {}),
            evidence=list(data.get("evidence") or []),
            references=dict(data.get("references") or {}),
            completed=bool(data.get("completed", False)),
            last_summary=str(data.get("last_summary", "")),
            saved_at=str(data.get("saved_at", "") or _utc_now_iso()),
        )


class SessionStore:
    """Append-only JSON-on-disk store for ``SessionState``.

    Files are written atomically (``write to .tmp + os.replace``) so a
    crashed process never leaves a half-written snapshot on disk.
    """

    SCHEMA_VERSION = 1

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or _default_dir()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, session_id: str) -> Path:
        normalized = str(session_id).strip()
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", normalized) is None:
            raise ValueError("Invalid session id")
        return self._dir / f"{normalized}.json"

    def save(self, state: SessionState) -> Path:
        path = self._path_for(state.session_id)
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "state": state.to_dict(),
        }
        atomic_write_json(path, payload)
        return path

    def load(self, session_id: str) -> SessionState | None:
        path = self._path_for(session_id)
        if not path.exists():
            return None
        data = read_json_file(path)
        if int(data.get("schema_version", 0)) != self.SCHEMA_VERSION:
            return None
        return SessionState.from_dict(data.get("state") or {})

    def list_sessions(self) -> list[str]:
        return sorted(p.stem for p in self._dir.glob("*.json") if p.is_file())

    def delete(self, session_id: str) -> bool:
        path = self._path_for(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def save_active_session_id(self, session_id: str) -> None:
        self._path_for(session_id)  # validates without requiring a snapshot
        atomic_write_json(
            self._dir.parent / "active_session.json",
            {"schema_version": 1, "session_id": session_id},
        )

    def load_active_session_id(self) -> str | None:
        path = self._dir.parent / "active_session.json"
        if not path.exists():
            return None
        payload = read_json_file(path)
        if int(payload.get("schema_version", 0)) != 1:
            return None
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return None
        self._path_for(session_id)
        return session_id


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"


def snapshot_from_world_model(
    world_model: WorldModel,
    *,
    session_id: str,
    objective: str,
    correlation_id: str = "",
    last_summary: str = "",
    completed: bool = False,
) -> SessionState:
    """Build a ``SessionState`` from a live ``WorldModel``."""
    snap = world_model.snapshot()
    return SessionState(
        session_id=session_id,
        objective=objective,
        correlation_id=correlation_id,
        world_state=snap.get("environment", {}),
        task_state=snap.get("task", {}),
        evidence=snap.get("evidence", []),
        references=snap.get("references", {}),
        last_summary=last_summary,
        completed=completed,
    )


def apply_to_world_model(state: SessionState, world_model: WorldModel) -> None:
    """Validate a complete snapshot, then atomically replace in-memory state."""
    from hermes.world_model.evidence import EvidenceRecord, EvidenceSource
    from hermes.world_model.reference import ReferenceBinding, ReferenceKind

    requirements = tuple(
        _dict_to_requirement(req) for req in state.task_state.get("requirements", [])
    )
    evidence_records: list[EvidenceRecord] = []
    for record in state.evidence:
        evidence_id = str(record.get("evidence_id", "")).strip()
        if not evidence_id:
            raise ValueError("Persisted evidence requires evidence_id")
        evidence_records.append(
            EvidenceRecord(
                evidence_id=evidence_id,
                source=EvidenceSource(str(record.get("source", "imported"))),
                capability=str(record.get("capability", "")),
                claim=str(record.get("claim", "")),
                data=dict(record.get("data") or {}),
                confidence=float(record.get("confidence", 1.0)),
                recorded_at=str(record.get("recorded_at", "") or _utc_now_iso()),
                correlation_id=str(record.get("correlation_id", "")),
            )
        )
    references: list[tuple[str, ReferenceBinding]] = []
    for key, ref in state.references.items():
        binding_id = str(ref.get("binding_id", "")).strip()
        if not binding_id:
            raise ValueError("Persisted reference requires binding_id")
        references.append(
            (
                key,
                ReferenceBinding(
                    binding_id=binding_id,
                    key=key,
                    kind=ReferenceKind(str(ref.get("kind", "entity"))),
                    value=str(ref.get("value", "")),
                    provenance=str(ref.get("provenance", "")),
                    as_of=str(ref.get("as_of", "") or _utc_now_iso()),
                    expires_at=ref.get("expires_at"),
                    extra=dict(ref.get("extra") or {}),
                ),
            )
        )

    world_model.reset()
    world_model.set_objective(
        state.objective or state.task_state.get("objective", ""),
        requirements=requirements,
        uncertainty=tuple(state.task_state.get("uncertainty") or ()),
        relevant_context=dict(state.task_state.get("relevant_context") or {}),
        status=str(state.task_state.get("status", "open")),
    )
    environment = dict(state.world_state)
    extra = dict(environment.pop("extra", {}) or {})
    environment.pop("as_of", None)
    world_model.update_environment(**environment)
    if extra:
        world_model.update_environment(**extra)
    for evidence in evidence_records:
        world_model.record_evidence(evidence)
    for key, reference in references:
        world_model.bind_reference(key, reference)


def _dict_to_requirement(data: dict[str, Any]) -> Any:
    from hermes.world_model.state import TaskRequirement

    return TaskRequirement(
        capability=str(data.get("capability", "")),
        description=str(data.get("description", "")),
        satisfied=bool(data.get("satisfied", False)),
        evidence=tuple(data.get("evidence") or ()),
    )


__all__ = [
    "SessionState",
    "SessionStore",
    "new_session_id",
    "snapshot_from_world_model",
    "apply_to_world_model",
]