"""Phase 1A — Read-only UI projection layer.
Maps canonical V3 execution-log / orchestrator events to typed UI events.
Does NOT modify backend; uses existing frozen dataclasses / enums.
NOTE: UIEventKind defines presentation-level kinds (TASK_CANCELLED) without
referencing V3AgentPhase.CANCELLED (not canonical in V3AgentPhase).
"""
from __future__ import annotations
from enum import StrEnum
from dataclasses import dataclass
from typing import Any
from hermes.execution_log.events import EventEnvelope, EventKind

class UIEventKind(StrEnum):
    ACTION_STARTED = "action_started"
    ACTION_FINISHED = "action_finished"
    OBSERVATION_RECORDED = "observation_recorded"
    VERIFICATION_RECORDED = "verification_recorded"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_FINISHED = "recovery_finished"
    TASK_CANCELLED = "task_cancelled"  # presentation only; NOT V3AgentPhase.CANCELLED

@dataclass(frozen=True)
class UIEvent:
    event_id: str
    kind: str
    timestamp: str
    correlation_id: str
    task_id: str
    action_id: str | None
    payload: dict[str, Any]

# NOTE (review): canonical EventEnvelope has no session_id; task_id maps to correlation_id.
# Payload is minimal (source_version + kind) to avoid exposing mutable backend objects.
# If full payload projection needed, extend here; do NOT expose EventEnvelope directly.
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
