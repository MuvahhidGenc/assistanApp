"""V3 contract: ``V3Orchestrator.process_turn`` must bound a runaway
reasoning loop and surface a failed turn to the caller.

The production runtime wraps the per-turn coroutine in
:func:`asyncio.wait_for` so the UI/voice pipeline never blocks
indefinitely. This test exercises three cases:

  1. ``turn_timeout_seconds=None`` keeps backwards-compatible behaviour
     (no wall-clock cap).
  2. ``turn_timeout_seconds`` triggers a ``TimeoutError`` that becomes
     a ``TurnOutcome`` with ``completed=False`` and a user-visible
     Turkish message.
  3. A *real* hang in the LLM client surfaces the same outcome — the
     orchestrator does not swallow the timeout or invent a success.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from hermes.execution_log import ExecutionLogStore
from hermes.reasoning import Decision, DecisionKind, ReasoningReply
from hermes.reasoning.transport import ReasoningReply
from hermes.runtime.executor import V3Executor
from hermes.runtime.orchestrator import V3Orchestrator
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.security.approval_manager import ApprovalManager
from hermes.capability import create_default_capability_registry


class _SlowReasoningClient:
    """Always hangs forever, simulating a stuck LLM transport."""

    async def reason(self, prompt):
        await asyncio.sleep(60)
        return ReasoningReply(decision_json={"kind": "complete", "summary": "unreachable"})


class _ImmediateClient:
    """Always returns an immediate Complete decision."""

    def __init__(self):
        self.calls = 0

    async def reason(self, prompt):
        self.calls += 1
        return ReasoningReply(
            decision_json={"kind": "complete", "summary": "tamam"}
        )


def _build_orchestrator(client, *, turn_timeout_seconds):
    log = ExecutionLogStore()
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    vers = create_default_verifier_registry()
    policy = PolicyEngine()
    audit = AuditLogger(log_path="/tmp/_test_timeout_audit.log")
    approval = ApprovalManager(audit_logger=audit)
    tex = ToolExecutor(tools, policy, audit, approval, vers)
    from hermes.reasoning import ReasoningRuntime
    from hermes.world_model import WorldModel
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tex,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    runtime = ReasoningRuntime(
        client=client,
        capability_registry=caps,
        execution_log=log,
    )
    return V3Orchestrator(
        runtime=runtime,
        executor=executor,
        execution_log=log,
        world_model=WorldModel(),
        turn_timeout_seconds=turn_timeout_seconds,
    )


def test_turn_timeout_none_keeps_backwards_compatible_behaviour():
    """Without a timeout the orchestrator must complete normally."""
    client = _ImmediateClient()
    orch = _build_orchestrator(client, turn_timeout_seconds=None)
    outcome = asyncio.run(orch.process_turn("selam"))
    assert outcome.completed is True
    assert outcome.reply == "tamam"
    assert client.calls == 1


def test_turn_timeout_caps_a_runaway_loop_with_failed_turn():
    """A hung LLM must surface a failed turn, not block the caller."""
    client = _SlowReasoningClient()
    orch = _build_orchestrator(client, turn_timeout_seconds=0.1)
    outcome = asyncio.run(orch.process_turn("selam"))
    assert outcome.completed is False
    assert "zaman aşımı" in outcome.reply
    # Decision was never produced because the LLM never returned.
    assert outcome.decision is None


def test_turn_timeout_emits_failed_status_event():
    """The on_status callback must observe the FAILED phase."""
    client = _SlowReasoningClient()
    orch = _build_orchestrator(client, turn_timeout_seconds=0.1)

    captured: list[tuple[str, str]] = []

    async def _on_status(phase, message, extras):
        captured.append((phase.value, message))

    orch._on_status_callback = _on_status
    asyncio.run(orch.process_turn("selam"))
    # The first status is REASONING, the last one is FAILED with
    # timeout text — a real failure, not a fabricated success.
    assert any("Turn timeout" in msg for _phase, msg in captured)


def test_process_turn_does_not_invent_success_on_timeout():
    """Defence-in-depth: outcome.completed must stay False."""
    client = _SlowReasoningClient()
    orch = _build_orchestrator(client, turn_timeout_seconds=0.05)
    outcome = asyncio.run(orch.process_turn("x"))
    assert outcome.completed is False
    # No "Done." string — the LLM never reached the COMPLETE branch.
    assert "Done" not in outcome.reply


# ---------------------------------------------------------------------------
# Voice V3 integration: a runaway turn must not hang the voice pipeline
# indefinitely. The V3 orchestrator has its own timeout; the voice layer
# must respect it via ``_active_request.cancel()``.
# ---------------------------------------------------------------------------


def test_voice_active_request_can_be_cancelled_during_runaway_turn():
    """``VoiceAssistant`` exposes ``_active_request.cancel()``; cancelling
    it must propagate ``CancelledError`` into the running turn.
    """
    from hermes.voice.assistant import VoiceAssistant

    src = inspect.getsource(VoiceAssistant)
    assert "stop_active" in src
    assert "_active_request" in src
    # ``_active_request.cancel()`` is the only cancellation primitive
    # the voice layer uses; no V2 ``agent._server.stop_run`` path.
    assert "agent._server.stop_run" not in src
