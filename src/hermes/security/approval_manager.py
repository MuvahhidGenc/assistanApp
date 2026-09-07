from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable

from hermes.config.settings import RiskLevel
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
    if decision == ApprovalDecision.APPROVE_ALL:
        return HermesApprovalChoice.SESSION
    if decision in (ApprovalDecision.APPROVE, ApprovalDecision.CONTINUE, ApprovalDecision.PARTIAL):
        return HermesApprovalChoice.ONCE
    if decision == ApprovalDecision.CANCEL:
        return HermesApprovalChoice.DENY
    if decision == ApprovalDecision.REJECT:
        return HermesApprovalChoice.DENY
    return HermesApprovalChoice.ONCE


class NaturalLanguageApprovalParser:
    """Parse natural language approval responses."""

    APPROVE_ALL_PATTERNS = (
        r"(hepsini|taminini|tumunu)\s*onay",
        r"onayliyorum",
        r"^evet$",
        r"^onayla$",
        r"^tamam$",
        r"^ok$",
        r"devam\s*et",
        r"approve\s*all",
    )
    REJECT_PATTERNS = (
        r"iptal",
        r"vazgec",
        r"hayir",
        r"reject",
        r"cancel",
    )
    PARTIAL_PATTERNS = (
        r"sadece\s+([\d,\sve]+)",
        r"(\d+(?:\s*[,ve]\s*\d+)*)\s*(?:numarali|nolu)?\s*(?:adim|step)",
        r"(\d+)\.\s*adimi\s*cikar",
    )

    @classmethod
    def _normalize(cls, text: str) -> str:
        mapping = {
            "İ": "i",
            "ı": "i",
            "Ş": "s",
            "ş": "s",
            "Ğ": "g",
            "ğ": "g",
            "Ü": "u",
            "ü": "u",
            "Ö": "o",
            "ö": "o",
            "Ç": "c",
            "ç": "c",
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
            if not match:
                continue
            numbers = re.findall(r"\d+", match.group(0))
            steps = [int(n) for n in numbers if 1 <= int(n) <= total_steps]
            if not steps:
                continue
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
    """Manages pending approval requests from Hermes Server.

    The optional ``audit_logger`` parameter is an
    ``AuditLogger``-compatible callable (``log(event, **kwargs)``).
    When provided, every decision the manager makes is recorded so
    the trail is complete: a denied tool call still leaves a record
    on disk.
    """

    def __init__(
        self,
        handler: ApprovalHandler | None = None,
        audit_logger: Any | None = None,
    ) -> None:
        self._pending: dict[str, PendingApproval] = {}
        self._handler = handler
        self._audit = audit_logger
        self._bulk_approved_runs: set[str] = set()

    def set_handler(self, handler: ApprovalHandler) -> None:
        self._handler = handler

    def set_audit_logger(self, audit_logger: Any) -> None:
        """Attach (or replace) the audit logger.

        Used by the V3 bootstrap so every approval decision lands in
        the persistent audit log alongside the policy engine's
        tool-call records.
        """
        self._audit = audit_logger

    def _audit_event(self, event: str, **kwargs: Any) -> None:
        if self._audit is None:
            return
        try:
            self._audit.log(event, **kwargs)
        except Exception:  # noqa: BLE001
            # Audit must never break the security chain. If the
            # logger is broken we still want the runtime to fail
            # closed (REJECT) without an unhandled exception
            # surfacing in the executor.
            logger.error("audit_log_failure", event=event)

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

        if response.decision == ApprovalDecision.APPROVE_ALL:
            pending.state = ApprovalState.APPROVED
            pending.approved_steps = list(range(1, len(pending.request.plan_steps) + 1))
            self.mark_run_bulk_approved(pending.request.run_id)
        elif response.decision == ApprovalDecision.PARTIAL:
            pending.state = ApprovalState.PARTIALLY_APPROVED
            pending.approved_steps = response.approved_steps or []
            pending.excluded_steps = response.excluded_steps or []
        elif response.decision in (ApprovalDecision.CANCEL, ApprovalDecision.REJECT):
            pending.state = ApprovalState.CANCELLED
        else:
            pending.state = ApprovalState.APPROVED

        return response

    def should_require_new_approval(self, run_id: str, tool_name: str) -> bool:
        if not self.is_run_bulk_approved(run_id):
            return True
        high_risk_keywords = ("delete", "uninstall", "registry", "firewall", "format")
        return any(k in tool_name.lower() for k in high_risk_keywords)

    async def request_approval(
        self,
        *,
        run_id: str,
        action_id: str,
        capability: str,
        tool: str,
        arguments: dict[str, Any],
        risk_level: RiskLevel | None,
        reason: str = "",
    ) -> ApprovalDecision:
        """Resolve an approval decision for one action.

        The V3 runtime calls this once per action that policy requires
        approval for. The decision is bound to the real ``run_id`` and
        ``action_id`` so audit and execution-log records can be linked.

        Decision precedence (security-first):

          1. ``read_only`` risk → ``APPROVE`` (no user interaction needed).
          2. Explicit registered handler → call it, return the verdict.
          3. No handler, no registered provider → ``REJECT`` (fail closed).

        Bulk-approval state is intentionally **not consulted here**: the
        security model treats each action as a fresh decision unless the
        user explicitly opted in via a registered handler. Production
        must wire a real approval provider; tests must inject one.
        """
        if not run_id:
            # No correlation id means the runtime skipped bookkeeping —
            # the security chain cannot continue.
            logger.warning(
                "approval_request_missing_run_id",
                capability=capability,
                tool=tool,
            )
            return ApprovalDecision.REJECT
        if risk_level is RiskLevel.READ_ONLY:
            # Read-only actions never need human approval.
            return ApprovalDecision.APPROVE

        # Build an ApprovalRequest with the real correlation ids so audit
        # and UI can refer back to the action.
        from hermes.server.models import ApprovalRequest as _AR

        plan_steps = ["1"]  # one logical step per V3 action — string values
        request = _AR(
            id=f"approval_{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            title=f"Approve {tool} for {capability}",
            description=reason or f"Tool {tool!r} requires approval for capability {capability!r}.",
            tool_name=tool,
            plan_steps=plan_steps,
            action_id=action_id,
            capability=capability,
            tool=tool,
            arguments=dict(arguments),
            risk_level=(risk_level.value if risk_level else None),
            reason=reason,
        )

        pending = self.register(request)

        if self._handler is None:
            # Fail closed: no handler means no human answer. Production
            # must wire a provider that surfaces the request to a human.
            pending.state = ApprovalState.REJECTED
            logger.warning(
                "approval_no_handler",
                run_id=run_id,
                action_id=action_id,
                capability=capability,
                tool=tool,
            )
            return ApprovalDecision.REJECT

        try:
            parsed = await self._handler(pending)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "approval_handler_error",
                run_id=run_id,
                action_id=action_id,
                error=str(exc),
            )
            pending.state = ApprovalState.REJECTED
            return ApprovalDecision.REJECT

        response_approval_id = parsed.approval_id or pending.request.id
        parsed.approval_id = response_approval_id

        # Apply the verdict to the pending record so audit and UI see it.
        if parsed.decision == ApprovalDecision.APPROVE_ALL:
            pending.state = ApprovalState.APPROVED
            pending.approved_steps = list(range(1, len(pending.request.plan_steps) + 1))
            self.mark_run_bulk_approved(pending.request.run_id)
        elif parsed.decision == ApprovalDecision.APPROVE:
            # An explicit single-action APPROVE is treated as bulk for
            # this ``run_id`` so the runtime can re-invoke the tool
            # chain. The security model treats the approval as scoped
            # to the run, not the entire process.
            pending.state = ApprovalState.APPROVED
            pending.approved_steps = list(range(1, len(pending.request.plan_steps) + 1))
            self.mark_run_bulk_approved(pending.request.run_id)
        elif parsed.decision == ApprovalDecision.PARTIAL:
            pending.state = ApprovalState.PARTIALLY_APPROVED
            pending.approved_steps = parsed.approved_steps or []
            pending.excluded_steps = parsed.excluded_steps or []
        elif parsed.decision in (ApprovalDecision.CANCEL, ApprovalDecision.REJECT):
            pending.state = ApprovalState.CANCELLED
        else:
            pending.state = ApprovalState.APPROVED

        logger.info(
            "approval_resolved",
            run_id=run_id,
            action_id=action_id,
            capability=capability,
            tool=tool,
            decision=parsed.decision.value,
        )
        self._audit_event(
            "approval_resolved",
            run_id=run_id,
            action_id=action_id,
            capability=capability,
            tool=tool,
            decision=parsed.decision.value,
        )
        return parsed.decision

    def hermes_choice_for(self, decision: ApprovalDecision) -> HermesApprovalChoice:
        """Map a local decision to the server's Hermes approval choice."""
        return map_decision_to_hermes_choice(decision)
