"""V3 session persistence + resume tests.

A long-running task should be able to:
  1. save its current state mid-execution,
  2. (optionally) restart the process,
  3. resume from the saved state with the same world model.

The persistence layer must be atomic (a crashed save never leaves a
half-written file) and versioned.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hermes.execution_log import ExecutionLogStore
from hermes.runtime.executor import V3Executor
from hermes.runtime.orchestrator import V3Orchestrator
from hermes.runtime.session import (
    SessionState,
    SessionStore,
    apply_to_world_model,
    new_session_id,
    snapshot_from_world_model,
)
from hermes.security.approval_manager import ApprovalDecision, ParsedApproval
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.capability import create_default_capability_registry
from hermes.reasoning import ReasoningRuntime
from hermes.world_model import WorldModel
from tests._approval_providers import approving_provider


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []

    async def reason(self, prompt):
        from hermes.reasoning import ReasoningReply
        self.calls.append(prompt)
        nxt = self.replies.pop(0)
        return ReasoningReply(decision_json=nxt, raw_text="x")


def _build_orchestrator(tmp_path: Path, replies: list[dict], *, log: ExecutionLogStore | None = None):
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    vers = create_default_verifier_registry()
    policy = PolicyEngine()
    audit = AuditLogger(log_path=str(tmp_path / "audit.log"))
    approval = __import__("hermes.security.approval_manager", fromlist=["ApprovalManager"]).ApprovalManager()
    approval.set_handler(approving_provider())
    tool_executor = ToolExecutor(tools, policy, audit, approval, vers)
    log = log or ExecutionLogStore(path=tmp_path / "log.jsonl")
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    llm = _StubLLM(replies)
    runtime = ReasoningRuntime(
        client=llm,
        capability_registry=caps,
        execution_log=log,
    )
    orch = V3Orchestrator(runtime=runtime, executor=executor, execution_log=log)
    return orch, llm, log


def test_session_store_round_trip(tmp_path: Path):
    store = SessionStore(directory=tmp_path / "sessions")
    state = SessionState(
        session_id="sess_test",
        objective="investigate disk usage",
        world_state={"open_applications": ["explorer.exe"]},
        task_state={"status": "in_progress"},
        evidence=[{"capability": "system.inspect", "claim": "ok", "source": "tool_report"}],
        references={"active_file": {"kind": "file", "value": "/tmp/x"}},
        completed=False,
        last_summary="",
    )
    store.save(state)
    loaded = store.load("sess_test")
    assert loaded is not None
    assert loaded.objective == state.objective
    assert loaded.world_state == state.world_state
    assert loaded.task_state == state.task_state
    assert loaded.evidence == state.evidence
    assert loaded.references == state.references


def test_session_store_atomic_write(tmp_path: Path):
    """A partially-written .tmp must never replace the canonical file."""
    store = SessionStore(directory=tmp_path / "sessions")
    state = SessionState(session_id="sess_atomic", objective="x")
    store.save(state)
    canonical = tmp_path / "sessions" / "sess_atomic.json"
    assert canonical.exists()
    # Simulate a crashed write: leave a stray .tmp file. The next
    # load should still return the canonical state.
    tmp_file = tmp_path / "sessions" / "sess_atomic.json.tmp"
    tmp_file.write_text("{", encoding="utf-8")
    loaded = store.load("sess_atomic")
    assert loaded is not None
    assert loaded.objective == "x"


def test_session_store_list_and_delete(tmp_path: Path):
    store = SessionStore(directory=tmp_path / "sessions")
    for s in ("s1", "s2", "s3"):
        store.save(SessionState(session_id=s, objective=s))
    assert store.list_sessions() == ["s1", "s2", "s3"]
    assert store.delete("s2") is True
    assert store.list_sessions() == ["s1", "s3"]
    assert store.delete("s2") is False


def test_session_store_version_mismatch_returns_none(tmp_path: Path):
    """If the schema_version in the file is unknown, refuse to load."""
    store = SessionStore(directory=tmp_path / "sessions")
    (tmp_path / "sessions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sessions" / "sess_x.json").write_text(
        json.dumps({"schema_version": 999, "state": {}}),
        encoding="utf-8",
    )
    assert store.load("sess_x") is None


@pytest.mark.asyncio
async def test_orchestrator_save_and_resume(tmp_path: Path):
    """A real turn snapshot can be saved mid-execution and resumed in a
    new orchestrator with the same world state."""
    # First orchestrator runs a partial turn and saves.
    orch1, _, _ = _build_orchestrator(
        tmp_path / "first",
        replies=[
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "first" / "out.txt"), "content": "first"},
            },
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": str(tmp_path / "first"),
            },
        ],
    )
    await orch1.process_turn("write then list")
    saved = await orch1.save_session("sess_resume")
    # Snapshot the world-model evidence + references via the helper.
    snap = snapshot_from_world_model(
        orch1.world_model,
        session_id="sess_resume",
        objective=orch1.world_model.task.objective or "write then list",
        correlation_id=orch1.state.correlation_id,
    )
    store = SessionStore(directory=tmp_path / "sessions")
    store.save(snap)

    # Second orchestrator resumes from the saved snapshot.
    orch2, _, _ = _build_orchestrator(
        tmp_path / "second",
        replies=[
            {"kind": "complete", "summary": "resumed", "evidence_ids": []},
        ],
    )
    loaded = store.load("sess_resume")
    assert loaded is not None
    await orch2.resume_session(loaded)
    # The world model must reflect the loaded state.
    assert orch2.world_model.task.objective == loaded.objective
    # The orchestrator reports the same correlation id so the next
    # iteration's events link to the original turn.
    assert orch2.state.correlation_id == loaded.correlation_id

    # Now we can continue the turn from where the previous one stopped.
    outcome = await orch2.process_turn("continue")
    assert outcome.completed is True
    assert outcome.reply == "resumed"


def test_apply_to_world_model_restores_evidence(tmp_path: Path):
    wm = WorldModel()
    state = SessionState(
        session_id="sess_apply",
        objective="write",
        evidence=[{"evidence_id": "ev_1", "capability": "filesystem.write", "claim": "ok", "source": "tool_report"}],
    )
    apply_to_world_model(state, wm)
    assert wm.evidence
    assert wm.evidence[0].claim == "ok"
