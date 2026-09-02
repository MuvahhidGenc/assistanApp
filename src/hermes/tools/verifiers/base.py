from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"
    NOT_REQUIRED = "not_required"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Observation:
    """Separate from raw tool output — post-execution state snapshot."""

    source: str
    data: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=_utc_now)


@dataclass
class VerificationResult:
    status: VerificationStatus
    method: str
    details: dict[str, Any] = field(default_factory=dict)
    observation: Observation | None = None
    verified_at: str = field(default_factory=_utc_now)

    @property
    def verified(self) -> bool:
        return self.status == VerificationStatus.VERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "method": self.method,
            "details": self.details,
            "observation": (
                {
                    "source": self.observation.source,
                    "data": self.observation.data,
                    "observed_at": self.observation.observed_at,
                }
                if self.observation
                else None
            ),
            "verified_at": self.verified_at,
            "verified": self.verified,
        }


class BaseVerifier(ABC):
    tool_names: tuple[str, ...] = ()
    default_timeout: float = 10.0

    @abstractmethod
    async def verify(self, ctx: Any) -> VerificationResult:
        ...
