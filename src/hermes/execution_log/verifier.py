"""V3 Execution Log ↔ V2 verifier boundary.

This module does **not** re-implement verification. V2 verifier
implementations (in `hermes.tools.verifiers.*`) are the source of truth
for how a tool's effect is confirmed. V3 Execution Log records those
results in a structured form and binds them to the action that triggered
them.

`record_verification` takes a V2 `VerificationResult` and an action context
and emits the appropriate `VERIFICATION_RECORDED` event into the store.
It does not call the verifier — the caller does that, because V2 verifiers
are async and depend on the live tool registry.

This is the **only** place where V2 verifier shapes are translated into V3
event shapes. Future Verifier implementations may emit events directly via
the store; this adapter remains the bridge for the existing fleet.
"""

from __future__ import annotations

from typing import Any

from hermes.execution_log.events import verification_recorded_payload
from hermes.execution_log.store import ExecutionLogStore
from hermes.tools.verifiers.base import (
    Observation,
    VerificationResult,
    VerificationStatus,
)


# Mapping from V2 VerificationStatus (StrEnum) to V3 status string. Kept
# as a function so callers cannot mutate it and so unknown statuses raise.
def _status_value(status: VerificationStatus | str) -> str:
    if isinstance(status, VerificationStatus):
        return status.value
    text = str(status).strip().lower()
    if text in {member.value for member in VerificationStatus}:
        return text
    raise ValueError(f"Unknown VerificationStatus: {status!r}")


def _observation_data(observation: Observation | None) -> dict[str, Any]:
    if observation is None:
        return {}
    # Observation is a frozen dataclass — convert via attribute access so we
    # do not depend on whether dataclasses.asdict is imported everywhere.
    return {
        "source": observation.source,
        "data": dict(observation.data),
        "observed_at": observation.observed_at,
    }


def record_verification(
    store: ExecutionLogStore,
    *,
    correlation_id: str,
    action_id: str,
    verifier_name: str,
    result: VerificationResult,
) -> Any:
    """Translate a V2 `VerificationResult` into a V3 event and append it.

    Returns the appended envelope so the caller can correlate it. Raises
    `ValueError` if the result carries an unknown status — the log must
    never silently coerce a fact.
    """
    status = _status_value(result.status)
    envelope = verification_recorded_payload(
        correlation_id=correlation_id,
        action_id=action_id,
        verifier=verifier_name,
        method=result.method,
        status=status,
        details=dict(result.details or {}),
        observed_at=result.verified_at,
    )
    return store.append(envelope)


def extract_observation_data(result: VerificationResult) -> dict[str, Any]:
    """Helper for callers that need the Observation sub-record separately.

    Returns an empty dict when the verifier produced no observation.
    """
    return _observation_data(result.observation)