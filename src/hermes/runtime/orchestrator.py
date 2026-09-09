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

UI, voice and CLI share the public ``process_message`` API. New runtime code
should use the typed ``process_turn`` method.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
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
)
from hermes.reasoning import Decision, DecisionKind, ReasoningRuntime
from hermes.reasoning.decision import Action
from hermes.runtime.executor import ActionExecutionOutcome, V3Executor
from hermes.world_model import (
    EvidenceRecord,
    EvidenceSource,
    ReferenceBinding,
    ReferenceKind,
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
    CANCELLED = "cancelled"


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
        turn_timeout_seconds: float = 300.0,
        memory: Any = None,
        session_store: Any = None,
        session_id_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._runtime = runtime
        self._executor = executor
        self._execution_log = execution_log
        self._world_model = world_model or WorldModel()
        self._max_iterations = max_iterations
        if turn_timeout_seconds <= 0:
            raise ValueError("turn_timeout_seconds must be positive")
        self._turn_timeout_seconds = float(turn_timeout_seconds)
        self._active_turn_task: asyncio.Task[TurnOutcome] | None = None
        self._turn_action_outcomes: list[ActionExecutionOutcome] = []
        self._turn_generation = 0
        self._memory = memory
        self._session_store = session_store
        self._loaded_session_id = ""
        self._session_id_sink = session_id_sink
        self._base_environment = self._world_model.environment.to_dict()
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
        self._on_approval_required_callback: Callable[[Any], Awaitable[str]] | None = None

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
        self._loaded_session_id = state.session_id
        if self._memory is not None and hasattr(self._memory, "activate_session"):
            self._memory.activate_session(state.session_id)
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

    @property
    def _on_approval_required(self) -> Callable[[Any], Awaitable[str]] | None:
        return self._on_approval_required_callback

    @_on_approval_required.setter
    def _on_approval_required(
        self, value: Callable[[Any], Awaitable[str]] | None
    ) -> None:
        """Bridge the legacy UI callback into V3's approval manager.

        The UI returns natural-language text (``evet`` / ``iptal``), while
        V3's security boundary consumes a typed ``ParsedApproval``.  Keeping
        the adaptation here lets UI callers retain their stable surface
        without bypassing ApprovalManager or ToolExecutor.
        """
        self._on_approval_required_callback = value
        if value is None:
            return

        from hermes.security.approval_manager import NaturalLanguageApprovalParser

        async def _handler(pending: Any) -> Any:
            answer = await value(pending.request)
            return NaturalLanguageApprovalParser.parse(
                answer,
                total_steps=len(pending.request.plan_steps) or 1,
            )

        self._executor.approval_manager.set_handler(_handler)

    @property
    def tool_executor(self) -> Any:
        """Underlying guarded executor used by localhost RPC compatibility."""
        return self._executor.tool_executor

    async def initialize_session(self) -> str:
        """V2-compatible entry point: produce a session id.

        V3 does not yet maintain a long-lived server session of its own;
        the id is local and used as the correlation anchor for the next
        ``process_message`` call. The real server session is opened
        lazily by the reasoning client on the first LLM call.
        """
        if not self.state.current_session_id:
            from hermes.runtime.session import new_session_id

            self.state.current_session_id = new_session_id()
            self.state.session_notice = None
            if self._session_id_sink is not None:
                self._session_id_sink(self.state.current_session_id)
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
            if self._session_id_sink is not None:
                self._session_id_sink(session_id)
        outcome = await self.process_turn(message)
        return outcome.reply

    # ---- primary V3 surface --------------------------------------------

    async def process_turn(self, message: str) -> TurnOutcome:
        """Run one bounded turn and own cancellation of its actual task."""
        self._turn_generation += 1
        generation = self._turn_generation
        previous = self._active_turn_task
        if previous is not None and not previous.done():
            previous.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await previous
        await self._prepare_session()

        task = asyncio.create_task(self._process_turn_inner(message))
        self._active_turn_task = task
        try:
            outcome = await asyncio.wait_for(
                task, timeout=self._turn_timeout_seconds
            )
            self._finalize_turn_memory(message, outcome)
            return outcome
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if generation == self._turn_generation:
                self._world_model.set_status("failed")
                self._world_model.set_uncertainty(("turn_timeout",))
                await self._emit_status(V3AgentPhase.FAILED, "Turn timed out")
            outcome = TurnOutcome(
                reply="The operation timed out before it could be verified.",
                decision=self.state.last_decision,
                iterations=self.state.iteration,
                completed=False,
            )
            self._finalize_turn_memory(message, outcome)
            return outcome
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if generation == self._turn_generation:
                self._world_model.set_status("abandoned")
                self._world_model.set_uncertainty(("turn_cancelled",))
                await self._emit_status(V3AgentPhase.CANCELLED, "Turn cancelled")
                self._finalize_turn_memory(
                    message,
                    TurnOutcome(
                        reply="Turn cancelled.",
                        decision=self.state.last_decision,
                        iterations=self.state.iteration,
                        completed=False,
                    ),
                )
            raise
        finally:
            if self._active_turn_task is task:
                self._active_turn_task = None

    async def cancel_active_turn(self) -> bool:
        """Cancel and join the active turn so no execution remains orphaned."""
        task = self._active_turn_task
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    async def _prepare_session(self) -> None:
        session_id = self.state.current_session_id or await self.initialize_session()
        if self._loaded_session_id == session_id:
            return
        if self._memory is not None and hasattr(self._memory, "activate_session"):
            self._memory.activate_session(session_id)

        restored = (
            self._session_store.load(session_id)
            if self._session_store is not None
            else None
        )
        if restored is not None:
            await self.resume_session(restored)
        else:
            self._world_model.reset()
            environment = dict(self._base_environment)
            extra = dict(environment.pop("extra", {}) or {})
            environment.pop("as_of", None)
            self._world_model.update_environment(**environment)
            if extra:
                self._world_model.update_environment(**extra)
            self.state = V3AgentState(current_session_id=session_id)
            self._loaded_session_id = session_id

    def _finalize_turn_memory(
        self,
        user_message: str,
        outcome: TurnOutcome,
    ) -> None:
        if self._memory is not None and hasattr(self._memory, "record_exchange"):
            self._memory.record_exchange(user_message, outcome.reply)
            if outcome.completed and hasattr(self._memory, "record_episode"):
                capabilities = tuple(
                    action.capability for action in self._turn_action_outcomes
                )
                self._memory.record_episode(
                    summary=outcome.reply,
                    capabilities=capabilities,
                )

        if self._session_store is not None:
            from hermes.runtime.session import snapshot_from_world_model

            session_id = self.state.current_session_id
            if not session_id:
                raise RuntimeError("Cannot persist a turn without session id")
            snapshot = snapshot_from_world_model(
                self._world_model,
                session_id=session_id,
                objective=self._world_model.task.objective,
                correlation_id=self.state.correlation_id,
                last_summary=outcome.reply,
                completed=outcome.completed,
            )
            self._session_store.save(snapshot)

    def _apply_memory_facts(self, decision: Decision) -> None:
        if not decision.memory_facts:
            return
        if self._memory is None or not hasattr(self._memory, "long_term"):
            raise ValueError("Persistent long-term memory is unavailable")
        provenance = f"explicit_user:{self.state.current_session_id}"
        self._memory.long_term.remember_many(
            tuple((fact.key, fact.value) for fact in decision.memory_facts),
            provenance=provenance,
        )

    async def _process_turn_inner(self, message: str) -> TurnOutcome:
        """Run one user message through the reasoning loop."""
        correlation_id = f"turn_{uuid.uuid4().hex[:12]}"
        current_session_id = self.state.current_session_id
        self.state = V3AgentState(
            correlation_id=correlation_id,
            current_session_id=current_session_id,
        )
        self._world_model.set_objective(message)
        self._turn_action_outcomes = []

        iterations = 0
        last_decision: Decision | None = None
        reply = ""
        completed = False
        terminated = False
        re_reason_reason = ""

        while iterations < self._max_iterations:
            iterations += 1
            self.state.iteration = iterations
            await self._emit_status(
                V3AgentPhase.REASONING, f"Reasoning (iteration {iterations})"
            )

            try:
                if re_reason_reason:
                    decision = await self._runtime.re_reason(
                        user_message=message,
                        world_model=self._world_model,
                        correlation_id=correlation_id,
                        reason=re_reason_reason,
                    )
                    re_reason_reason = ""
                else:
                    decision = await self._runtime.reason(
                        user_message=message,
                        world_model=self._world_model,
                        correlation_id=correlation_id,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._world_model.set_status("failed")
                self._world_model.set_uncertainty((f"reasoning_failed:{exc}",))
                await self._emit_status(V3AgentPhase.FAILED, "Reasoning failed")
                return TurnOutcome(
                    reply=f"Reasoning failed: {exc}",
                    decision=None,
                    iterations=iterations,
                    completed=False,
                )

            last_decision = decision
            self.state.last_decision = decision
            try:
                self._apply_memory_facts(decision)
            except (OSError, ValueError) as exc:
                re_reason_reason = f"memory persistence rejected: {exc}"
                self._world_model.set_uncertainty((re_reason_reason,))
                continue
            if decision.required_capabilities:
                self._world_model.reconcile_requirements(
                    decision.required_capabilities
                )

            if decision.kind is DecisionKind.ACTION:
                self._world_model.set_status("in_progress")
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
                    re_reason_reason = f"capability lookup failed: {exc}"
                    continue
                self._absorb_execution(outcome)
                self._turn_action_outcomes.append(outcome)
                # If the action failed, record recovery evidence so the
                # log captures the structural fact that a recovery attempt
                # is pending — the runtime's next decision will determine
                # what the recovery actually does.
                if outcome is not None and (
                    not outcome.action_success
                    or outcome.verification_status in ("failed", "unknown")
                ):
                    self._record_recovery_pending(
                        correlation_id=correlation_id,
                        action_id=outcome.action_id,
                        reason=(
                            outcome.error
                            or f"verification_{outcome.verification_status}"
                        ),
                    )
                    re_reason_reason = (
                        outcome.error
                        or f"verification was {outcome.verification_status}"
                    )
                continue

            if decision.kind is DecisionKind.OBSERVATION_REQUEST:
                self._world_model.set_status("verifying")
                await self._emit_status(V3AgentPhase.OBSERVING, decision.observation_request.observation_type)
                observation = await self._run_observation(
                    decision.observation_request, correlation_id
                )
                re_reason_reason = (
                    "observation recorded"
                    if observation is not None
                    else "requested observation was unavailable"
                )
                continue

            if decision.kind is DecisionKind.USER_QUESTION:
                await self._emit_status(V3AgentPhase.AWAITING_USER, decision.user_question.question)
                reply = decision.user_question.question
                completed = False
                terminated = True
                break

            if decision.kind is DecisionKind.COMPLETE:
                reply = decision.complete.summary if decision.complete else "Done."
                completion_error = self._completion_error(decision)
                if completion_error:
                    self._world_model.set_uncertainty((completion_error,))
                    re_reason_reason = completion_error
                    continue
                self._world_model.set_status("complete")
                await self._emit_status(V3AgentPhase.COMPLETED, reply)
                completed = True
                terminated = True
                break

            if decision.kind is DecisionKind.RE_REASON:
                re_reason_reason = (
                    decision.re_reason.reason
                    if decision.re_reason is not None
                    else "runtime requested re-reasoning"
                )
                continue

        if not terminated and iterations >= self._max_iterations:
            self._world_model.set_status("failed")
            self._world_model.set_uncertainty(("iteration_limit_reached",))
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
            data={
                "action_id": outcome.action_id,
                "tool": outcome.selection.tool_name if outcome.selection else "",
                "arguments": (
                    dict(outcome.selection.arguments) if outcome.selection else {}
                ),
                "output": outcome.payload.output if outcome.payload else None,
                "error": outcome.payload.error if outcome.payload else outcome.error,
            },
            correlation_id=self.state.correlation_id,
        )
        self._world_model.record_evidence(evidence)

        observation_evidence: EvidenceRecord | None = None
        if outcome.observation_envelope is not None:
            observation_payload = outcome.observation_envelope.payload
            observation_evidence = EvidenceRecord.make(
                source=EvidenceSource.OBSERVATION,
                capability=outcome.capability,
                claim="post-action observation recorded",
                data={
                    "action_id": outcome.action_id,
                    "source": getattr(observation_payload, "source", ""),
                    "observation_type": getattr(
                        observation_payload, "observation_type", ""
                    ),
                    "data": dict(getattr(observation_payload, "data", {}) or {}),
                },
                correlation_id=self.state.correlation_id,
            )
            self._world_model.record_evidence(observation_evidence)
            self._world_model.update_environment(
                last_observation=observation_evidence.data
            )

        if outcome.verification_envelope is not None:
            envelope = outcome.verification_envelope
            payload = envelope.payload
            verifier_status = getattr(payload, "status", "unknown")
            verified_evidence = EvidenceRecord.make(
                source=EvidenceSource.VERIFIER,
                capability=outcome.capability,
                claim=f"verifier: {verifier_status}",
                data={
                    "action_id": outcome.action_id,
                    "tool": (
                        outcome.selection.tool_name if outcome.selection else ""
                    ),
                    "method": getattr(payload, "method", ""),
                    "status": verifier_status,
                    "details": dict(getattr(payload, "details", {}) or {}),
                },
                correlation_id=self.state.correlation_id,
            )
            self._world_model.record_evidence(verified_evidence)
            if verifier_status == "verified" and success:
                self._mark_tool_requirements_satisfied(
                    outcome, verified_evidence.evidence_id
                )
                self._bind_artifact_references(
                    outcome, verified_evidence.evidence_id
                )
            elif (
                verifier_status == "not_required"
                and success
                and observation_evidence is not None
            ):
                self._mark_tool_requirements_satisfied(
                    outcome, observation_evidence.evidence_id
                )
            else:
                self._world_model.set_uncertainty(
                    (f"verification_{verifier_status}:{outcome.action_id}",)
                )

    def _mark_tool_requirements_satisfied(
        self,
        outcome: ActionExecutionOutcome,
        evidence_id: str,
    ) -> None:
        capabilities = {outcome.capability}
        if outcome.selection is not None:
            capabilities.update(
                self._executor.capability_registry.capabilities_for_tool(
                    outcome.selection.tool_name
                )
            )
        for capability in capabilities:
            self._world_model.mark_requirement_satisfied(capability, evidence_id)

    def _bind_artifact_references(
        self,
        outcome: ActionExecutionOutcome,
        evidence_id: str,
    ) -> None:
        if outcome.payload is None or outcome.selection is None:
            return
        if outcome.selection.tool_name == "delete_path":
            return

        from pathlib import Path

        from hermes.tools.capabilities import extract_structured_value

        contract = self._executor.capability_registry.get(outcome.capability)
        fields = contract.observation_value if contract is not None else ()
        path_value = None
        for field_name in fields:
            if field_name not in ("path", "destination"):
                continue
            path_value = extract_structured_value(outcome.payload.output, field_name)
            if path_value:
                break
        if not isinstance(path_value, str) or not path_value.strip():
            return

        path = Path(path_value).expanduser()
        if path.is_dir() or outcome.selection.tool_name == "create_folder":
            kind = ReferenceKind.FOLDER
            specific_key = "last_folder"
        else:
            kind = ReferenceKind.FILE
            specific_key = "last_file"
        binding = ReferenceBinding.make(
            key="last_artifact",
            kind=kind,
            value=str(path),
            provenance=evidence_id,
            extra={
                "action_id": outcome.action_id,
                "capability": outcome.capability,
                "tool": outcome.selection.tool_name,
                "verification_status": outcome.verification_status,
            },
        )
        self._world_model.bind_reference("last_artifact", binding)
        self._world_model.bind_reference(
            specific_key,
            ReferenceBinding.make(
                key=specific_key,
                kind=kind,
                value=str(path),
                provenance=evidence_id,
                extra=dict(binding.extra),
            ),
        )
        self._world_model.bind_reference(
            f"action:{outcome.action_id}",
            ReferenceBinding.make(
                key=f"action:{outcome.action_id}",
                kind=kind,
                value=str(path),
                provenance=evidence_id,
                extra=dict(binding.extra),
            ),
        )

    def _completion_error(self, decision: Decision) -> str | None:
        task = self._world_model.task
        if task.requirements and not task.all_satisfied:
            missing = ", ".join(
                requirement.capability for requirement in task.unsatisfied
            )
            return f"required capabilities are not verified: {missing}"

        unresolved = [
            outcome
            for outcome in self._turn_action_outcomes
            if not outcome.action_success
            or outcome.verification_status in ("unknown", "failed")
        ]
        if unresolved and not decision.required_capabilities:
            return (
                "completion cannot supersede unresolved actions without "
                "a reconciled requirement set"
            )

        complete = decision.complete
        if complete is None:
            return "completion payload is missing"
        requirement_evidence = {
            evidence_id
            for requirement in task.requirements
            for evidence_id in requirement.evidence
        }
        for evidence_id in complete.evidence_ids:
            evidence = self._world_model.evidence_by_id(evidence_id)
            if evidence is None:
                return f"completion cites unknown evidence: {evidence_id}"
            if task.requirements and evidence_id not in requirement_evidence:
                return f"completion evidence is unrelated to current requirements: {evidence_id}"
            if (
                not task.requirements
                and evidence.correlation_id != self.state.correlation_id
            ):
                return f"completion evidence is stale for this turn: {evidence_id}"
            if evidence.source not in (
                EvidenceSource.VERIFIER,
                EvidenceSource.OBSERVATION,
            ):
                return f"completion evidence is not independently observed: {evidence_id}"
            if (
                evidence.source is EvidenceSource.VERIFIER
                and evidence.data.get("status") != "verified"
            ):
                return f"completion cites unverified evidence: {evidence_id}"
        return None

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
        """Execute a read-only observation through the guarded V3 executor."""
        observation_type = str(
            getattr(request, "observation_type", "") or ""
        ).strip()
        target = str(getattr(request, "target", "") or "").strip()
        parameters = dict(getattr(request, "parameters", {}) or {})
        contract = self._executor.capability_registry.get(observation_type)
        if contract is None or contract.risk_level.value != "read_only":
            evidence = EvidenceRecord.make(
                source=EvidenceSource.OBSERVATION,
                capability=observation_type,
                claim="observation capability unavailable or not read-only",
                data={"target": target},
                confidence=0.0,
                correlation_id=correlation_id,
            )
            self._world_model.record_evidence(evidence)
            return None

        if target:
            properties = contract.input_schema.get("properties") or {}
            if "target" in properties:
                parameters.setdefault("target", target)
            elif "path" in properties:
                parameters.setdefault("path", target)
            else:
                required = contract.input_schema.get("required") or ()
                if len(required) == 1:
                    parameters.setdefault(str(required[0]), target)

        try:
            outcome = await self._executor.execute(
                action=Action(
                    capability=observation_type,
                    arguments=parameters,
                    rationale=str(getattr(request, "rationale", "") or ""),
                ),
                correlation_id=correlation_id,
                action_id=f"obs_{uuid.uuid4().hex[:12]}",
            )
        except LookupError as exc:
            evidence = EvidenceRecord.make(
                source=EvidenceSource.OBSERVATION,
                capability=observation_type,
                claim=f"observation failed: {exc}",
                data={"target": target},
                confidence=0.0,
                correlation_id=correlation_id,
            )
            self._world_model.record_evidence(evidence)
            return None

        self._absorb_execution(outcome)
        if outcome.payload is None or not outcome.action_success:
            return None
        observed_data = (
            dict(outcome.payload.output)
            if isinstance(outcome.payload.output, dict)
            else {"value": outcome.payload.output}
        )
        self._world_model.update_environment(last_observation=observed_data)
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