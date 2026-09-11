"""V3 Execution Log — the canonical record of what the system actually did.

The Execution Log is the single source of truth for execution reality.
It records what was attempted, what was observed, what was verified, and
what recovery was attempted. It does **not**:

  * interpret the user's message,
  * decide which capability to invoke,
  * produce a workflow,
  * call any LLM,
  * bypass security/policy/approval.

The log is append-only: existing records are never silently rewritten. If
a new fact is needed, a new event is appended that points to the previous
event. Corrections are themselves events.

Boundary with V2:

  * V2 `ToolExecutor` writes audit events via `security.audit.AuditLogger`
    and tracks verification through `tools.verifiers.*`. V3 Execution Log
    is a parallel record of the same facts in a structured, queryable form
    suitable for ReasoningRuntime and World Model.
  * V2 `MissionStore` persists mission state; the Execution Log persists
    events about actions, observations, verifications, and recoveries.
    They are separate stores with separate purposes.
  * V2 `MissionAuditor` emits structlog events; the Execution Log mirrors
    the same event taxonomy in a queryable form.
"""

from hermes.execution_log.audit import ExecutionLogAuditor
from hermes.execution_log.events import (
    ActionEvent,
    EventEnvelope,
    EventKind,
    ObservationEvent,
    RecoveryEvent,
    VerificationEvent,
    action_finished_payload,
    action_started_payload,
    observation_recorded_payload,
    recovery_finished_payload,
    recovery_started_payload,
    verification_recorded_payload,
)
from hermes.execution_log.recovery import RecoveryEvidenceRecorder
from hermes.execution_log.store import ExecutionLogStore
from hermes.execution_log.verifier import extract_observation_data, record_verification

__all__ = [
    "ActionEvent",
    "EventEnvelope",
    "EventKind",
    "ExecutionLogAuditor",
    "ExecutionLogStore",
    "ObservationEvent",
    "RecoveryEvent",
    "RecoveryEvidenceRecorder",
    "VerificationEvent",
    "action_finished_payload",
    "action_started_payload",
    "extract_observation_data",
    "observation_recorded_payload",
    "record_verification",
    "recovery_finished_payload",
    "recovery_started_payload",
    "verification_recorded_payload",
]