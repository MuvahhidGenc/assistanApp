"""Test-only approval provider helpers.

Tests must inject an explicit approval provider; the production
runtime must never auto-approve on its own. These helpers make the
test wiring explicit and obvious — the security model says "no
default auto-approve, ever" and tests demonstrate that the runtime
honours it.

The provider below always returns ``APPROVE`` (or ``APPROVE_ALL`` for
the bulk variant) so tests that don't care about the approval flow
can keep working. Tests that *do* care — the security tests — inject
``RejectingApprovalProvider`` or ``ConditionalApprovalProvider`` to
exercise the deny path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from hermes.security.approval_manager import ParsedApproval
from hermes.server.models import ApprovalDecision


def approving_provider(
    *,
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    on_call: Callable[[Any], None] | None = None,
) -> Callable[[Any], Awaitable[ParsedApproval]]:
    """Return a provider that approves every request.

    The optional ``on_call`` hook lets a test observe every approval
    request that passes through the provider — useful for asserting
    that the runtime really did invoke the security chain.
    """

    async def _provider(pending: Any) -> ParsedApproval:
        if on_call is not None:
            on_call(pending)
        return ParsedApproval(decision=decision, message=f"approved:{decision.value}")

    return _provider


def rejecting_provider(
    *,
    on_call: Callable[[Any], None] | None = None,
) -> Callable[[Any], Awaitable[ParsedApproval]]:
    """Return a provider that rejects every request — for deny tests."""

    async def _provider(pending: Any) -> ParsedApproval:
        if on_call is not None:
            on_call(pending)
        return ParsedApproval(
            decision=ApprovalDecision.REJECT,
            message=f"rejected:{getattr(pending.request, 'tool', '')}",
        )

    return _provider


def conditional_provider(
    predicate: Callable[[Any], bool],
    *,
    default: ApprovalDecision = ApprovalDecision.REJECT,
) -> Callable[[Any], Awaitable[ParsedApproval]]:
    """Return a provider that approves when ``predicate(pending)`` is true.

    Lets a test approve some actions and reject others based on the
    request payload — e.g. approve read-only tools, reject writes.
    """

    async def _provider(pending: Any) -> ParsedApproval:
        if predicate(pending):
            return ParsedApproval(decision=ApprovalDecision.APPROVE, message="conditional_approve")
        return ParsedApproval(decision=default, message="conditional_default")

    return _provider


__all__ = [
    "approving_provider",
    "conditional_provider",
    "rejecting_provider",
]