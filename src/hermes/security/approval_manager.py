from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Awaitable, Callable

from hermes.server.models import (
    ApprovalDecision,
    ApprovalRequest,
    HermesApprovalChoice,
    ParsedApproval,
)
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class PendingApproval:
    request: ApprovalRequest
    state: ApprovalState = ApprovalState.PENDING
    approved_steps: list[int] = field(default_factory=list)
    excluded_steps: list[int] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


ApprovalHandler = Callable[[PendingApproval], Awaitable[ParsedApproval]]


def map_decision_to_hermes_choice(decision: ApprovalDecision) -> HermesApprovalChoice:
    """Map local NL approval decisions to Hermes Agent API choices."""
    match decision:
        case ApprovalDecision.APPROVE_ALL:
            return HermesApprovalChoice.SESSION
        case ApprovalDecision.APPROVE | ApprovalDecision.CONTINUE | ApprovalDecision.PARTIAL:
            return HermesApprovalChoice.ONCE
        case ApprovalDecision.CANCEL | ApprovalDecision.REJECT:
            return HermesApprovalChoice.DENY
        case _:
            return HermesApprovalChoice.ONCE


class NaturalLanguageApprovalParser:
    """Parse natural language approval responses."""

    APPROVE_ALL_PATTERNS = [
        r"(hepsini|taminini|tumunu)\s*onay",
        r"onayliyorum",
        r"^evet$",
        r"^tamam$",
        r"^ok$",
        r"devam\s*et",
        r"approve\s*all",
    ]

    REJECT_PATTERNS = [
        r"iptal",
        r"vazgec",
        r"hayir",
        r"reject",
        r"cancel",
    ]

    PARTIAL_PATTERNS = [
        r"sadece\s+([\d,\sve]+)",
        r"(\d+(?:\s*[,ve]\s*\d+)*)\s*(?:numarali|nolu)?\s*(?:adim|step)",
        r"(\d+)\.\s*adimi\s*cikar",
    ]

    @classmethod
    def _normalize(cls, text: str) -> str:
        mapping = {
            "\u0130": "i",
            "\u0131": "i",
            "\u015e": "s",
            "\u015f": "s",
            "\u011e": "g",
            "\u011f": "g",
            "\u00dc": "u",
            "\u00fc": "u",
            "\u00d6": "o",
            "\u00f6": "o",
            "\u00c7": "c",
            "\u00e7": "c",
        }
        normalized = text
        for src, dst in mapping.items():
            normalized = normalized.replace(src, dst)
        return normalized.lower().strip()

    @classmethod
    def parse(cls, text: str, total_steps: int) -> ParsedApproval:
        normalized = cls._normalize(text)

        for pattern in cls.REJECT_PATTERNS:
            if re.search(pattern, normalized):
                return ParsedApproval(decision=ApprovalDecision.CANCEL, message=text)

        for pattern in cls.APPROVE_ALL_PATTERNS:
            if re.search(pattern, normalized):
                return ParsedApproval(decision=ApprovalDecision.APPROVE_ALL, message=text)

        for pattern in cls.PARTIAL_PATTERNS:
            match = re.search(pattern, normalized)
            if match:
                numbers = re.findall(r"\d+", match.group(0))
                steps = [int(n) for n in numbers if 1 <= int(n) <= total_steps]
                if steps:
                    if "cikar" in normalized or "exclude" in normalized:
                        excluded = steps
                        approved = [i for i in range(1, total_steps + 1) if i not in excluded]
                        return ParsedApproval(
                            decision=ApprovalDecision.PARTIAL,
                            approved_steps=approved,
                            excluded_steps=excluded,
                            message=text,
                        )
                    return ParsedApproval(
                        decision=ApprovalDecision.PARTIAL,
                        approved_steps=steps,
                        message=text,
                    )

        return ParsedApproval(decision=ApprovalDecision.APPROVE, message=text)


class ApprovalManager:
    """Manages pending approval requests from Hermes Server."""

    def __init__(self, handler: ApprovalHandler | None = None) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._handler = handler
        self._bulk_approved_runs: set[str] = set()

    def set_handler(self, handler: ApprovalHandler) -> None:
        self._handler = handler

    def register(self, request: ApprovalRequest) -> PendingApproval:
        key = request.id or request.run_id
        pending = PendingApproval(request=request)
        self._pending[key] = pending
        logger.info(
            "approval_registered",
            approval_id=key,
            run_id=request.run_id,
            steps=len(request.plan_steps),
        )
        return pending

    def is_run_bulk_approved(self, run_id: str) -> bool:
        return run_id in self._bulk_approved_runs

    def mark_run_bulk_approved(self, run_id: str) -> None:
        self._bulk_approved_runs.add(run_id)

    async def resolve(
        self,
        approval_id: str,
        user_input: str | None = None,
        *,
        auto_response: ParsedApproval | None = None,
    ) -> ParsedApproval:
        pending = self._pending.get(approval_id)
        if not pending:
            raise KeyError(f"No pending approval: {approval_id}")

        if auto_response:
            response = auto_response
        elif self._handler:
            response = await self._handler(pending)
        elif user_input:
            response = NaturalLanguageApprovalParser.parse(
                user_input,
                total_steps=len(pending.request.plan_steps) or 1,
            )
        else:
            raise ValueError("No approval handler or user input provided.")

        response.approval_id = approval_id

        match response.decision:
            case ApprovalDecision.APPROVE_ALL:
                pending.state = ApprovalState.APPROVED
                pending.approved_steps = list(range(1, len(pending.request.plan_steps) + 1))
                self.mark_run_bulk_approved(pending.request.run_id)
            case ApprovalDecision.PARTIAL:
                pending.state = ApprovalState.PARTIALLY_APPROVED
                pending.approved_steps = response.approved_steps or []
                pending.excluded_steps = response.excluded_steps or []
            case ApprovalDecision.CANCEL | ApprovalDecision.REJECT:
                pending.state = ApprovalState.CANCELLED
            case _:
                pending.state = ApprovalState.APPROVED

        return response

    def should_require_new_approval(self, run_id: str, tool_name: str) -> bool:
        if not self.is_run_bulk_approved(run_id):
            return True
        high_risk_keywords = ("delete", "uninstall", "registry", "firewall", "format")
        return any(k in tool_name.lower() for k in high_risk_keywords)
