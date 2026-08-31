from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyDecision, PolicyEngine
from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.tools.base import BaseTool, ToolExecutionResult
from hermes.tools.registry import ToolRegistry
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


class ToolDeniedError(ToolExecutionError):
    pass


class ToolApprovalRequiredError(ToolExecutionError):
    def __init__(self, tool_call: ToolCallRequest, reason: str) -> None:
        super().__init__(reason)
        self.tool_call = tool_call
        self.reason = reason


class ToolExecutor:
    """Executes local tools through Policy → Approval → Execute → Audit chain."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: PolicyEngine,
        audit_logger: AuditLogger,
        approval_manager: ApprovalManager,
    ) -> None:
        self._registry = registry
        self._policy = policy_engine
        self._audit = audit_logger
        self._approval = approval_manager

    async def execute_tool_call(
        self,
        tool_call: ToolCallRequest,
        *,
        run_id: str = "",
        skip_approval: bool = False,
    ) -> ToolResultPayload:
        tool_name = tool_call.name
        arguments = tool_call.arguments

        self._audit.log(
            "tool_call_received",
            tool=tool_name,
            tool_call_id=tool_call.id,
            run_id=run_id,
            arguments=arguments,
        )

        policy = self._policy.evaluate(
            tool_name,
            arguments,
            server_risk_level=tool_call.risk_level,
        )

        if policy.decision == PolicyDecision.DENY:
            self._audit.log("tool_denied", tool=tool_name, reason=policy.reason)
            return ToolResultPayload(
                tool_call_id=tool_call.id,
                success=False,
                error=f"Denied by local policy: {policy.reason}",
            )

        if policy.decision == PolicyDecision.REQUIRE_APPROVAL and not skip_approval:
            if run_id and not self._approval.should_require_new_approval(run_id, tool_name):
                pass  # bulk-approved run, low-risk tool
            else:
                self._audit.log("tool_approval_required", tool=tool_name, reason=policy.reason)
                raise ToolApprovalRequiredError(tool_call, policy.reason)

        tool = self._registry.get(tool_name)
        if not tool:
            error = f"Unknown local tool: {tool_name}"
            self._audit.log("tool_not_found", tool=tool_name)
            return ToolResultPayload(tool_call_id=tool_call.id, success=False, error=error)

        try:
            result = await tool.execute(**arguments)
            self._audit.log(
                "tool_executed",
                tool=tool_name,
                success=result.success,
                verified=result.verified,
            )
            return ToolResultPayload(
                tool_call_id=tool_call.id,
                success=result.success,
                output=result.output,
                error=result.error,
            )
        except Exception as exc:
            self._audit.log("tool_error", tool=tool_name, error=str(exc))
            return ToolResultPayload(
                tool_call_id=tool_call.id,
                success=False,
                error=str(exc),
            )

    async def execute_batch(
        self,
        tool_calls: list[ToolCallRequest],
        *,
        run_id: str = "",
        skip_approval: bool = False,
    ) -> list[ToolResultPayload]:
        results: list[ToolResultPayload] = []
        for call in tool_calls:
            try:
                result = await self.execute_tool_call(call, run_id=run_id, skip_approval=skip_approval)
                results.append(result)
            except ToolApprovalRequiredError:
                raise
        return results
