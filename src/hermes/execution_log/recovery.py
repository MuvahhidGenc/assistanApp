"""V3 Execution Log ↔ V2 recovery evidence boundary.

This module does **not** re-implement recovery orchestration. V2
`RecoveryEngine` (in `hermes.mission.recovery.engine`) is the live
implementation that decides *whether* and *how* to retry a failed action.
V3 Execution Log records what recovery *attempted* and *produced*, in
structured form.

`RecoveryEvidenceRecorder` is a thin helper that:

  * emits `RECOVERY_STARTED` when a recovery attempt begins,
  * emits `RECOVERY_FINISHED` when it ends,
  * tracks attempts-used and budget-remaining so the runtime can stop
    over-recovering without consulting V2 directly.

The recorder holds no policy. It does not decide which strategy to use,
it does not evaluate risk, and it does not call any tool. The orchestration
remains V2's job until V3.4 (Runtime Integration) replaces it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hermes.execution_log.events import (
    recovery_finished_payload,
    recovery_started_payload,
)
from hermes.execution_log.store import ExecutionLogStore


@dataclass
class RecoveryEvidenceRecorder:
    """Stateless recorder for recovery evidence, scoped to one recovery session."""

    store: ExecutionLogStore
    correlation_id: str
    action_id: str
    budget_total: int
    attempts_used: int = 0
    attempts_by_strategy: dict[str, int] = field(default_factory=dict)

    @property
    def budget_remaining(self) -> int:
        return max(0, self.budget_total - self.attempts_used)

    def started(
        self,
        *,
        strategy_id: str,
        reason: str,
        idempotency_key: str | None = None,
        risk_level: str | None = None,
    ):
        """Emit a `RECOVERY_STARTED` event.

        Does not increment attempts — that happens at `finished` so a
        recovery that aborts before running is not double-counted.
        """
        envelope = recovery_started_payload(
            correlation_id=self.correlation_id,
            action_id=self.action_id,
            strategy_id=strategy_id,
            reason=reason,
            idempotency_key=idempotency_key,
            risk_level=risk_level,
        )
        return self.store.append(envelope)

    def finished(
        self,
        *,
        strategy_id: str,
        result: str,
        user_message: str,
    ):
        """Emit a `RECOVERY_FINISHED` event and update attempt counters.

        `result` must be one of: "recovered" | "failed" | "waiting_for_user"
        | "requires_approval". Anything else raises so the log never carries
        an unknown recovery outcome.
        """
        allowed = {"recovered", "failed", "waiting_for_user", "requires_approval"}
        if result not in allowed:
            raise ValueError(
                f"Unknown recovery result {result!r}; expected one of {sorted(allowed)}"
            )
        self.attempts_used += 1
        self.attempts_by_strategy[strategy_id] = (
            self.attempts_by_strategy.get(strategy_id, 0) + 1
        )
        envelope = recovery_finished_payload(
            correlation_id=self.correlation_id,
            action_id=self.action_id,
            strategy_id=strategy_id,
            result=result,
            user_message=user_message,
            attempts_used=self.attempts_used,
            budget_remaining=self.budget_remaining,
        )
        return self.store.append(envelope)