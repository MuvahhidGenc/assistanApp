"""V3 orchestrator tests.

These tests exercise the V3 runtime loop end-to-end with real V2 tools —
filesystem.list, filesystem.read, filesystem.write, etc. They use a stub
LLM client that returns scripted decisions so the test is deterministic.

Every test verifies that the orchestrator:

  * owns the loop (multiple iterations when needed),
  * records execution events in the Execution Log,
  * surfaces tool failures to the runtime (does not paper over them),
  * stops on COMPLETE with verified evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermes.capability import create_default_capability_registry
from hermes.execution_log import (
    EventKind,
    ExecutionLogStore,
)
from hermes.reasoning import LlmReasoningClient, ReasoningPrompt, ReasoningReply
from hermes.runtime import (
    V3AgentPhase,
    V3Executor,
    V3Orchestrator,
)
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.world_model import WorldModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubClient:
    """Deterministic stand-in for the server-side LLM."""

    def __init__(self, replies: list[dict[str, Any]] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[ReasoningPrompt] = []

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(prompt)
        if not self.replies:
            raise AssertionError("Stub client ran out of replies")
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


def _build_orchestrator(
    tmp_path: Path,
    *,
    replies: list[dict[str, Any]],
    tool_registry=None,
    capability_registry=None,
    policy_engine=None,
    approval_manager=None,
    audit_logger=None,
    approval_provider=None,
) -> tuple[V3Orchestrator, ExecutionLogStore, _StubClient]:
    log = ExecutionLogStore(path=tmp_path / "log.jsonl")
    tools = tool_registry or create_default_registry()
    caps = capability_registry or create_default_capability_registry(tools)
    vers = create_default_verifier_registry()
    policy = policy_engine or PolicyEngine()
    audit = audit_logger or AuditLogger(log_path=str(tmp_path / "audit.log"))
    approval = approval_manager or ApprovalManager()
    # Tests inject an explicit approval provider — production would do the
    # same. The default test provider approves every action, mirroring the
    # V2 ``auto_approve_local_tools=True`` opt-in knob.
    from tests._approval_providers import approving_provider

    provider = approval_provider or approving_provider()
    approval.set_handler(provider)
    tool_executor = ToolExecutor(tools, policy, audit, approval, vers)
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    client = _StubClient(replies=replies)
    from hermes.reasoning import ReasoningRuntime

    runtime = ReasoningRuntime(
        client=client,
        capability_registry=caps,
        execution_log=log,
    )
    orchestrator = V3Orchestrator(
        runtime=runtime,
        executor=executor,
        execution_log=log,
    )
    return orchestrator, log, client


# ---------------------------------------------------------------------------
# Loop shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_runs_one_iteration_for_complete(tmp_path: Path):
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[{"kind": "complete", "summary": "done", "evidence_ids": ["ev_a"]}],
    )
    outcome = await orchestrator.process_turn("hi")
    assert outcome.completed is True
    assert outcome.iterations == 1
    assert outcome.reply == "done"
    assert orchestrator.state.phase is V3AgentPhase.COMPLETED


@pytest.mark.asyncio
async def test_orchestrator_chains_action_then_complete(tmp_path: Path):
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.list",
                "arguments": {"path": str(tmp_path)},
},
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ],
    )
    outcome = await orchestrator.process_turn("list directory")
    assert outcome.completed is True
    assert outcome.iterations == 2
    # The execution log must contain ACTION_STARTED + ACTION_FINISHED + COMPLETE-related evidence.
    kinds = [e.kind for e in log.all()]
    assert EventKind.ACTION_STARTED in kinds
    assert EventKind.ACTION_FINISHED in kinds


@pytest.mark.asyncio
async def test_orchestrator_chains_observation_then_complete(tmp_path: Path):
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": str(tmp_path),
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ],
    )
    outcome = await orchestrator.process_turn("look")
    assert outcome.completed is True
    assert outcome.iterations == 2


@pytest.mark.asyncio
async def test_orchestrator_surfaces_user_question_without_completing(tmp_path: Path):
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {"kind": "user_question", "question": "which file?", "options": ["a", "b"]},
        ],
    )
    outcome = await orchestrator.process_turn("open it")
    assert outcome.completed is False
    assert outcome.reply == "which file?"
    assert orchestrator.state.phase is V3AgentPhase.AWAITING_USER


# ---------------------------------------------------------------------------
# Re-reasoning & failure surfacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_reasons_again_after_failed_action(tmp_path: Path):
    """When a tool reports failure, the orchestrator must surface that fact
    to the runtime — never silently succeed. The next iteration must see
    the failure evidence."""
    # Force a real tool failure by renaming a non-existent path; on both
    # Linux and Windows this returns a tool-reported `success=False` so
    # the test's assertion is meaningful cross-platform.
    source = tmp_path / "does_not_exist.txt"
    target = tmp_path / "y.txt"
    orchestrator, log, client = _build_orchestrator(
        tmp_path,
        replies=[
            # First decision: rename a missing path — must fail.
            {
                "kind": "action",
                "capability": "filesystem.rename",
                "arguments": {"path": str(source), "new_name": target.name},
            },
            # Second decision: re-reason.
            {"kind": "re_reason", "reason": "rename failed"},
            # Third decision: succeed via filesystem.write.
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "hi"},
            },
            {"kind": "complete", "summary": "wrote", "evidence_ids": []},
        ],
    )
    outcome = await orchestrator.process_turn("rename it")
    assert outcome.completed is True
    # The runtime's last call must see the failed rename in recent_events.
    last_call = client.calls[-1]
    assert any(
        envelope["kind"] == EventKind.ACTION_FINISHED.value
        and envelope["payload"].get("success") is False
        for envelope in last_call.recent_events
    )


@pytest.mark.asyncio
async def test_orchestrator_records_action_and_verification_events(tmp_path: Path):
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "hello.txt"), "content": "hi"},
            },
            {"kind": "complete", "summary": "done", "evidence_ids": []},
        ],
    )
    await orchestrator.process_turn("write a file")
    kinds = [e.kind for e in log.all()]
    assert EventKind.ACTION_STARTED in kinds
    assert EventKind.ACTION_FINISHED in kinds
    assert EventKind.VERIFICATION_RECORDED in kinds


@pytest.mark.asyncio
async def test_orchestrator_does_not_silently_succeed_when_tool_fails(tmp_path: Path):
    """The orchestrator must propagate failure into the world model."""
    # `rename_path` requires the source to exist; using a non-existent
    # source forces a tool-reported failure so the assertion below is
    # meaningful across Linux and Windows.
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.rename",
                "arguments": {
                    "path": str(tmp_path / "does_not_exist.txt"),
                    "new_name": "y.txt",
                },
            },
            {"kind": "complete", "summary": "?", "evidence_ids": []},
        ],
    )
    outcome = await orchestrator.process_turn("rename it")
    finished = [e for e in log.all() if e.kind is EventKind.ACTION_FINISHED]
    assert len(finished) == 1
    assert finished[0].payload.success is False
    # The world model carries a tool-report evidence claim of failure.
    evidence_claims = [e.claim for e in orchestrator._world_model.evidence]
    assert any("failed" in claim.lower() for claim in evidence_claims)


# ---------------------------------------------------------------------------
# Goal completion only with verified evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_records_evidence_per_capability(tmp_path: Path):
    orchestrator, _, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "out.txt"), "content": "hi"},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": ["ev_x"]},
        ],
    )
    await orchestrator.process_turn("write")
    # Two evidence records: one tool report, one verifier.
    assert len(orchestrator._world_model.evidence) == 2


@pytest.mark.asyncio
async def test_orchestrator_records_action_success_and_verification_separately(tmp_path: Path):
    """Action Success != Verification Success invariant."""
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "x.txt"), "content": "hi"},
            },
            {"kind": "complete", "summary": "done", "evidence_ids": []},
        ],
    )
    await orchestrator.process_turn("write")
    finished = next(e for e in log.all() if e.kind is EventKind.ACTION_FINISHED)
    verified = next(e for e in log.all() if e.kind is EventKind.VERIFICATION_RECORDED)
    # Both events exist; their meanings are independent.
    assert finished.payload.success is True
    assert verified.payload.status in {"verified", "unknown", "not_required", "failed"}


# ---------------------------------------------------------------------------
# Real runtime smoke: write a file, then re-read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_writes_and_verifies_real_file(tmp_path: Path):
    """End-to-end: write a file, observe it, complete. No mocks at the tool layer."""
    target = tmp_path / "smoke.txt"
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "merhaba dunya"},
            },
            {
                "kind": "observation_request",
                "observation_type": "filesystem.read",
                "target": str(target),
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ],
    )
    outcome = await orchestrator.process_turn("write a file")
    assert outcome.completed is True
    # The file must really exist on disk.
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "merhaba dunya"
    # The orchestrator recorded the evidence in the world model.
    assert any("succeeded" in e.claim.lower() for e in orchestrator._world_model.evidence)


# ---------------------------------------------------------------------------
# Anti-routing invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrator_does_not_inspect_user_language(tmp_path: Path):
    orchestrator, log, client = _build_orchestrator(
        tmp_path,
        replies=[{"kind": "user_question", "question": "?"}],
    )
    await orchestrator.process_turn("dosyayı aç lütfen")
    # The runtime saw the message verbatim — no keyword manipulation.
    assert client.calls
    assert client.calls[0].user_message == "dosyayı aç lütfen"


@pytest.mark.asyncio
async def test_orchestrator_records_recovery_events_on_failure(tmp_path: Path):
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.rename",
                "arguments": {"path": str(tmp_path / "missing.txt"), "new_name": "y.txt"},
            },
            {"kind": "user_question", "question": "what next?"},
        ],
    )
    await orchestrator.process_turn("rename")
    kinds = [e.kind for e in log.all()]
    assert EventKind.RECOVERY_STARTED in kinds
    assert EventKind.RECOVERY_FINISHED in kinds


@pytest.mark.asyncio
async def test_orchestrator_no_recovery_events_on_success(tmp_path: Path):
    orchestrator, log, _ = _build_orchestrator(
        tmp_path,
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "ok.txt"), "content": "hi"},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ],
    )
    await orchestrator.process_turn("write")
    kinds = [e.kind for e in log.all()]
    assert EventKind.RECOVERY_STARTED not in kinds
    assert EventKind.RECOVERY_FINISHED not in kinds


def test_orchestrator_does_not_import_legacy_semantic_routing():
    import hermes.runtime.orchestrator as mod

    text = open(mod.__file__, encoding="utf-8").read()
    forbidden = (
        "agent.local_intent",
        "agent.goal_router",
        "agent.task_planner",
        "agent.goal_parser",
        "agent.plan_models",
        "agent.plan_analysis",
        "agent.conversation_flow",
        "agent.mission_flow",
        "agent.risk_gate",
        "agent.scope_resolver",
        "intent.understanding",
        "intent.router",
    )
    for token in forbidden:
        assert token not in text, f"runtime/orchestrator.py must not reference {token!r}"


def test_orchestrator_does_not_call_policy_or_approval_directly():
    """Security is enforced through ToolExecutor — not the orchestrator."""
    import hermes.runtime.orchestrator as mod

    text = open(mod.__file__, encoding="utf-8").read()
    for token in ("PolicyEngine(", "ApprovalManager(", "evaluate_policy("):
        assert token not in text, f"orchestrator must not call {token!r} directly"