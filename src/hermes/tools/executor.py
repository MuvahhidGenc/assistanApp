from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel  # noqa: F401
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyDecision, PolicyEngine
from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.tools.base import BaseTool, ToolExecutionResult  # noqa: F401
from hermes.tools.execution_target import ExecutionTarget, validate_runtime_execution
from hermes.tools.registry import ToolRegistry
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class ToolExecutionError(Exception):
    pass


class ToolDeniedError(Exception):
    pass


class ToolApprovalRequiredError(Exception):
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
        verifier_registry: Any = None,
    ) -> None:
        self._registry = registry
        self._policy = policy_engine
        self._audit = audit_logger
        self._approval = approval_manager
        self._verifiers = verifier_registry

    def _verifier_registry(self) -> Any:
        if self._verifiers is None:
            from hermes.tools.verifiers.registry import create_default_verifier_registry

            self._verifiers = create_default_verifier_registry()
        return self._verifiers

    async def _verify_result(
        self,
        payload: ToolResultPayload,
        tool_name: str,
        arguments: dict[str, Any],
        run_id: str,
    ) -> ToolResultPayload:
        """Confirm the tool's claim against observable state.

        Only tools with a dedicated verifier are checked; the generic verifier
        can only restate the tool's own output, so running it here would add
        latency without adding evidence.
        """
        try:
            verifiers = self._verifier_registry()
            if verifiers.get(tool_name) is None:
                return payload

            from hermes.tools.verifiers.base import VerificationStatus
            from hermes.tools.verifiers.context import VerifierContext

            result = await verifiers.verify(
                VerifierContext(
                    tool_name=tool_name,
                    tool_arguments=dict(arguments),
                    execution_success=payload.success,
                    execution_output=payload.output,
                    execution_error=payload.error,
                    run_id=run_id,
                )
            )
        except Exception as exc:  # verification must never break execution
            logger.warning("verification_skipped", tool=tool_name, error=str(exc))
            return payload

        payload.verified = result.verified
        payload.verification_status = result.status.value
        payload.verification_method = result.method
        payload.verification_details = result.details

        # A tool reporting success while the state says otherwise is a failure.
        # UNKNOWN and timeouts are not evidence of failure, so they stand.
        if payload.success and result.status == VerificationStatus.FAILED:
            payload.success = False
            payload.error = payload.error or f"{tool_name} dogrulanamadi: {result.method}"
            self._audit.log(
                "tool_verification_failed", tool=tool_name, method=result.method
            )
            logger.info("tool_verification_failed", tool=tool_name, method=result.method)

        return payload

    async def execute_tool_call(
        self,
        tool_call: ToolCallRequest,
        run_id: str = "",
        skip_approval: bool = False,
        *,
        runtime: ExecutionTarget = ExecutionTarget.CLIENT,
        user_message: str = "",
        verify: bool = True,
    ) -> ToolResultPayload:
        tool_name = tool_call.name
        arguments = tool_call.arguments

        allowed, boundary_reason = validate_runtime_execution(
            tool_name,
            self._registry,
            runtime=runtime,
            user_message=user_message,
            envelope_target=getattr(tool_call, "execution_target", None),
        )
        if not allowed:
            self._audit.log(
                "tool_execution_boundary_denied",
                tool=tool_name,
                runtime=runtime.value,
                reason=boundary_reason,
            )
            return ToolResultPayload(
                tool_call_id=tool_call.id,
                success=False,
                error=boundary_reason,
            )

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
            runtime=runtime,
            envelope_target=getattr(tool_call, "execution_target", None),
        )

        if policy.decision == PolicyDecision.DENY:
            self._audit.log("tool_denied", tool=tool_name, reason=policy.reason)
            return ToolResultPayload(
                tool_call_id=tool_call.id,
                success=False,
                error=f"Denied by local policy: {policy.reason}",
            )

        if policy.decision == PolicyDecision.REQUIRE_APPROVAL and not skip_approval:
            if not run_id or self._approval.should_require_new_approval(run_id, tool_name):
                self._audit.log("tool_approval_required", tool=tool_name, reason=policy.reason)
                raise ToolApprovalRequiredError(tool_call, policy.reason)

        tool = self._registry.get(tool_name)
        if not tool:
            error = f"Unknown local tool: {tool_name}"
            self._audit.log("tool_not_found", tool=tool_name)
            return ToolResultPayload(tool_call_id=tool_call.id, success=False, error=error)

        try:
            exec_kwargs = dict(arguments)
            if user_message:
                exec_kwargs["user_message"] = user_message
            result = await tool.execute(**exec_kwargs)
            self._audit.log(
                "tool_executed",
                tool=tool_name,
                success=result.success,
                verified=result.verified,
            )
            if not result.success:
                logger.info(
                    "tool_failed",
                    tool=tool_name,
                    error=(result.error or "")[:500],
                    run_id=run_id,
                )
            else:
                logger.info(
                    "tool_succeeded",
                    tool=tool_name,
                    run_id=run_id,
                    verified=result.verified,
                )
            payload = ToolResultPayload(
                tool_call_id=tool_call.id,
                success=result.success,
                output=result.output,
                error=result.error,
                verified=result.verified,
                verification_status=result.verification_status,
                verification_method=result.verification_method,
                verification_details=result.verification_details,
            )
            if verify:
                payload = await self._verify_result(payload, tool_name, arguments, run_id)
            return payload
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
        run_id: str = "",
        skip_approval: bool = False,
    ) -> list[ToolResultPayload]:
        results: list[ToolResultPayload] = []
        for call in tool_calls:
            try:
                result = await self.execute_tool_call(
                    call, run_id=run_id, skip_approval=skip_approval
                )
                results.append(result)
            except ToolApprovalRequiredError:
                raise
        return results
