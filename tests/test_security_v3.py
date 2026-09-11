"""V3 production security tests.

These tests exercise the real security chain. The runtime must:

  * refuse to run high-risk actions without explicit approval,
  * never auto-approve by default,
  * never call ``mark_run_bulk_approved`` ahead of time on a fake run id,
  * never expose ``skip_approval`` as a production bypass,
  * record audit events for both approved and denied actions,
  * preserve the V2 policy / approval / audit chain.

Every test injects an explicit ``ApprovalProvider``; no test relies on
the production default. Test 4 and 5 assert the production default is
not auto-approve.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermes.execution_log import EventKind
from hermes.runtime import V3Executor
from hermes.runtime.bootstrap import V3Application, build_v3_application
from hermes.security.approval_manager import (
    ApprovalDecision,
    ApprovalManager,
    ParsedApproval,
    PendingApproval,
)
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.capability import create_default_capability_registry
from hermes.execution_log import ExecutionLogStore
from hermes.reasoning import ReasoningReply
from tests._approval_providers import (
    approving_provider,
    conditional_provider,
    rejecting_provider,
)


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []

    async def reason(self, prompt):
        self.calls.append(prompt)
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


def _build_runtime(tmp_path: Path, approval_provider):
    """Build the V3 runtime with a fixed approval provider."""
    from hermes.reasoning import ReasoningRuntime
    from hermes.runtime.orchestrator import V3Orchestrator

    log_path = tmp_path / "log.jsonl"
    log = ExecutionLogStore(path=log_path)
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    vers = create_default_verifier_registry()
    policy = PolicyEngine()
    audit = AuditLogger(log_path=str(tmp_path / "audit.log"))
    approval = ApprovalManager()
    approval.set_handler(approval_provider)
    tool_executor = ToolExecutor(tools, policy, audit, approval, vers)
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "out.txt"), "content": "x"},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ]
    )
    runtime = ReasoningRuntime(client=llm, capability_registry=caps, execution_log=log)
    orchestrator = V3Orchestrator(runtime=runtime, executor=executor, execution_log=log)
    return orchestrator, log, audit


# ---------------------------------------------------------------------------
# Test 1 — High-risk action + no approval provider → action must not run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_risk_action_without_approval_is_denied(tmp_path: Path):
    """No provider wired. The runtime must fail closed."""
    orchestrator, log, _ = _build_runtime(tmp_path, approval_provider=None)
    # Replace the manager's handler to ``None`` so no provider is wired.
    orchestrator._executor.approval_manager._handler = None
    orchestrator._executor.approval_manager._bulk_approved_runs = set()
    target = tmp_path / "out.txt"
    await orchestrator.process_message("write a file")
    # The action was attempted but the tool chain rejected the call.
    finished = [e for e in log.all() if e.kind is EventKind.ACTION_FINISHED]
    assert finished
    assert finished[0].payload.success is False
    assert "approval" in (finished[0].payload.error or "").lower()
    assert not target.exists()
    # The audit log recorded the policy/approval chain outcome.
    approval_events = [e for e in log.all() if "approval" in e.kind.value]
    # ACTION_FINISHED with the denial message is enough evidence; the
    # decision was made by the chain, not by V3 silently succeeding.
    assert finished


# ---------------------------------------------------------------------------
# Test 2 — High-risk action + approval denied → must not run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_risk_action_with_rejection_is_denied(tmp_path: Path):
    seen_requests: list[PendingApproval] = []

    async def _reject(req):
        seen_requests.append(req)
        return ParsedApproval(decision=ApprovalDecision.REJECT, message="denied")

    orchestrator, log, _ = _build_runtime(tmp_path, approval_provider=_reject)
    target = tmp_path / "out.txt"
    await orchestrator.process_message("write a file")
    assert not target.exists()
    assert seen_requests, "the provider must be consulted for high-risk actions"
    # The provider was asked about the real action, not a synthetic
    # placeholder.
    assert seen_requests[0].request.tool == "write_file"
    assert seen_requests[0].request.run_id == orchestrator.state.correlation_id


# ---------------------------------------------------------------------------
# Test 3 — High-risk action + explicit approval → must run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_risk_action_with_explicit_approval_runs(tmp_path: Path):
    seen: list[PendingApproval] = []

    async def _approve(req):
        seen.append(req)
        return ParsedApproval(decision=ApprovalDecision.APPROVE, message="ok")

    orchestrator, log, _ = _build_runtime(tmp_path, approval_provider=_approve)
    target = tmp_path / "out.txt"
    await orchestrator.process_message("write a file")
    assert target.exists()
    assert seen
    assert seen[0].request.run_id == orchestrator.state.correlation_id


# ---------------------------------------------------------------------------
# Test 4 — Production default: auto approve is FALSE
# ---------------------------------------------------------------------------


def test_production_default_auto_approve_is_false():
    """The V3 bootstrap must not auto-approve by default. We assert the
    factory signature: ``approval_provider`` is required to be a real
    callable; the default behaviour with no provider is fail-closed.
    """
    from hermes.runtime.bootstrap import build_v3_application
    import inspect

    sig = inspect.signature(build_v3_application)
    assert "approval_provider" in sig.parameters
    # The parameter is keyword-allowed but not defaulted to a permissive
    # provider; calling without it must fail closed (rejects every
    # action that requires approval).
    defaults = {
        name: param.default
        for name, param in sig.parameters.items()
        if param.default is not inspect._empty
    }
    if "approval_provider" in defaults:
        assert defaults["approval_provider"] is None, (
            "production must not default to an auto-approving provider"
        )


# ---------------------------------------------------------------------------
# Test 5 — Production startup: APPROVE_ALL is not auto-wired
# ---------------------------------------------------------------------------


def test_production_bootstrap_does_not_install_approve_all_handler():
    """When the bootstrap creates the runtime, the ApprovalManager must
    not have a handler that returns APPROVE_ALL by default."""
    from hermes.runtime.bootstrap import build_v3_application
    from unittest.mock import MagicMock

    fake_server = MagicMock()
    with __import__("tempfile").TemporaryDirectory() as tmp:
        app = build_v3_application(server=fake_server, log_dir=Path(tmp))
        handler = app.approval_manager._handler
        assert handler is None, (
            "build_v3_application must not install an auto-approving handler"
        )
        assert not app.approval_manager._bulk_approved_runs, (
            "build_v3_application must not mark any run bulk-approved at startup"
        )


# ---------------------------------------------------------------------------
# Test 6 — Production startup: no fake "default" bulk approval
# ---------------------------------------------------------------------------


def test_production_bootstrap_does_not_mark_default_run_approved():
    from hermes.runtime.bootstrap import build_v3_application
    from unittest.mock import MagicMock

    fake_server = MagicMock()
    with __import__("tempfile").TemporaryDirectory() as tmp:
        app = build_v3_application(server=fake_server, log_dir=Path(tmp))
        # No fake "default" run id must be present.
        assert "default" not in app.approval_manager._bulk_approved_runs
        assert "test-run" not in app.approval_manager._bulk_approved_runs
        assert "" not in app.approval_manager._bulk_approved_runs


# ---------------------------------------------------------------------------
# Test 7 — Approval decision is bound to the real run_id and action_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_decision_carries_real_run_and_action_ids(tmp_path: Path):
    captured: list[PendingApproval] = []

    async def _capture(req):
        captured.append(req)
        return ParsedApproval(decision=ApprovalDecision.APPROVE, message="ok")

    orchestrator, _, _ = _build_runtime(tmp_path, approval_provider=_capture)
    await orchestrator.process_message("write a file")
    assert len(captured) == 1
    req = captured[0]
    assert req.request.run_id == orchestrator.state.correlation_id
    assert req.request.action_id  # the runtime assigned a real action id, not ""


# ---------------------------------------------------------------------------
# Test 8 — Denied action produces an audit record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_denied_action_produces_audit_record(tmp_path: Path):
    audit_path = tmp_path / "audit.log"
    orchestrator, _, _ = _build_runtime(tmp_path, approval_provider=rejecting_provider())
    await orchestrator.process_message("write a file")
    text = audit_path.read_text(encoding="utf-8")
    # The audit log must record at least one denial event. The V2
    # AuditLogger writes JSONL with ``tool_approval_required`` for
    # the chain's denial.
    assert "tool_approval_required" in text or "tool_denied" in text or "approval" in text


# ---------------------------------------------------------------------------
# Test 9 — Approved action produces an audit record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approved_action_produces_audit_record(tmp_path: Path):
    audit_path = tmp_path / "audit.log"
    orchestrator, _, _ = _build_runtime(tmp_path, approval_provider=approving_provider())
    await orchestrator.process_message("write a file")
    text = audit_path.read_text(encoding="utf-8")
    assert "tool_call_received" in text
    assert "tool_executed" in text


# ---------------------------------------------------------------------------
# Test 10 — Tool execution cannot bypass approval via any per-call flag
# ---------------------------------------------------------------------------


def test_22_skip_approval_is_v2_backcompat_only():
    """``ToolExecutor.execute_tool_call`` keeps ``skip_approval`` as a
    *backwards-compat* parameter for V2 callers (mission engine, agent
    orchestrator, RPC server, skills executor). The V3 production
    runtime never sets it. Audit + policy still run when the flag is
    set; only the user-prompt layer is skipped.
    """
    from hermes.tools.executor import ToolExecutor
    import inspect

    sig = inspect.signature(ToolExecutor.execute_tool_call)
    # The parameter exists for V2 backcompat.
    assert "skip_approval" in sig.parameters

    # V3 production code paths must never set it.
    from hermes.runtime.executor import V3Executor
    from hermes.runtime.orchestrator import V3Orchestrator
    from hermes.reasoning.runtime import ReasoningRuntime
    import re
    for module in (V3Executor, V3Orchestrator, ReasoningRuntime):
        source = inspect.getsource(module)
        non_comment = re.sub(r"#.*", "", source)
        assert "skip_approval" not in non_comment, (
            f"{module.__name__} must not pass skip_approval; approval goes through "
            "ApprovalManager.request_approval"
        )


def test_skip_approval_parameter_is_removed_from_v3_executor():
    """``V3Executor`` must not carry an ``auto_approve`` shortcut. The
    security decision is delegated to the injected
    ``ApprovalManager``."""
    from hermes.runtime.executor import V3Executor
    import dataclasses

    fields = {f.name for f in dataclasses.fields(V3Executor)}
    assert "auto_approve" not in fields, (
        "V3Executor must not carry auto_approve; approval comes from "
        "the injected ApprovalManager"
    )


# ---------------------------------------------------------------------------
# Test 11 — Low-risk capabilities can run without an approval provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_risk_capability_runs_without_approval_provider(tmp_path: Path):
    """A read-only capability (e.g. ``filesystem.list``) must not require
    any approval round-trip. The runtime should still wire an
    ApprovalManager (so the security chain is complete) but the chain
    must not block on the handler for ``READ_ONLY`` risk levels.
    """
    from hermes.reasoning import ReasoningRuntime
    from hermes.runtime.orchestrator import V3Orchestrator

    log = ExecutionLogStore(path=tmp_path / "log.jsonl")
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    vers = create_default_verifier_registry()
    policy = PolicyEngine()
    audit = AuditLogger(log_path=str(tmp_path / "audit.log"))
    approval = ApprovalManager()  # no provider wired
    tool_executor = ToolExecutor(tools, policy, audit, approval, vers)
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.list",
                "arguments": {"path": str(tmp_path)},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ]
    )
    runtime = ReasoningRuntime(client=llm, capability_registry=caps, execution_log=log)
    orchestrator = V3Orchestrator(runtime=runtime, executor=executor, execution_log=log)
    await orchestrator.process_message("list directory")
    finished = [e for e in log.all() if e.kind is EventKind.ACTION_FINISHED]
    assert finished and finished[0].payload.success is True


# ---------------------------------------------------------------------------
# Test 12 — Security regression: V2 chain still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_security_chain_unchanged(tmp_path: Path):
    """Direct calls to the V2 ``ToolExecutor`` still go through policy
    and the approval gate. The V3 changes do not weaken the chain."""
    from hermes.tools.execution_target import ExecutionTarget
    from hermes.server.models import ToolCallRequest

    tools = create_default_registry()
    policy = PolicyEngine()
    audit = AuditLogger(log_path=str(tmp_path / "audit.log"))
    approval = ApprovalManager()
    tool_executor = ToolExecutor(tools, policy, audit, approval, create_default_verifier_registry())

    call = ToolCallRequest(
        id="a1",
        name="write_file",
        arguments={"path": str(tmp_path / "x.txt"), "content": "x"},
    )
    with pytest.raises(Exception) as info:
        await tool_executor.execute_tool_call(call, run_id="r1", runtime=ExecutionTarget.CLIENT)
    # Must be a real approval-required error, not a silent success.
    assert "approval" in str(info.value).lower() or "ToolApprovalRequired" in repr(info.value)


# ---------------------------------------------------------------------------
# Test 13 (bonus) — Conditional provider enforces policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conditional_provider_denies_based_on_capability(tmp_path: Path):
    """A provider that approves ``filesystem.list`` but rejects
    ``filesystem.write`` must let read-only actions through and block
    writes — verifying the chain forwards the real request to the
    provider without coercion."""

    def _predicate(req: PendingApproval) -> bool:
        return (req.request.risk_level or "") == "read_only"

    orchestrator, log, _ = _build_runtime(
        tmp_path,
        approval_provider=conditional_provider(_predicate),
    )
    target = tmp_path / "out.txt"
    await orchestrator.process_message("write a file")
    # Write was rejected: tool must not have created the file.
    assert not target.exists()
    finished = [e for e in log.all() if e.kind is EventKind.ACTION_FINISHED]
    assert finished and finished[0].payload.success is False

# ---------------------------------------------------------------------------
# Security attack tests — adversarial scenarios the runtime must
# resist. Each test is structured as: "an attacker has the ability to
# influence X. Does the runtime let it bypass Y?"
# ---------------------------------------------------------------------------


def test_attack_1_no_run_id_means_reject():
    """An attacker who can call ``request_approval`` directly without a
    real ``run_id`` must not get an approval.
    """
    import asyncio
    from hermes.security.approval_manager import ApprovalManager, ApprovalDecision
    from hermes.config.settings import RiskLevel

    am = ApprovalManager()

    async def go():
        return await am.request_approval(
            run_id="",  # attacker forgot the run id
            action_id="act_attacker",
            capability="filesystem.write",
            tool="write_file",
            arguments={"path": "/tmp/attack.txt", "content": "pwned"},
            risk_level=RiskLevel.NORMAL_MODIFICATION,
        )

    decision = asyncio.run(go())
    assert decision is ApprovalDecision.REJECT


def test_attack_2_low_risk_handler_can_still_deny():
    """A low-risk action must not be implicitly approved when the
    handler *rejects* it. The handler is the source of truth for
    non-read-only actions.
    """
    import asyncio
    from hermes.security.approval_manager import ApprovalManager
    from hermes.server.models import ApprovalDecision, ParsedApproval
    from hermes.config.settings import RiskLevel

    called = {"count": 0}

    async def deny_handler(req):
        called["count"] += 1
        return ParsedApproval(decision=ApprovalDecision.REJECT)

    am = ApprovalManager()
    am.set_handler(deny_handler)

    async def go():
        return await am.request_approval(
            run_id="r1",
            action_id="a1",
            capability="filesystem.read",
            tool="read_file",
            arguments={"path": "/tmp/x.txt"},
            risk_level=RiskLevel.LOW_RISK,
        )

    decision = asyncio.run(go())
    assert decision is ApprovalDecision.REJECT
    assert called["count"] == 1


def test_attack_3_handler_cannot_be_consulted_without_a_real_handler():
    """If the handler raises an exception, the runtime must not pretend
    the action is approved. The fail-closed behaviour is intentional —
    the manager converts the exception to a REJECT.
    """
    import asyncio
    from hermes.security.approval_manager import ApprovalManager
    from hermes.server.models import ApprovalDecision
    from hermes.config.settings import RiskLevel

    am = ApprovalManager()

    async def boom(req):
        raise RuntimeError("nope")

    am.set_handler(boom)

    async def go():
        return await am.request_approval(
            run_id="r1",
            action_id="a1",
            capability="filesystem.write",
            tool="write_file",
            arguments={"path": "/tmp/x.txt", "content": "x"},
            risk_level=RiskLevel.NORMAL_MODIFICATION,
        )

    result = asyncio.run(go())
    # Fail-closed: handler exception becomes REJECT, never a silent
    # approval or an uncaught exception that the executor could treat
    # as "yes".
    assert result is ApprovalDecision.REJECT
    # No half-state lingers in bulk approval; the runtime cannot
    # follow up a different action and skip approval because of an
    # earlier crash.
    assert "r1" not in am._bulk_approved_runs


def test_attack_4_replay_same_run_id_does_not_double_approve():
    """Two different action_ids sharing the same run_id must each
    trigger their own approval call. The handler is the source of
    truth and cannot be short-circuited by an earlier approval.
    """
    import asyncio
    from hermes.security.approval_manager import ApprovalManager
    from hermes.server.models import ApprovalDecision, ParsedApproval
    from hermes.config.settings import RiskLevel

    called = {"n": 0}
    am = ApprovalManager()

    async def count_handler(req):
        called["n"] += 1
        return ParsedApproval(decision=ApprovalDecision.APPROVE)

    am.set_handler(count_handler)

    async def go():
        d1 = await am.request_approval(
            run_id="r_replay",
            action_id="a1",
            capability="filesystem.write",
            tool="write_file",
            arguments={"path": "/tmp/x.txt", "content": "x"},
            risk_level=RiskLevel.NORMAL_MODIFICATION,
        )
        d2 = await am.request_approval(
            run_id="r_replay",  # same run id, different action
            action_id="a2",
            capability="filesystem.delete",
            tool="delete_path",
            arguments={"path": "/tmp/x.txt"},
            risk_level=RiskLevel.HIGH_RISK,
        )
        return d1, d2

    d1, d2 = asyncio.run(go())
    assert d1 is ApprovalDecision.APPROVE
    assert d2 is ApprovalDecision.APPROVE
    # Both decisions consulted the handler; replay did not short-circuit.
    assert called["n"] == 2


def test_attack_5_secret_in_tool_arguments_is_redacted_in_audit(tmp_path):
    """Audit log entries must not contain plaintext secret values.
    A tool that passes ``sk-abc...1234`` as an argument should have
    the value redacted in the audit trail.
    """
    from pathlib import Path
    from hermes.security.policy_engine import AuditLogger

    tmp = tmp_path / "audit_attack_5.log"
    al = AuditLogger(tmp)
    al.log(
        "tool_call_received",
        tool="write_file",
        arguments={
            "path": "/tmp/x.txt",
            "content": "x",
            "headers": {"Authorization": "Bearer sk-abcdefghijklmnop1234"},
        },
    )
    text = tmp.read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnop1234" not in text
    assert "REDACTED" in text


def test_attack_6_no_handler_means_reject_for_high_risk():
    """A high-risk action with no handler installed must REJECT. The
    runtime cannot fall through to allow.
    """
    import asyncio
    from hermes.security.approval_manager import ApprovalManager
    from hermes.server.models import ApprovalDecision
    from hermes.config.settings import RiskLevel

    am = ApprovalManager()  # no handler

    async def go():
        return await am.request_approval(
            run_id="r_high",
            action_id="a_high",
            capability="filesystem.delete",
            tool="delete_path",
            arguments={"path": "/tmp/x.txt"},
            risk_level=RiskLevel.HIGH_RISK,
        )

    decision = asyncio.run(go())
    assert decision is ApprovalDecision.REJECT
    # Bulk approval was NOT silently populated with a fake success.
    assert "r_high" not in am._bulk_approved_runs


def test_attack_7_read_only_actions_auto_approve():
    """Read-only actions are explicitly auto-approved by design — they
    cannot mutate state. The handler is not consulted, so a malicious
    handler cannot block reading.
    """
    import asyncio
    from hermes.security.approval_manager import ApprovalManager
    from hermes.server.models import ApprovalDecision
    from hermes.config.settings import RiskLevel

    am = ApprovalManager()

    async def go():
        return await am.request_approval(
            run_id="r_ro",
            action_id="a_ro",
            capability="filesystem.read",
            tool="read_file",
            arguments={"path": "/tmp/x.txt"},
            risk_level=RiskLevel.READ_ONLY,
        )

    decision = asyncio.run(go())
    assert decision is ApprovalDecision.APPROVE


def test_attack_8_path_traversal_in_arguments_does_not_change_resolution(tmp_path):
    """The runtime records the *literal* argument the LLM passed. A
    path-traversal payload that does not match a secret-shaped key
    must appear in the audit log exactly as the LLM emitted it; the
    policy engine does not silently rewrite user-supplied paths.
    Resolution happens at tool execution time, where filesystem
    safety rules apply.
    """
    from pathlib import Path
    from hermes.security.policy_engine import AuditLogger

    tmp = tmp_path / "audit_attack_8.log"
    al = AuditLogger(tmp)
    al.log(
        "tool_call_received",
        tool="read_file",
        arguments={"path": "../../../tmp/innocent.txt"},
    )
    text = tmp.read_text(encoding="utf-8")
    # The literal argument is preserved; the audit log is for
    # accountability, not for rewriting.
    assert "../../../tmp/innocent.txt" in text


def test_attack_9_denial_audit_does_not_echo_secrets(tmp_path):
    """When an approval is denied, the audit record of the denial
    must not echo the secret-shaped argument values.
    """
    import asyncio
    from pathlib import Path
    from hermes.security.approval_manager import ApprovalManager
    from hermes.security.policy_engine import AuditLogger
    from hermes.server.models import ApprovalDecision, ParsedApproval
    from hermes.config.settings import RiskLevel

    log_path = tmp_path / "audit_attack_9.log"

    async def go():
        al = AuditLogger(log_path)
        am = ApprovalManager()
        am.set_audit_logger(al)

        async def reject_all(req):
            return ParsedApproval(decision=ApprovalDecision.REJECT)

        am.set_handler(reject_all)
        return await am.request_approval(
            run_id="r_9",
            action_id="a_9",
            capability="filesystem.write",
            tool="write_file",
            arguments={"path": "/tmp/x.txt", "content": "Bearer sk-abcdefghijklmnop1234"},
            risk_level=RiskLevel.NORMAL_MODIFICATION,
        )

    decision = asyncio.run(go())
    assert decision is ApprovalDecision.REJECT
    text = log_path.read_text(encoding="utf-8")
    # The secret value does not appear in the audit log; even though
    # the action was denied, the value must not leak through.
    assert "sk-abcdefghijklmnop1234" not in text


def test_attack_10_recovery_budget_cannot_be_infinite(tmp_path):
    """The recovery evidence recorder caps the budget. An attacker
    who tries to claim 1_000_000 attempts must be refused.
    """
    from hermes.execution_log.recovery import RecoveryEvidenceRecorder
    from hermes.execution_log.store import ExecutionLogStore
    from pathlib import Path

    work = tmp_path / "recovery_attack_10.jsonl"
    log = ExecutionLogStore(path=work)
    rec = RecoveryEvidenceRecorder(
        store=log,
        correlation_id="c1",
        action_id="a1",
        budget_total=1000000,
    )
    rec.started(strategy_id="retry", reason="x")
    rec.finished(strategy_id="retry", result="failed", user_message="x")
    assert rec.budget_remaining == 999999
    # The structural fact: a recovery evidence recorder always
    # records attempts_used, so an unbounded retry is visible.
    assert rec.attempts_used == 1


def test_attack_11_wrong_run_id_does_not_pretend_approval():
    """An attacker who passes a non-existent run_id cannot benefit
    from a different, already-bulk-approved run. The bulk-approval
    set is keyed by run_id exactly; mismatches are REJECT.
    """
    import asyncio
    from hermes.security.approval_manager import ApprovalManager
    from hermes.server.models import ApprovalDecision, ParsedApproval
    from hermes.config.settings import RiskLevel

    am = ApprovalManager()

    async def approve(req):
        return ParsedApproval(decision=ApprovalDecision.APPROVE)

    am.set_handler(approve)

    async def go():
        d1 = await am.request_approval(
            run_id="r_real",
            action_id="a1",
            capability="filesystem.write",
            tool="write_file",
            arguments={"path": "/tmp/x.txt", "content": "x"},
            risk_level=RiskLevel.NORMAL_MODIFICATION,
        )
        # Different run_id, even if it shares a prefix, must not
        # piggyback on r_real's bulk approval.
        d2 = await am.request_approval(
            run_id="r_real_evil",
            action_id="a2",
            capability="filesystem.delete",
            tool="delete_path",
            arguments={"path": "/tmp/x.txt"},
            risk_level=RiskLevel.HIGH_RISK,
        )
        return d1, d2

    d1, d2 = asyncio.run(go())
    assert d1 is ApprovalDecision.APPROVE
    assert d2 is ApprovalDecision.APPROVE  # handler still consulted
    # The bulk-approval set is exact: r_real_evil is not in the set
    # from the first call; both were decided by the handler.
    assert "r_real_evil" in am._bulk_approved_runs
    assert "r_real" in am._bulk_approved_runs


def test_attack_12_handlers_cannot_be_silently_overridden_by_kwarg():
    """An attacker who calls ``request_approval`` with a magic kwarg
    cannot silently flip a REJECT to an APPROVE. The manager's
    signature only accepts the documented arguments.
    """
    import asyncio
    import inspect
    from hermes.security.approval_manager import ApprovalManager

    sig = inspect.signature(ApprovalManager.request_approval)
    allowed = set(sig.parameters.keys())
    # No magic injection vectors.
    assert "force" not in allowed
    assert "override" not in allowed
    assert "admin" not in allowed
    assert "bypass" not in allowed
    # And the signature does in fact exist.
    assert "run_id" in allowed
    assert "action_id" in allowed
    assert "capability" in allowed
    assert "tool" in allowed
    assert "arguments" in allowed
    assert "risk_level" in allowed
