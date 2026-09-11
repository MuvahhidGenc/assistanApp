"""Execution Log event types.

The taxonomy is small and closed. Each kind has a single purpose:

  * ACTION_STARTED     — a capability/action was invoked.
  * ACTION_FINISHED    — the tool reported its execution result.
  * OBSERVATION_RECORDED — read-only evidence captured for an action.
  * VERIFICATION_RECORDED — independent confirmation (or refusal of it)
                            of the action's effect.
  * RECOVERY_STARTED   — a recovery attempt was initiated for a failed action.
  * RECOVERY_FINISHED  — the recovery attempt ended, with or without success.

The split is deliberate. Each event records one fact and exactly one fact.
A reader can reconstruct the full trajectory by replaying events in order;
it cannot infer a fact that is not present in the log.

`Action Success != Observation Success != Verification Success != Goal
Success` is enforced by *type*, not by convention: an `ActionEvent` carries
only the tool's own report; an `ObservationEvent` carries only the observed
state; a `VerificationEvent` carries only the verifier's judgement. Goal
completion is not an event here — ReasoningRuntime makes that decision
using the recorded facts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class EventKind(StrEnum):
    """Closed taxonomy of Execution Log events."""

    ACTION_STARTED = "action_started"
    ACTION_FINISHED = "action_finished"
    OBSERVATION_RECORDED = "observation_recorded"
    VERIFICATION_RECORDED = "verification_recorded"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_FINISHED = "recovery_finished"


# ---------------------------------------------------------------------------
# Action events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionStarted:
    """The intent and context for an action that is about to be executed."""

    capability: str
    tool: str
    arguments: dict[str, Any]
    execution_target: str          # "client" | "server"
    risk_level: str | None         # RiskLevel value, optional
    correlation_id: str            # links related events for one user intent
    security_decision: str | None  # "allow" | "require_approval" | "deny" | None
    approval_outcome: str | None   # "approved" | "rejected" | "bulk" | None


@dataclass(frozen=True)
class ActionFinished:
    """The tool's own report on what happened.

    `success` is whatever the tool returned — it is the *action* result, not
    an independent confirmation.
    """

    tool: str
    success: bool
    output: Any
    error: str | None
    started_at: str
    finished_at: str
    duration_ms: int


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationRecorded:
    """Read-only evidence captured for an action (or independently)."""

    source: str                    # e.g. "filesystem.list", "screen.read", "process.list"
    observation_type: str          # "structured" | "raw" | "snapshot" | "diff"
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationRecorded:
    """Independent confirmation (or refusal of it) of an action's effect.

    `status` is one of: "verified" | "failed" | "unknown" | "not_required".
    `verifier` is the verifier class name; `method` is the strategy used
    (e.g. "filesystem.exists", "screen.text_present").

    Verification is its own event. It is not the action's report and it is
    not a goal judgement — ReasoningRuntime combines them.
    """

    verifier: str                  # verifier class name
    method: str                    # verification method
    status: str                    # verified | failed | unknown | not_required
    details: dict[str, Any] = field(default_factory=dict)
    observed_at: str | None = None


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryStarted:
    """A recovery attempt was initiated for a failed action."""

    strategy_id: str               # e.g. "retry", "fallback_tool", "alt_args"
    reason: str                    # what triggered the recovery
    idempotency_key: str | None    # replay-safety token, if any
    risk_level: str | None         # escalation risk for the recovery action


@dataclass(frozen=True)
class RecoveryFinished:
    """The recovery attempt ended, with or without success."""

    strategy_id: str
    result: str                    # "recovered" | "failed" | "waiting_for_user" | "requires_approval"
    user_message: str              # what the recovery surfaced to the user
    attempts_used: int             # how many strategy attempts so far
    budget_remaining: int          # budget left in the recovery session
    finished_at: str


# ---------------------------------------------------------------------------
# Envelope and union type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventEnvelope:
    """A single Execution Log entry.

    Every event in the log is wrapped in an envelope so the writer does not
    have to repeat timestamp/id/correlation boilerplate.
    """

    event_id: str
    kind: EventKind
    recorded_at: str               # ISO-8601 UTC timestamp
    correlation_id: str            # links events for the same user intent
    action_id: str                 # the *logical* action this event belongs to
    payload: Any                   # one of the typed payloads above


def action_started_payload(
    correlation_id: str,
    action_id: str | None,
    capability: str,
    tool: str,
    arguments: dict[str, Any],
    execution_target: str,
    risk_level: str | None = None,
    security_decision: str | None = None,
    approval_outcome: str | None = None,
) -> EventEnvelope:
    """Build a typed envelope for an `ACTION_STARTED` event."""
    return EventEnvelope(
        event_id=_new_id("evt"),
        kind=EventKind.ACTION_STARTED,
        recorded_at=_utc_now_iso(),
        correlation_id=correlation_id,
        action_id=action_id or _new_id("act"),
        payload=ActionStarted(
            capability=capability,
            tool=tool,
            arguments=dict(arguments),
            execution_target=execution_target,
            risk_level=risk_level,
            correlation_id=correlation_id,
            security_decision=security_decision,
            approval_outcome=approval_outcome,
        ),
    )


def action_finished_payload(
    correlation_id: str,
    action_id: str,
    tool: str,
    success: bool,
    output: Any,
    error: str | None,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> EventEnvelope:
    """Build a typed envelope for an `ACTION_FINISHED` event."""
    return EventEnvelope(
        event_id=_new_id("evt"),
        kind=EventKind.ACTION_FINISHED,
        recorded_at=_utc_now_iso(),
        correlation_id=correlation_id,
        action_id=action_id,
        payload=ActionFinished(
            tool=tool,
            success=bool(success),
            output=output,
            error=error,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int(duration_ms),
        ),
    )


def observation_recorded_payload(
    correlation_id: str,
    action_id: str,
    source: str,
    observation_type: str,
    data: dict[str, Any] | None = None,
) -> EventEnvelope:
    """Build a typed envelope for an `OBSERVATION_RECORDED` event."""
    return EventEnvelope(
        event_id=_new_id("evt"),
        kind=EventKind.OBSERVATION_RECORDED,
        recorded_at=_utc_now_iso(),
        correlation_id=correlation_id,
        action_id=action_id,
        payload=ObservationRecorded(
            source=source,
            observation_type=observation_type,
            data=dict(data or {}),
        ),
    )


def verification_recorded_payload(
    correlation_id: str,
    action_id: str,
    verifier: str,
    method: str,
    status: str,
    details: dict[str, Any] | None = None,
    observed_at: str | None = None,
) -> EventEnvelope:
    """Build a typed envelope for a `VERIFICATION_RECORDED` event."""
    return EventEnvelope(
        event_id=_new_id("evt"),
        kind=EventKind.VERIFICATION_RECORDED,
        recorded_at=_utc_now_iso(),
        correlation_id=correlation_id,
        action_id=action_id,
        payload=VerificationRecorded(
            verifier=verifier,
            method=method,
            status=status,
            details=dict(details or {}),
            observed_at=observed_at or _utc_now_iso(),
        ),
    )


def recovery_started_payload(
    correlation_id: str,
    action_id: str,
    strategy_id: str,
    reason: str,
    idempotency_key: str | None = None,
    risk_level: str | None = None,
) -> EventEnvelope:
    """Build a typed envelope for a `RECOVERY_STARTED` event."""
    return EventEnvelope(
        event_id=_new_id("evt"),
        kind=EventKind.RECOVERY_STARTED,
        recorded_at=_utc_now_iso(),
        correlation_id=correlation_id,
        action_id=action_id,
        payload=RecoveryStarted(
            strategy_id=strategy_id,
            reason=reason,
            idempotency_key=idempotency_key,
            risk_level=risk_level,
        ),
    )


def recovery_finished_payload(
    correlation_id: str,
    action_id: str,
    strategy_id: str,
    result: str,
    user_message: str,
    attempts_used: int,
    budget_remaining: int,
    finished_at: str | None = None,
) -> EventEnvelope:
    """Build a typed envelope for a `RECOVERY_FINISHED` event."""
    return EventEnvelope(
        event_id=_new_id("evt"),
        kind=EventKind.RECOVERY_FINISHED,
        recorded_at=_utc_now_iso(),
        correlation_id=correlation_id,
        action_id=action_id,
        payload=RecoveryFinished(
            strategy_id=strategy_id,
            result=result,
            user_message=user_message,
            attempts_used=int(attempts_used),
            budget_remaining=int(budget_remaining),
            finished_at=finished_at or _utc_now_iso(),
        ),
    )


# Backwards-compatible alias used in the public API docstring.
ActionEvent = ActionStarted
ObservationEvent = ObservationRecorded
VerificationEvent = VerificationRecorded
RecoveryEvent = RecoveryStarted  # type alias kept for clarity in docstring