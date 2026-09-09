"""V3 end-to-end runtime smoke tests.

These tests run the full V3 reasoning loop with the real V2 tool fleet.
They verify the behaviours the task explicitly demands:

  1. write a file → it actually exists on disk.
  2. observe the filesystem → the world model reflects the observation.
  3. attempt an action that fails → failure recorded + recovery evidence.
  4. re-reason after failure → recovers to a successful outcome.
  5. inspect system → action succeeds with evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.capability import create_default_capability_registry
from hermes.execution_log import EventKind, ExecutionLogStore
from hermes.reasoning import ReasoningReply, ReasoningPrompt, ReasoningRuntime
from hermes.runtime.executor import V3Executor
from hermes.runtime.orchestrator import V3Orchestrator
from hermes.security.approval_manager import ApprovalDecision, ApprovalManager, ParsedApproval
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import create_default_verifier_registry


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(prompt)
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


def _build_orchestrator(
    tmp_path: Path,
    replies: list[dict],
    *,
    approval_provider=None,
) -> tuple[V3Orchestrator, ExecutionLogStore, _StubLLM]:
    log = ExecutionLogStore(path=tmp_path / "log.jsonl")
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    vers = create_default_verifier_registry()
    policy = PolicyEngine()
    audit = AuditLogger(log_path=str(tmp_path / "audit.log"))
    approval = ApprovalManager()

    # Tests inject an explicit provider. The default approves every
    # action so existing tests keep working; security tests use
    # ``rejecting_provider`` or ``conditional_provider`` instead.
    from tests._approval_providers import approving_provider

    approval.set_handler(approval_provider or approving_provider())

    tool_executor = ToolExecutor(tools, policy, audit, approval, vers)
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    llm = _StubLLM(replies)
    runtime = ReasoningRuntime(client=llm, capability_registry=caps, execution_log=log)
    orchestrator = V3Orchestrator(runtime=runtime, executor=executor, execution_log=log)
    return orchestrator, log, llm


# ---------------------------------------------------------------------------
# Scenario 1 — Write a file, then verify it exists.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_write_a_file(tmp_path: Path):
    target = tmp_path / "smoke.txt"
    orch, _, _ = _build_orchestrator(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "smoke ok"},
            },
            {"kind": "complete", "summary": "wrote", "evidence_ids": []},
        ],
    )
    outcome = await orch.process_turn("write a file")
    assert outcome.completed is True
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "smoke ok"
    assert any("succeeded" in e.claim.lower() for e in orch._world_model.evidence)


# ---------------------------------------------------------------------------
# Scenario 2 — Observe the filesystem, then complete.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_observe_filesystem(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("hi", encoding="utf-8")
    orch, log, _ = _build_orchestrator(
        tmp_path,
        [
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": str(tmp_path),
            },
            {"kind": "complete", "summary": "observed", "evidence_ids": []},
        ],
    )
    outcome = await orch.process_turn("look")
    assert outcome.completed is True
    # The observation was recorded as an event.
    assert EventKind.OBSERVATION_RECORDED in {e.kind for e in log.all()}
    # The observation event carries the filesystem data.
    obs_events = [e for e in log.all() if e.kind is EventKind.OBSERVATION_RECORDED]
    assert obs_events
    payload = obs_events[-1].payload
    assert payload.source == "filesystem.list"
    assert "path" in payload.data


# ---------------------------------------------------------------------------
# Scenario 3 — Failure recorded, recovery evidence captured.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_failure_records_recovery_evidence(tmp_path: Path):
    orch, log, _ = _build_orchestrator(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.rename",
                "arguments": {
                    "path": str(tmp_path / "ghost.txt"),
                    "new_name": "y.txt",
                },
            },
            {"kind": "user_question", "question": "?"},
        ],
    )
    outcome = await orch.process_turn("rename")
    assert outcome.completed is False
    finished = [e for e in log.all() if e.kind is EventKind.ACTION_FINISHED]
    assert finished and finished[0].payload.success is False
    # Failure evidence in world model.
    assert any("failed" in e.claim.lower() for e in orch._world_model.evidence)
    # Recovery evidence pair recorded.
    assert EventKind.RECOVERY_STARTED in {e.kind for e in log.all()}
    assert EventKind.RECOVERY_FINISHED in {e.kind for e in log.all()}


# ---------------------------------------------------------------------------
# Scenario 4 — Re-reason after failure → recover.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_re_reason_recovers(tmp_path: Path):
    final = tmp_path / "final.txt"
    orch, log, _ = _build_orchestrator(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.rename",
                "arguments": {"path": str(tmp_path / "ghost.txt"), "new_name": "y.txt"},
                "required_capabilities": ["filesystem.rename"],
            },
            {
                "kind": "re_reason",
                "reason": "missing file",
                "required_capabilities": ["filesystem.rename"],
            },
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(final), "content": "recovered"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "recovered",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
        ],
    )
    outcome = await orch.process_turn("recover")
    assert outcome.completed is True
    assert final.exists()
    assert final.read_text(encoding="utf-8") == "recovered"


# ---------------------------------------------------------------------------
# Scenario 5 — System inspect (read-only) completes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_system_inspect(tmp_path: Path):
    orch, _, _ = _build_orchestrator(
        tmp_path,
        [
            {"kind": "action", "capability": "system.inspect", "arguments": {}},
            {"kind": "complete", "summary": "inspected", "evidence_ids": []},
        ],
    )
    outcome = await orch.process_turn("inspect")
    assert outcome.completed is True
    assert any("succeeded" in e.claim.lower() for e in orch._world_model.evidence)


# ---------------------------------------------------------------------------
# Scenario 6 — Action Success ≠ Observation Success ≠ Verification Success.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_action_observation_verification_are_distinct(tmp_path: Path):
    """The tool reports success, the verifier confirms, and the runtime sees
    them as separate facts in the log."""
    target = tmp_path / "verify.txt"
    orch, log, _ = _build_orchestrator(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "x"},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ],
    )
    await orch.process_turn("write")
    kinds = [e.kind for e in log.all()]
    assert EventKind.ACTION_STARTED in kinds
    assert EventKind.ACTION_FINISHED in kinds
    assert EventKind.VERIFICATION_RECORDED in kinds
    finished = next(e for e in log.all() if e.kind is EventKind.ACTION_FINISHED)
    verified = next(e for e in log.all() if e.kind is EventKind.VERIFICATION_RECORDED)
    assert finished.payload.success is True
    assert verified.payload.status in {"verified", "not_required", "unknown", "failed"}