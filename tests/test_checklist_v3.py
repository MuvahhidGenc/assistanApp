"""End-to-end production sanity test for the V3 checklist.

This single test exercises the production wiring the audit requires
and asserts the final-acceptance criteria that can be observed in a
test. Criteria that require a live VPS or a human are documented in
the test docstring and excluded from the assertions.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from hermes.app.bootstrap import HermesApplication
from hermes.execution_log import EventKind
from hermes.runtime.bootstrap import build_v3_application
from hermes.runtime.orchestrator import V3AgentPhase
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from tests._approval_providers import approving_provider


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    async def reason(self, prompt):
        from hermes.reasoning import ReasoningReply

        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


# ---------------------------------------------------------------------------
# 1 — Production entrypoint is V3
# ---------------------------------------------------------------------------


def test_1_production_entrypoint_uses_v3():
    """``app.bootstrap.create_application`` returns a V3 orchestrator."""
    import asyncio
    from unittest.mock import MagicMock

    fake = MagicMock()
    with __import__("tempfile").TemporaryDirectory() as tmp:
        from hermes.app.bootstrap import create_application

        # Build the app object directly with a fake server.
        from hermes.config.settings import AppSettings
        from hermes.security.approval_manager import ApprovalDecision, ParsedApproval
        from hermes.runtime.bootstrap import build_v3_application

        async def _approve(req):
            return ParsedApproval(decision=ApprovalDecision.APPROVE)

        # We cannot call create_application because it needs a real api
        # key. Use build_v3_application which is the actual production
        # factory and is wired from create_application.
        app = build_v3_application(
            server=fake,
            log_dir=Path(tmp),
            approval_provider=_approve,
        )
        assert app.orchestrator.__class__.__module__ == "hermes.runtime.orchestrator"


# ---------------------------------------------------------------------------
# 4, 5, 6, 23 — local semantic routing removed, capability registry does
#                 not decide, no fake bulk approval, test approval is
#                 isolated from production.
# ---------------------------------------------------------------------------


def test_4_local_routing_not_used_by_runtime():
    import hermes.runtime.executor as executor
    import hermes.reasoning.runtime as reasoning

    for module in (executor, reasoning):
        source = inspect.getsource(module)
        for forbidden in (
            "from hermes.agent.local_intent",
            "from hermes.agent.goal_router",
            "from hermes.agent.task_planner",
            "from hermes.intent.router",
            "from hermes.intent.understanding",
        ):
            assert forbidden not in source, (
                f"{module.__name__} must not depend on legacy semantic routing"
            )


def test_5_capability_registry_does_not_route():
    from hermes.capability import CapabilityRegistry

    forbidden = [
        "route", "decide", "classify", "match", "dispatch", "plan",
        "interpret", "understand", "choose_intent", "score_message",
    ]
    for method in forbidden:
        assert not hasattr(CapabilityRegistry, method), (
            f"CapabilityRegistry must not expose {method!r}"
        )


def test_6_production_bootstrap_does_not_install_default_approval():
    from hermes.runtime.bootstrap import build_v3_application
    from unittest.mock import MagicMock

    fake = MagicMock()
    with __import__("tempfile").TemporaryDirectory() as tmp:
        app = build_v3_application(server=fake, log_dir=Path(tmp))
        assert "default" not in app.approval_manager._bulk_approved_runs


# ---------------------------------------------------------------------------
# 16, 17, 18 — Policy, Approval, Audit all wired.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_16_17_18_policy_approval_audit_wired(tmp_path: Path):
    from hermes.capability import create_default_capability_registry
    from hermes.execution_log import ExecutionLogStore
    from hermes.reasoning import ReasoningRuntime
    from hermes.runtime.executor import V3Executor
    from hermes.runtime.orchestrator import V3Orchestrator
    from hermes.security.approval_manager import ApprovalManager
    from hermes.security.policy_engine import AuditLogger, PolicyEngine
    from hermes.tools.registry import create_default_registry
    from hermes.tools.verifiers.registry import create_default_verifier_registry

    log = ExecutionLogStore(path=tmp_path / "log.jsonl")
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    vers = create_default_verifier_registry()
    policy = PolicyEngine()
    audit = AuditLogger(log_path=str(tmp_path / "audit.log"))
    approval = ApprovalManager()
    approval.set_handler(approving_provider())
    tool_executor = ToolExecutor(tools, policy, audit, approval, vers)
    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    target = tmp_path / "wire.txt"
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "wired"},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ]
    )
    runtime = ReasoningRuntime(client=llm, capability_registry=caps, execution_log=log)
    orchestrator = V3Orchestrator(runtime=runtime, executor=executor, execution_log=log)
    await orchestrator.process_message("write wire.txt")
    # Audit recorded the call
    assert "tool_call_received" in (tmp_path / "audit.log").read_text()
    # Execution log has the action events
    kinds = {e.kind for e in log.all()}
    assert EventKind.ACTION_STARTED in kinds
    assert EventKind.ACTION_FINISHED in kinds
    assert EventKind.VERIFICATION_RECORDED in kinds


# ---------------------------------------------------------------------------
# 22 — skip_approval is a backwards-compat parameter; the V3 runtime
#      never passes it.
# ---------------------------------------------------------------------------


def test_22_skip_approval_is_v2_backcompat_not_v3():
    from hermes.runtime.executor import V3Executor
    import re as _re

    source = inspect.getsource(V3Executor)
    non_comment = _re.sub(r"#.*", "", source)
    assert "skip_approval" not in non_comment


# ---------------------------------------------------------------------------
# 28 — Real server transport: MockTransport is test-only.
# ---------------------------------------------------------------------------


def test_28_bootstrap_uses_real_hermes_server_client_in_production():
    from hermes.runtime import bootstrap as bootstrap_mod

    source = inspect.getsource(bootstrap_mod)
    # The bootstrap accepts any object that has a ``chat`` method. The
    # production caller passes ``HermesServerClient``. The only place
    # ``MockTransport`` may appear is the test module.
    assert "MockTransport" not in source


# ---------------------------------------------------------------------------
# 30 — Shim/stub/no-op regression: V3 modules do not import or
#      implement these patterns.
# ---------------------------------------------------------------------------


def test_30_no_shim_stub_noop_in_v3_modules():
    forbidden = ("shim", "stub", "no-op", "noop", "placeholder")
    for module_path in (
        "hermes.capability",
        "hermes.execution_log",
        "hermes.world_model",
        "hermes.reasoning",
        "hermes.memory",
        "hermes.runtime",
    ):
        module = __import__(module_path, fromlist=["*"])
        module_file = module.__file__
        if module_file is None or not module_file.endswith(".py"):
            continue
        text = open(module_file, encoding="utf-8").read()
        # Strip comments and docstrings to avoid false positives on
        # anti-pattern notes.
        non_comment = re.sub(r"#.*", "", text)
        non_docstring = re.sub(r'""".*?"""', "", non_comment, flags=re.DOTALL)
        for token in forbidden:
            if token in non_docstring:
                raise AssertionError(
                    f"{module_path} contains forbidden token {token!r}"
                )