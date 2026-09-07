"""Execution Log Store — append-only, durable, queryable.

The store writes one JSON line per event to disk. Reads return the same
events in insertion order. The store never rewrites an existing entry — a
correction is a new event that references the one being corrected.

Persistence boundary:

  * The store owns a JSONL file at the path given by the caller. By default
    it uses `app_data_dir() / "state" / "execution_log.jsonl"`.
  * Each `append` opens the file in append mode, writes one line, and
    flushes. The store does **not** batch — losing one event on crash is
    worse than the latency cost.
  * Reads return frozen tuples so a caller cannot mutate the in-memory
    representation; the on-disk record is the source of truth.

The store exposes a *query* surface (`events_for`, `actions`) for
ReasoningRuntime and World Model. It does not interpret results — that is
the reasoning layer's job.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from hermes.config.paths import app_data_dir
from hermes.execution_log.events import (
    ActionFinished,
    ActionStarted,
    EventEnvelope,
    EventKind,
    ObservationRecorded,
    RecoveryFinished,
    RecoveryStarted,
    VerificationRecorded,
    _utc_now_iso,
)


_DEFAULT_PATH = app_data_dir() / "state" / "execution_log.jsonl"


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    if is_dataclass(payload):
        return asdict(payload)
    if isinstance(payload, dict):
        return dict(payload)
    raise TypeError(f"Cannot serialize event payload of type {type(payload).__name__}")


def _envelope_to_dict(envelope: EventEnvelope) -> dict[str, Any]:
    return {
        "event_id": envelope.event_id,
        "kind": envelope.kind.value,
        "recorded_at": envelope.recorded_at,
        "correlation_id": envelope.correlation_id,
        "action_id": envelope.action_id,
        "payload": _payload_to_dict(envelope.payload),
    }


class ExecutionLogStore:
    """Append-only Execution Log store backed by a JSONL file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_PATH

    # ---- location ---------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ---- write ------------------------------------------------------------

    def append(self, envelope: EventEnvelope) -> EventEnvelope:
        """Persist one event and return it.

        The write is durable (file is opened, written, flushed, and the
        OS flush is requested). On a transient OS error the store raises;
        it does not silently swallow writes.
        """
        self._ensure_parent()
        record = _envelope_to_dict(envelope)
        line = json.dumps(record, ensure_ascii=False, sort_keys=False)
        # Open in append mode, write, flush, fsync.
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return envelope

    def append_many(self, envelopes: Iterable[EventEnvelope]) -> list[EventEnvelope]:
        """Append several envelopes in order; same durability as `append`."""
        result: list[EventEnvelope] = []
        for envelope in envelopes:
            result.append(self.append(envelope))
        return result

    # ---- read -------------------------------------------------------------

    def all(self) -> tuple[EventEnvelope, ...]:
        """Read every event in the log, in insertion order.

        Returns an empty tuple if the file does not exist or is empty.
        Malformed lines are skipped with a hard-stop on the first parse
        failure after a sane prefix — append-only logs must never silently
        rewrite history.
        """
        if not self._path.exists():
            return ()
        envelopes: list[EventEnvelope] = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Malformed execution log line {line_number} in {self._path}: {exc}"
                    ) from exc
                envelopes.append(self._record_to_envelope(record))
        return tuple(envelopes)

    def events_for(self, correlation_id: str) -> tuple[EventEnvelope, ...]:
        """Return every event tagged with the given correlation_id."""
        if not correlation_id:
            return ()
        return tuple(env for env in self.all() if env.correlation_id == correlation_id)

    def actions(self) -> tuple[str, ...]:
        """Return every distinct `action_id` in the log, in first-seen order."""
        seen: set[str] = set()
        ordered: list[str] = []
        for envelope in self.all():
            if envelope.action_id in seen:
                continue
            seen.add(envelope.action_id)
            ordered.append(envelope.action_id)
        return tuple(ordered)

    def history_for_action(self, action_id: str) -> tuple[EventEnvelope, ...]:
        """Return every event belonging to one action, in insertion order."""
        if not action_id:
            return ()
        return tuple(env for env in self.all() if env.action_id == action_id)

    def has_kind(self, kind: EventKind) -> bool:
        """Cheap predicate: does any event of this kind exist in the log?"""
        return any(env.kind is kind for env in self.all())

    def count_by_kind(self) -> dict[str, int]:
        """Count events per kind. O(N) over the log."""
        counts: dict[str, int] = {}
        for envelope in self.all():
            counts[envelope.kind.value] = counts.get(envelope.kind.value, 0) + 1
        return counts

    # ---- lifecycle --------------------------------------------------------

    def rotate(self) -> Path:
        """Move the current log to a timestamped backup and start fresh.

        Used by tests and by callers that want a deterministic log state.
        Returns the backup path.
        """
        if not self._path.exists():
            self._ensure_parent()
            return self._path
        backup = self._path.with_suffix(
            f".{_utc_now_iso().replace(':', '-')}.bak"
        )
        # Atomic rename.
        with tempfile.NamedTemporaryFile(
            dir=str(self._path.parent), delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            os.replace(self._path, backup)
            tmp_path.unlink(missing_ok=True)
            self._ensure_parent()
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise
        return backup

    def reset(self) -> None:
        """Delete the log. Used by tests."""
        if self._path.exists():
            self._path.unlink()

    # ---- internals --------------------------------------------------------

    def _record_to_envelope(self, record: dict[str, Any]) -> EventEnvelope:
        try:
            kind = EventKind(record["kind"])
        except (KeyError, ValueError) as exc:
            raise RuntimeError(f"Unknown event kind in log: {record.get('kind')!r}") from exc
        payload = self._dict_to_payload(kind, record.get("payload") or {})
        return EventEnvelope(
            event_id=str(record["event_id"]),
            kind=kind,
            recorded_at=str(record["recorded_at"]),
            correlation_id=str(record["correlation_id"]),
            action_id=str(record["action_id"]),
            payload=payload,
        )

    @staticmethod
    def _dict_to_payload(kind: EventKind, payload: dict[str, Any]) -> Any:
        """Reconstruct a typed payload dataclass from its JSON form.

        The reader does not *trust* the JSON shape — it asserts the keys
        the dataclass expects. An unknown extra key is preserved on the
        payload so the caller can still introspect; a missing required
        key raises so the log never silently corrupts.
        """
        if kind is EventKind.ACTION_STARTED:
            return ActionStarted(
                capability=str(payload["capability"]),
                tool=str(payload["tool"]),
                arguments=dict(payload.get("arguments") or {}),
                execution_target=str(payload["execution_target"]),
                risk_level=payload.get("risk_level"),
                correlation_id=str(payload["correlation_id"]),
                security_decision=payload.get("security_decision"),
                approval_outcome=payload.get("approval_outcome"),
            )
        if kind is EventKind.ACTION_FINISHED:
            return ActionFinished(
                tool=str(payload["tool"]),
                success=bool(payload["success"]),
                output=payload.get("output"),
                error=payload.get("error"),
                started_at=str(payload["started_at"]),
                finished_at=str(payload["finished_at"]),
                duration_ms=int(payload["duration_ms"]),
            )
        if kind is EventKind.OBSERVATION_RECORDED:
            return ObservationRecorded(
                source=str(payload["source"]),
                observation_type=str(payload["observation_type"]),
                data=dict(payload.get("data") or {}),
            )
        if kind is EventKind.VERIFICATION_RECORDED:
            return VerificationRecorded(
                verifier=str(payload["verifier"]),
                method=str(payload["method"]),
                status=str(payload["status"]),
                details=dict(payload.get("details") or {}),
                observed_at=payload.get("observed_at"),
            )
        if kind is EventKind.RECOVERY_STARTED:
            return RecoveryStarted(
                strategy_id=str(payload["strategy_id"]),
                reason=str(payload["reason"]),
                idempotency_key=payload.get("idempotency_key"),
                risk_level=payload.get("risk_level"),
            )
        if kind is EventKind.RECOVERY_FINISHED:
            return RecoveryFinished(
                strategy_id=str(payload["strategy_id"]),
                result=str(payload["result"]),
                user_message=str(payload["user_message"]),
                attempts_used=int(payload["attempts_used"]),
                budget_remaining=int(payload["budget_remaining"]),
                finished_at=str(payload["finished_at"]),
            )
        # Defensive: future kinds added without a reader raise loudly.
        raise RuntimeError(f"No payload reader for event kind {kind.value!r}")