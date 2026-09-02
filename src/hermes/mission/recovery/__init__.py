"""Deterministic mission recovery — ERROR → CLASSIFY → RECOVER → VERIFY."""

from hermes.mission.recovery.classifier import ErrorCategory, classify_error
from hermes.mission.recovery.config import RecoveryConfig
from hermes.mission.recovery.context import (
    FailureKind,
    RecoveryAction,
    RecoveryContext,
    check_idempotency,
    step_is_fatal,
)
from hermes.mission.recovery.engine import RecoveryEngine, RecoveryOutcome
from hermes.mission.recovery.strategies import RecoveryStrategy, create_default_strategies

__all__ = [
    "ErrorCategory",
    "FailureKind",
    "RecoveryAction",
    "RecoveryConfig",
    "RecoveryContext",
    "RecoveryEngine",
    "RecoveryOutcome",
    "RecoveryStrategy",
    "check_idempotency",
    "classify_error",
    "create_default_strategies",
    "step_is_fatal",
]
