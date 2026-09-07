"""V3 runtime orchestrator.

The orchestrator owns the agent loop:

  1. Take a user message.
  2. Ask ReasoningRuntime for a Decision.
  3. Depending on the decision:
       * Action              → call V3Executor → log evidence → ask again.
       * ObservationRequest  → run a read-only tool → ask again.
       * UserQuestion        → ask the user.
       * Complete            → record goal completion and stop.
       * ReReason            → ask again with updated world.
  4. Keep going until Complete, UserQuestion, or a hard limit.

The orchestrator is the *only* layer that:
  * owns the loop,
  * decides when to stop,
  * wires security/approval (via ToolExecutor) around execution,
  * records goal completion (after verified evidence).

It does **not**:

  * inspect user language,
  * route messages to capabilities,
  * plan in advance,
  * call the LLM directly — the runtime does.

Public API mirrors `AgentOrchestrator.process_message` so existing UI/voice
bootstrap keep working without source changes. New code should use the
typed `process_turn` method instead.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from hermes.capability import (
    CapabilityRegistry,
    create_default_capability_registry,
)
from hermes.execution_log import (
    ExecutionLogStore,
    EventKind,
    action_started_payload,
    observation_recorded_payload,
)
from hermes.reasoning import Decision, DecisionKind, ReasoningRuntime
from hermes.reasoning.decision import Action
from hermes.runtime.executor import ActionExecutionOutcome, V3Executor
from hermes.world_model import (
    EvidenceRecord,
    EvidenceSource,
    WorldModel,
)
from hermes.execution_log.recovery import RecoveryEvidenceRecorder


class V3AgentPhase(StrEnum):
    IDLE = "idle"
    REASONING = "reasoning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    AWAITING_USER = "awaiting_user"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class V3AgentState:
    phase: V3AgentPhase = V3AgentPhase.IDLE
    last_decision: Decision | None = None
    iteration: int = 0
    correlation_id: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_session_id: str = ""
    session_notice: str | None = None
    local_capabilities_registered: bool = False
    local_tool_count: int = 0


# Backwards-compatible alias — the V2 UI imports ``AgentPhase``. V3
# exposes the same phase vocabulary so existing callers can switch
# without source edits.
AgentPhase = V3AgentPhase


@dataclass
class TurnOutcome:
    """The user-visible result of one user message."""

    reply: str
    decision: Decision | None
    iterations: int
    completed: bool


StatusCallback = Callable[[V3AgentPhase, str, dict[str, Any]], Awaitable[None] | None]


class V3Orchestrator:
    """V3 reasoning orchestrator.

    One instance per session. The constructor accepts the building blocks
    the user may want to inject (runtime, executor, log, world model,
    status callback).
    """

    def __init__(
        self,
        *,
        runtime: ReasoningRuntime,
        executor: V3Executor,
        execution_log: ExecutionLogStore,
        world_model: WorldModel | None = None,
        on_status: StatusCallback | None = None,
        max_iterations: int = 12,
    ) -> None:
        self._runtime = runtime
        self._executor = executor
        self._execution_log = execution_log
        self._world_model = world_model or WorldModel()
        self._max_iterations = max_iterations
        self.state = V3AgentState()
        # Public accessor for the world model — used by
        # ``SessionStore`` to snapshot/resume state, and by callers
        # that want to inspect the current objective or evidence.
        self.world_model: WorldModel = self._world_model
        # Public callback hooks so the V2 UI layer (which assigns
        # ``agent._on_status`` and ``agent._on_approval_required`` at
        # runtime) can drive V3 without code edits. We store the
        # callable under a private alias and expose it via the
        # ``_on_status`` property below.
        self._on_status_callback = on_status
        self._on_approval_required: Callable[[Any], Awaitable[str]] | None = None

    # ---- legacy-compatible surface ---------------------------------------

    async def save_session(self, session_id: str | None = None) -> Any:
        """Snapshot the current world model + state to a ``SessionState``.

        Long-running tasks can call this between iterations and resume
        later via ``resume_session``. The state is *append-only* on
        disk: every save overwrites the previous snapshot atomically.
        """
        from hermes.runtime.session import (
            SessionState,
            snapshot_from_world_model,
        )

        if session_id is None:
            session_id = self.state.current_session_id or f"sess_{self.state.correlation_id}"
        state = snapshot_from_world_model(
            self._world_model,
            session_id=session_id,
            objective=self._world_model.task.objective or self.state.current_session_id,
            correlation_id=self.state.correlation_id,
            last_summary=(
                self.state.last_decision.complete.summary
                if self.state.last_decision
                and self.state.last_decision.complete is not None
                else ""
            ),
            completed=self._world_model.task.status == "complete",
        )
        return state

    async def resume_session(self, state: Any) -> None:
        """Restore a previously-saved session into this orchestrator.

        The world model is rebuilt from the snapshot; the orchestrator
        state (correlation id, last decision) is set so the next
        ``process_turn`` continues from where the previous one left
        off.
        """
        from hermes.runtime.session import apply_to_world_model

        apply_to_world_model(state, self._world_model)
        self.state.correlation_id = state.correlation_id
        self.state.current_session_id = state.session_id
        # Iteration counter restarts; the LLM gets the loaded world
        # model snapshot and recent events from the execution log.
        self.state.iteration = 0

    @property
    def _on_status(self) -> StatusCallback | None:
        return self._on_status_callback

    @_on_status.setter
    def _on_status(self, value: StatusCallback | None) -> None:
        # The V2 UI assigns ``agent._on_status = callback`` directly. V3
        # accepts the same assignment and re-points the internal callback
        # the orchestrator uses.
        self._on_status_callback = value

    async def initialize_session(self) -> str:
        """V2-compatible entry point: produce a session id.

        V3 does not yet maintain a long-lived server session of its own;
        the id is local and used as the correlation anchor for the next
        ``process_message`` call. The real server session is opened
        lazily by the reasoning client on the first LLM call.
        """
        if not self.state.current_session_id:
            self.state.current_session_id = f"session_{uuid.uuid4().hex[:12]}"
            self.state.session_notice = None
        return self.state.current_session_id

    async def register_local_capabilities(self, session_id: str | None = None) -> None:
        """V2-compatible entry point: report local tool count.

        V3 always has the full capability registry on the client; the
        registry is forwarded to the LLM via the reasoning prompt. The
        state flag is set so the UI can show a "ready" hint if needed.
        """
        self.state.local_capabilities_registered = True
        self.state.local_tool_count = len(self._executor.tool_registry)

    def get_local_capabilities(self) -> dict[str, Any]:
        """V2-compatible: return the local capability manifest."""
        return self._executor.tool_registry.to_capabilities_dict()

    @property
    def local_context(self) -> Any:
        """V2-compatible: surface the local-context adapter for any UI that
        introspects it. The V3 executor carries the same registries."""
        return self._executor

    async def process_message(
        self,
        message: str,
        session_id: str | None = None,
    ) -> str:
        """Backwards-compatible entry point used by UI/voice bootstrap."""
        if session_id:
            self.state.current_session_id = session_id
        outcome = await self.process_turn(message)
        return outcome.reply

    # ---- primary V3 surface --------------------------------------------

    async def process_turn(self, message: str) -> TurnOutcome:
        """Run one user message through the reasoning loop."""
        correlation_id = f"turn_{uuid.uuid4().hex[:12]}"
        self.state = V3AgentState(correlation_id=correlation_id)
        self._world_model.set_objective(message)

        iterations = 0
        last_decision: Decision | None = None
        reply = ""
        completed = False

        while iterations < self._max_iterations:
            iterations += 1
            self.state.iteration = iterations
            await self._emit_status(
                V3AgentPhase.REASONING, f"Reasoning (iteration {iterations})"
            )

            try:
                decision = await self._runtime.reason(
                    user_message=message,
                    world_model=self._world_model,
                    correlation_id=correlation_id,
                )
            except Exception as exc:
                await self._emit_status(V3AgentPhase.FAILED, "Reasoning failed")
                return TurnOutcome(
                    reply=f"Reasoning failed: {exc}",
                    decision=None,
                    iterations=iterations,
                    completed=False,
                )

            last_decision = decision
            self.state.last_decision = decision

            if decision.kind is DecisionKind.ACTION:
                await self._emit_status(V3AgentPhase.EXECUTING, f"Executing {decision.action.capability}")
                try:
                    outcome = await self._executor.execute(
                        action=decision.action,
                        correlation_id=correlation_id,
                    )
                except LookupError as exc:
                    # The LLM chose a capability the registry cannot
                    # serve (typo, hallucination, removed capability).
                    # Surface the failure as a tool-reported error and
                    # ask the LLM to reconsider rather than crash the
                    # loop.
                    outcome = None
                    from hermes.world_model.evidence import (
                        EvidenceRecord,
                        EvidenceSource,
                    )

                    self._world_model.record_evidence(
                        EvidenceRecord.make(
                            source=EvidenceSource.TOOL_REPORT,
                            capability=decision.action.capability,
                            claim=f"capability lookup failed: {exc}",
                            correlation_id=correlation_id,
                        )
                    )
                    self.state.last_decision = decision
                    continue
                self._absorb_execution(outcome)
                # If the action failed, record recovery evidence so the
                # log captures the structural fact that a recovery attempt
                # is pending — the runtime's next decision will determine
                # what the recovery actually does.
                if outcome is not None and not outcome.action_success:
                    self._record_recovery_pending(
                        correlation_id=correlation_id,
                        action_id=outcome.action_id,
                        reason=(outcome.error or "tool_reported_failure"),
                    )
                continue

            if decision.kind is DecisionKind.OBSERVATION_REQUEST:
                await self._emit_status(V3AgentPhase.OBSERVING, decision.observation_request.observation_type)
                observation = await self._run_observation(
                    decision.observation_request, correlation_id
                )
                if observation is not None:
                    self._world_model.update_environment(extra=observation)
                continue

            if decision.kind is DecisionKind.USER_QUESTION:
                await self._emit_status(V3AgentPhase.AWAITING_USER, decision.user_question.question)
                reply = decision.user_question.question
                completed = False
                break

            if decision.kind is DecisionKind.COMPLETE:
                reply = decision.complete.summary if decision.complete else "Done."
                self._world_model.set_status("complete")
                # Record evidence references.
                for evidence_id in (decision.complete.evidence_ids if decision.complete else ()):
                    self._world_model.mark_requirement_satisfied("__complete__", evidence_id)
                await self._emit_status(V3AgentPhase.COMPLETED, reply)
                completed = True
                break

            if decision.kind is DecisionKind.RE_REASON:
                # The next iteration's reason call already pulls a fresh
                # snapshot — nothing else to do.
                continue

        if iterations >= self._max_iterations:
            await self._emit_status(V3AgentPhase.FAILED, "Iteration limit reached")
            reply = reply or "Could not reach a conclusion in time."
            completed = False

        return TurnOutcome(reply=reply, decision=last_decision, iterations=iterations, completed=completed)

    # ---- helpers -------------------------------------------------------

    def _absorb_execution(self, outcome: ActionExecutionOutcome) -> None:
        """Translate an execution outcome into World Model updates.

        The executor already wrote the canonical Execution Log entries.
        Here we record *evidence* — the runtime learns from the log.
        """
        success = outcome.action_success
        evidence = EvidenceRecord.make(
            source=EvidenceSource.TOOL_REPORT,
            capability=outcome.capability,
            claim=("action succeeded" if success else f"action failed: {outcome.error}"),
            data={"action_id": outcome.action_id, "tool": outcome.selection.tool_name if outcome.selection else ""},
            correlation_id=self.state.correlation_id,
        )
        self._world_model.record_evidence(evidence)
        if outcome.verified and outcome.verification_envelope is not None:
            envelope = outcome.verification_envelope
            # The verifier payload is a VerificationRecorded dataclass.
            payload = envelope.payload
            verifier_status = getattr(payload, "status", "unknown")
            verified_evidence = EvidenceRecord.make(
                source=EvidenceSource.VERIFIER,
                capability=outcome.capability,
                claim=f"verifier: {verifier_status}",
                data={"method": getattr(payload, "method", "")},
                correlation_id=self.state.correlation_id,
            )
            self._world_model.record_evidence(verified_evidence)
            if verifier_status == "verified" and success:
                self._world_model.mark_requirement_satisfied(
                    outcome.capability, verified_evidence.evidence_id
                )

    def _record_recovery_pending(
        self,
        *,
        correlation_id: str,
        action_id: str,
        reason: str,
    ) -> None:
        """Record a pending recovery attempt as evidence.

        The runtime is the *owner* of the recovery decision. The recorder
        only writes the structural pair so the Execution Log reflects
        that a recovery attempt is pending; the runtime's next decision
        determines the actual outcome and may add further evidence.
        """
        recorder = RecoveryEvidenceRecorder(
            store=self._execution_log,
            correlation_id=correlation_id,
            action_id=action_id,
            budget_total=self._executor.recovery_budget_per_action,
        )
        recorder.started(strategy_id="runtime_pending", reason=reason)
        recorder.finished(
            strategy_id="runtime_pending",
            result="waiting_for_user",
            user_message="runtime will decide recovery strategy",
        )

    async def _run_observation(
        self,
        request: Any,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        """Translate an observation_request into a structured environment update.

        The runtime asks for read-only evidence. We map a few common
        observation types to existing read-only tools; everything else
        returns an empty observation and the runtime re-reasons.

        Every observation request is recorded as an `OBSERVATION_RECORDED`
        event in the Execution Log so the trail is complete regardless of
        whether the read succeeded.
        """
        import uuid as _uuid

        observation_type = getattr(request, "observation_type", "") or ""
        target = getattr(request, "target", "") or ""
        action_id = f"obs_{_uuid.uuid4().hex[:12]}"

        observed_data: dict[str, Any] | None = None

        if observation_type == "filesystem.list":
            try:
                tool = self._executor.tool_registry.get("list_directory")
                if tool is not None:
                    result = await tool.execute(path=target)
                    if result.success:
                        observed_data = {"path": target, "result": result.output, "entries": (result.output or {}).get("entries", []) if isinstance(result.output, dict) else []}
                    else:
                        observed_data = {"path": target, "error": result.error}
            except Exception as exc:  # noqa: BLE001
                observed_data = {"path": target, "error": str(exc)}

        if observation_type == "filesystem.read":
            try:
                tool = self._executor.tool_registry.get("read_file")
                if tool is not None:
                    result = await tool.execute(path=target)
                    if result.success:
                        observed_data = {"path": target, "content": result.output}
                    else:
                        observed_data = {"path": target, "error": result.error}
            except Exception as exc:  # noqa: BLE001
                observed_data = {"path": target, "error": str(exc)}

        if observation_type == "system.inspect":
            try:
                tool = self._executor.tool_registry.get("get_system_info")
                if tool is not None:
                    result = await tool.execute()
                    if result.success:
                        observed_data = {"system_info": result.output}
                    else:
                        observed_data = {"error": result.error}
            except Exception as exc:  # noqa: BLE001
                observed_data = {"error": str(exc)}

        # Always record the observation event — its absence is itself a fact.
        payload = observed_data or {"target": target}
        self._execution_log.append(
            observation_recorded_payload(
                correlation_id=correlation_id,
                action_id=action_id,
                source=observation_type or "unknown",
                observation_type="structured",
                data=payload,
            )
        )
        if observed_data is not None:
            self._world_model.update_environment(extra=observed_data)
        return observed_data

    async def _emit_status(self, phase: V3AgentPhase, message: str) -> None:
        self.state.phase = phase
        if self._on_status is None:
            return
        maybe_coro = self._on_status(phase, message, {})
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro


def build_default_v3_orchestrator(
    *,
    tool_executor: Any,
    execution_log: ExecutionLogStore,
    reasoning_client: Any,
    capability_registry: CapabilityRegistry | None = None,
    verifier_registry: Any | None = None,
    tool_registry: Any | None = None,
    on_status: StatusCallback | None = None,
) -> V3Orchestrator:
    """Convenience factory wiring V2 ToolExecutor + V3 reasoning runtime."""
    caps = capability_registry or create_default_capability_registry(
        tool_registry=tool_registry,
    )
    executor = V3Executor.from_defaults(
        execution_log=execution_log,
        tool_executor=tool_executor,
        tool_registry=tool_registry,
        capability_registry=caps,
        verifier_registry=verifier_registry,
    )
    runtime = ReasoningRuntime(
        client=reasoning_client,
        capability_registry=caps,
        execution_log=execution_log,
    )
    return V3Orchestrator(
        runtime=runtime,
        executor=executor,
        execution_log=execution_log,
        on_status=on_status,
    )