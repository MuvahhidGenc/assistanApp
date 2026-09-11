"""V3 Execution Log audit boundary.

This module does **not** replace `hermes.security.policy_engine.AuditLogger`.
The V2 security audit records policy/approval decisions — those are
secrets and access-control events that must remain in the security domain.
The V3 Execution Log audit is a *parallel* trail:

  * V2 security audit answers "was this allowed?".
  * V3 Execution Log answers "what actually happened?".

They share a fact when a one is the cause of the other — V3 records the
allow/deny/approval as part of the `ACTION_STARTED` envelope so the trail
is self-contained — but the security audit itself continues to be the
authority on access decisions.

`ExecutionLogAuditor` is a small convenience wrapper that emits one
event per call. It does not decide anything; the caller passes the fact.
"""

from __future__ import annotations

from typing import Any

from hermes.execution_log.events import (
    EventEnvelope,
    action_finished_payload,
    action_started_payload,
    observation_recorded_payload,
)
from hermes.execution_log.store import ExecutionLogStore


class ExecutionLogAuditor:
    """Thin write-side wrapper for the most common event shapes."""

    def __init__(self, store: ExecutionLogStore) -> None:
        self._store = store

    @property
    def store(self) -> ExecutionLogStore:
        return self._store

    # ---- actions ---------------------------------------------------------

    def action_started(
        self,
        *,
        correlation_id: str,
        capability: str,
        tool: str,
        arguments: dict[str, Any],
        execution_target: str,
        action_id: str | None = None,
        risk_level: str | None = None,
        security_decision: str | None = None,
        approval_outcome: str | None = None,
    ) -> EventEnvelope:
        envelope = action_started_payload(
            correlation_id=correlation_id,
            action_id=action_id,
            capability=capability,
            tool=tool,
            arguments=arguments,
            execution_target=execution_target,
            risk_level=risk_level,
            security_decision=security_decision,
            approval_outcome=approval_outcome,
        )
        return self._store.append(envelope)

    def action_finished(
        self,
        *,
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
        envelope = action_finished_payload(
            correlation_id=correlation_id,
            action_id=action_id,
            tool=tool,
            success=success,
            output=output,
            error=error,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        return self._store.append(envelope)

    # ---- observations ----------------------------------------------------

    def observation_recorded(
        self,
        *,
        correlation_id: str,
        action_id: str,
        source: str,
        observation_type: str,
        data: dict[str, Any] | None = None,
    ) -> EventEnvelope:
        envelope = observation_recorded_payload(
            correlation_id=correlation_id,
            action_id=action_id,
            source=source,
            observation_type=observation_type,
            data=data,
        )
        return self._store.append(envelope)