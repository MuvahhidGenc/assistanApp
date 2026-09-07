"""Evidence — the trail of *how we know what we know*.

Every claim about the world should be backed by evidence. The World
Model stores evidence records alongside the state they justify; the
Execution Log stores the events that produced the evidence; the
reasoning layer can re-examine both.

`EvidenceSource` is a closed taxonomy so the reasoning layer can trust
"this evidence came from a verifier that actually read the file" more
than "this evidence came from a tool that *said* it wrote the file".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class EvidenceSource(StrEnum):
    """How a fact was acquired.

    Listed in roughly descending trust order, though the reasoning layer
    treats them as inputs, not verdicts.
    """

    USER_STATEMENT = "user_statement"
    TOOL_REPORT = "tool_report"
    OBSERVATION = "observation"
    VERIFIER = "verifier"
    RECOVERY = "recovery"
    INFERENCE = "inference"
    IMPORTED = "imported"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_evidence_id() -> str:
    import uuid as _uuid

    return f"ev_{_uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class EvidenceRecord:
    """A single claim about the world, with provenance and a confidence hint."""

    evidence_id: str
    source: EvidenceSource
    capability: str
    claim: str
    data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    recorded_at: str = field(default_factory=_utc_now_iso)
    correlation_id: str = ""

    @staticmethod
    def make(
        *,
        source: EvidenceSource,
        capability: str,
        claim: str,
        data: dict[str, Any] | None = None,
        confidence: float = 1.0,
        correlation_id: str = "",
    ) -> "EvidenceRecord":
        return EvidenceRecord(
            evidence_id=_new_evidence_id(),
            source=source,
            capability=capability,
            claim=claim,
            data=dict(data or {}),
            confidence=float(confidence),
            correlation_id=correlation_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source.value,
            "capability": self.capability,
            "claim": self.claim,
            "data": dict(self.data),
            "confidence": self.confidence,
            "recorded_at": self.recorded_at,
            "correlation_id": self.correlation_id,
        }