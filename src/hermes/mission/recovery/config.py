from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RecoveryConfig:
    """Configurable recovery budget — defaults conservative."""

    max_same_strategy_attempts: int = 2
    max_alternative_strategies: int = 3
    total_recovery_budget: int = 5
    retry_backoff_seconds: float = 0.5

    @classmethod
    def from_dict(cls, data: dict | None) -> RecoveryConfig:
        if not data:
            return cls()
        return cls(
            max_same_strategy_attempts=int(data.get("max_same_strategy_attempts", 2)),
            max_alternative_strategies=int(data.get("max_alternative_strategies", 3)),
            total_recovery_budget=int(data.get("total_recovery_budget", 5)),
            retry_backoff_seconds=float(data.get("retry_backoff_seconds", 0.5)),
        )
