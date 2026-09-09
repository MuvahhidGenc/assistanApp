"""Phase 1A — Read-only UI projection layer.

Maps canonical V3 execution-log / orchestrator events to typed UI events.
Does NOT modify backend; uses existing frozen dataclasses / enums.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from hermes.execution_log.events import EventEnvelope, EventKind

@dataclass(frozen=True)
class UIEvent:
    event_id: str
    kind: str
    timestamp: str
    correlation_id: str
    task_id: str
    action_id: str | None
    payload: dict[str, Any]

def project(envelope: EventEnvelope) -> UIEvent:
    return UIEvent(
        event_id=envelope.event_id,
        kind=envelope.kind.value,
        timestamp=envelope.recorded_at,
        correlation_id=envelope.correlation_id,
        task_id=envelope.correlation_id,
        action_id=envelope.action_id,
        payload={"source_version":"v3","kind":envelope.kind.value},
    )
