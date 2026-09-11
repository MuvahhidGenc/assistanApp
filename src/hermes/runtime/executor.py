"""V3 runtime executor — turns a runtime `Action` into real tool execution.

The executor is the only place in V3 that calls a tool. It:

  1. looks up the capability's tools in the registry,
  2. picks the lowest-risk tool that fits the payload (delegating to
     `capability.selector`),
  3. records the action as `ACTION_STARTED` in the Execution Log,
  4. runs the tool through `ToolExecutor` (V2) so the existing
     Policy → Approval → Audit chain stays intact,
  5. records `ACTION_FINISHED` with the tool's own report,
  6. runs the verifier (if bound) and records `VERIFICATION_RECORDED`,
  7. optionally captures an `OBSERVATION_RECORDED` event.

The executor never makes a routing decision. It only translates a typed
`Action` into events and side-effects.

It does **not**:

  * inspect user language,
  * decide which capability to invoke,
  * override security policy,
  * invent `success=True` if the tool reported failure.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from hermes.capability import (
    CapabilityRegistry,
    CapabilityToolSelection,
    create_default_capability_registry,
    select_tool_for_capability,
)
from hermes.execution_log import (
    EventEnvelope,
    EventKind,
    ExecutionLogStore,
    RecoveryEvidenceRecorder,
    action_finished_payload,
    action_started_payload,
    observation_recorded_payload,
    recovery_finished_payload,
    recovery_started_payload,
)
from hermes.execution_log.verifier import record_verification
from hermes.reasoning.decision import Action
from hermes.security.approval_manager import ApprovalManager
from hermes.tools.executor import ToolApprovalRequiredError, ToolExecutor, ToolResultPayload
from hermes.tools.registry import ToolRegistry, create_default_registry
from hermes.tools.verifiers.base import (
    Observation,
    VerificationResult,
    VerificationStatus,
)
from hermes.tools.verifiers.registry import VerifierRegistry, create_default_verifier_registry


@dataclass
class ActionExecutionOutcome:
    """The result of executing a single runtime action."""

    capability: str
    selection: CapabilityToolSelection | None
    action_id: str
    payload: ToolResultPayload | None
    started_envelope: EventEnvelope
    finished_envelope: EventEnvelope | None = None
    verification_envelope: EventEnvelope | None = None
    observation_envelope: EventEnvelope | None = None
    recovery_envelopes: tuple[EventEnvelope, ...] = ()
    error: str | None = None
    approval_outcome: str | None = None

    @property
    def action_success(self) -> bool:
        """`True` if the tool reported success. *Not* the same as verification."""
        return bool(self.payload and self.payload.success)

    @property
    def verified(self) -> bool:
        if self.verification_envelope is None:
            return False
        return getattr(self.verification_envelope.payload, "status", "") == "verified"

    @property
    def verification_status(self) -> str:
        if self.verification_envelope is None:
            return "unknown"
        return str(getattr(self.verification_envelope.payload, "status", "unknown"))


@dataclass
class V3Executor:
    """Bridges runtime `Action` to real V2 tool execution + execution.

    Security is enforced through the underlying ``ToolExecutor`` chain
    (Policy → Approval → Audit). The executor never bypasses approval;
    when ``ToolExecutor`` raises ``ToolApprovalRequiredError`` the V3
    executor delegates to ``ApprovalManager.request_approval`` which
    binds the decision to the real ``run_id`` and ``action_id``.
    """

    tool_registry: ToolRegistry
    capability_registry: CapabilityRegistry
    execution_log: ExecutionLogStore
    tool_executor: ToolExecutor
    verifier_registry: VerifierRegistry
    approval_manager: ApprovalManager
    recovery_budget_per_action: int = 2

    @classmethod
    def from_defaults(
        cls,
        *,
        execution_log: ExecutionLogStore,
        tool_executor: ToolExecutor,
        approval_manager: ApprovalManager | None = None,
        tool_registry: ToolRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
        verifier_registry: VerifierRegistry | None = None,
        recovery_budget_per_action: int = 2,
    ) -> "V3Executor":
        tools = tool_registry or create_default_registry()
        caps = capability_registry or create_default_capability_registry(tools)
        vers = verifier_registry or create_default_verifier_registry()
        approval = approval_manager or getattr(tool_executor, "_approval", None) or ApprovalManager()
        return cls(
            tool_registry=tools,
            capability_registry=caps,
            execution_log=execution_log,
            tool_executor=tool_executor,
            verifier_registry=vers,
            approval_manager=approval,
            recovery_budget_per_action=recovery_budget_per_action,
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def execute(
        self,
        *,
        action: Action,
        correlation_id: str,
        action_id: str | None = None,
    ) -> ActionExecutionOutcome:
        """Translate an `Action` into recorded execution + outcome."""
        if not action.capability:
            raise ValueError("Action.capability is required")

        # 1. Look up the capability and pick the best-fitting tool.
        selection = select_tool_for_capability(
            self.capability_registry,
            tool_registry=self.tool_registry,
            capability=action.capability,
            inputs=action.arguments,
        )
        if selection is None:
            raise LookupError(
                f"No tool in capability {action.capability!r} accepts the requested arguments"
            )

        # 2. Record ACTION_STARTED.
        action_id = action_id or f"act_{uuid.uuid4().hex[:12]}"
        started_envelope = action_started_payload(
            correlation_id=correlation_id,
            action_id=action_id,
            capability=action.capability,
            tool=selection.tool_name,
            arguments=selection.arguments,
            execution_target="client",
            risk_level=str(selection.risk_level.value) if selection.risk_level else None,
            security_decision=None,
            approval_outcome=None,
        )
        self.execution_log.append(started_envelope)
        # If we have to ask for approval later, we will update the
        # ``approval_outcome`` field on the started envelope so audit
        # can correlate policy/approval/audit in one record. The
        # append-only invariant forbids editing past records; the
        # resolution is recorded as a separate observation in the log.

        # 3. Run the tool through the security chain. ``ToolExecutor`` raises
        # ``ToolApprovalRequiredError`` when policy requires approval; we
        # delegate that decision to ``ApprovalManager.request_approval``
        # so the audit trail binds the verdict to this exact action.
        started_at = started_envelope.recorded_at
        payload: ToolResultPayload | None = None
        error: str | None = None
        approval_outcome: str | None = None
        cancelled = False
        try:
            from hermes.server.models import ToolCallRequest
            from hermes.tools.execution_target import ExecutionTarget

            call = ToolCallRequest(
                id=action_id,
                name=selection.tool_name,
                arguments=dict(selection.arguments),
            )
            try:
                payload = await self.tool_executor.execute_tool_call(
                    call,
                    run_id=correlation_id,
                    runtime=ExecutionTarget.CLIENT,
                    verify=False,
                )
            except ToolApprovalRequiredError as approval_exc:
                # The V2 chain requires approval. Delegate to the new
                # ``ApprovalManager.request_approval`` API which binds
                # the decision to this exact ``run_id`` / ``action_id``.
                from hermes.server.models import ApprovalDecision as _AD

                decision = await self.approval_manager.request_approval(
                    run_id=correlation_id,
                    action_id=action_id,
                    capability=action.capability,
                    tool=selection.tool_name,
                    arguments=dict(selection.arguments),
                    risk_level=selection.risk_level,
                    reason=str(approval_exc),
                )
                approval_outcome = decision.value
                if decision in (_AD.APPROVE, _AD.APPROVE_ALL):
                    # Approved: retry through the chain. V2 will now
                    # honour the recorded bulk-approval flag bound to
                    # this real ``run_id``; the per-call ``skip_approval``
                    # bypass has been removed.
                    payload = await self.tool_executor.execute_tool_call(
                        call,
                        run_id=correlation_id,
                        runtime=ExecutionTarget.CLIENT,
                        verify=False,
                    )
                else:
                    # Denied / cancelled / rejected: do not run the tool.
                    error = str(approval_exc) + f" [approval={decision.value}]"
                    payload = ToolResultPayload(
                        tool_call_id=call.id,
                        success=False,
                        error=error,
                    )
        except asyncio.CancelledError:
            cancelled = True
            error = "action_cancelled"
        except Exception as exc:  # noqa: BLE001 — record and continue
            error = str(exc)
            payload = ToolResultPayload(
                tool_call_id=action_id,
                success=False,
                error=error,
            )

        # 4. Record ACTION_FINISHED.
        from datetime import datetime, timezone

        finished_at = datetime.now(timezone.utc).isoformat()
        duration_ms = _duration_ms(started_at, finished_at)
        finished_envelope = action_finished_payload(
            correlation_id=correlation_id,
            action_id=action_id,
            tool=selection.tool_name,
            success=bool(payload and payload.success),
            output=(payload.output if payload else None),
            error=(payload.error if payload else error),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )
        self.execution_log.append(finished_envelope)

        outcome = ActionExecutionOutcome(
            capability=action.capability,
            selection=selection,
            action_id=action_id,
            payload=payload,
            started_envelope=started_envelope,
            finished_envelope=finished_envelope,
            error=(payload.error if payload and payload.error else error),
            approval_outcome=approval_outcome,
        )

        if cancelled:
            raise asyncio.CancelledError

        # 5. Run the bound verifier (if any) and record the result.
        if payload is not None:
            verification_envelope, observation_envelope = await self._verify(
                action=action,
                selection=selection,
                correlation_id=correlation_id,
                action_id=action_id,
                payload=payload,
            )
            outcome.verification_envelope = verification_envelope
            outcome.observation_envelope = observation_envelope

        # 6. The runtime owns recovery decisions. The orchestrator will
        #    call `executor.attempt_recovery(...)` when the runtime returns
        #    a recovery intent; we leave the actual evidence pair to be
        #    recorded by that call so the runtime's verdict always wins.

        return outcome

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _verify(
        self,
        *,
        action: Action,
        selection: CapabilityToolSelection,
        correlation_id: str,
        action_id: str,
        payload: ToolResultPayload,
    ) -> tuple[EventEnvelope, EventEnvelope | None]:
        verifier = self.verifier_registry.get(selection.tool_name)
        contract = self.capability_registry.get(action.capability)
        timeout_seconds = (
            contract.verification.timeout_seconds
            if contract is not None and contract.verification is not None
            else 10.0
        )

        from hermes.tools.verifiers.context import VerifierContext

        context = VerifierContext(
            tool_name=selection.tool_name,
            tool_arguments=dict(selection.arguments),
            execution_success=bool(payload.success),
            execution_output=payload.output,
            execution_error=payload.error,
            run_id=correlation_id,
            timeout_seconds=timeout_seconds,
        )
        if verifier is None:
            if contract is not None and contract.risk_level.value == "read_only":
                result = VerificationResult(
                    status=VerificationStatus.NOT_REQUIRED,
                    method="read_only_observation",
                    details={"reason": "read_only_capability"},
                    observation=Observation(
                        source=action.capability,
                        data=_observation_payload(payload.output),
                    ),
                )
            else:
                result = VerificationResult(
                    status=VerificationStatus.UNKNOWN,
                    method=(
                        contract.verification.method
                        if contract is not None and contract.verification is not None
                        else "verifier_unavailable"
                    ),
                    details={"reason": "no_bound_verifier"},
                )
        else:
            try:
                result = await self.verifier_registry.verify(context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — convert failure into explicit evidence
                result = VerificationResult(
                    status=VerificationStatus.UNKNOWN,
                    method=f"{selection.tool_name}_verifier_error",
                    details={"reason": "verifier_error", "error": str(exc)},
                )

        observation_envelope: EventEnvelope | None = None
        if result.observation is not None:
            observation_envelope = self.execution_log.append(
                observation_recorded_payload(
                    correlation_id=correlation_id,
                    action_id=action_id,
                    source=result.observation.source,
                    observation_type="structured",
                    data=dict(result.observation.data),
                )
            )

        verification_envelope = record_verification(
            self.execution_log,
            correlation_id=correlation_id,
            action_id=action_id,
            verifier_name=type(verifier).__name__ if verifier is not None else "None",
            result=result,
        )
        return verification_envelope, observation_envelope

    async def attempt_recovery(  # noqa: D401
        self,
        *,
        correlation_id: str,
        action_id: str,
        strategy_id: str,
        reason: str,
        result: str,
        user_message: str = "",
    ) -> tuple[EventEnvelope, EventEnvelope]:
        """Record a runtime-driven recovery attempt as evidence.

        The runtime decides *what* to do (retry, switch tool, ask the user,
        give up). This recorder only writes the pair of events so the
        Execution Log captures the attempt. `result` must be one of:
        ``"recovered" | "failed" | "waiting_for_user" | "requires_approval"``;
        the runtime supplies the verdict, not the executor.
        """
        recorder = RecoveryEvidenceRecorder(
            store=self.execution_log,
            correlation_id=correlation_id,
            action_id=action_id,
            budget_total=self.recovery_budget_per_action,
        )
        started = recorder.started(strategy_id=strategy_id, reason=reason)
        finished = recorder.finished(strategy_id=strategy_id, result=result, user_message=user_message)
        return started, finished


def _duration_ms(started_at: str, finished_at: str) -> int:
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at)
        return max(0, int((end - start).total_seconds() * 1000))
    except ValueError:
        return 0


def _observation_payload(output: Any) -> dict[str, Any]:
    if isinstance(output, dict):
        return dict(output)
    return {"value": output}


__all__ = ["ActionExecutionOutcome", "V3Executor"]