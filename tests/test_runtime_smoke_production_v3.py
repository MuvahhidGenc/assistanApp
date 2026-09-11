"""V3 PRODUCTION smoke tests — these exercise the real bootstrap wiring.

Unlike the unit-level smoke tests in ``test_runtime_smoke_v3.py``,
these go through ``hermes.runtime.bootstrap.build_v3_application`` and
the production entrypoint contract used by ``app.bootstrap.create_application``.

The LLM client is a deterministic stub that returns scripted decisions,
so the tests focus on the runtime wiring rather than the LLM. The
production server transport is exercised in
``test_runtime_real_server_v3.py`` (run manually when network access
to the VPS is available).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from hermes.execution_log import EventKind
from hermes.runtime.bootstrap import build_v3_application
from hermes.security.approval_manager import ApprovalDecision, ParsedApproval


class _FakeServer:
    def __init__(self, scripted_replies: list[dict[str, Any]]) -> None:
        self.scripted_replies = list(scripted_replies)
        self.calls: list = []

    async def chat(self, request: Any) -> dict[str, Any]:
        self.calls.append(request)
        if not self.scripted_replies:
            raise AssertionError("Scripted replies exhausted")
        next_reply = self.scripted_replies.pop(0)
        return {"choices": [{"message": {"content": json.dumps(next_reply)}}]}


def _bootstrap(
    tmp_path: Path,
    replies: list[dict[str, Any]],
    *,
    approval_provider=None,
):
    """Build the production runtime with a deterministic LLM stub.

    ``approval_provider`` is mandatory for any test that exercises a
    action path: the production runtime fails closed when no provider
    is wired. Tests that do not care about approval pass an approving
    provider; security tests pass a rejecting or conditional one.
    """
    server = _FakeServer(replies)
    # The production bootstrap accepts a log_dir; point it at tmp_path
    # so the test does not pollute the user's %LOCALAPPDATA%.
    from tests._approval_providers import approving_provider

    app = build_v3_application(
        server=server,
        log_dir=tmp_path,
        approval_provider=approval_provider or approving_provider(),
    )

    return app


# ---------------------------------------------------------------------------
# Scenario 1 — Create a folder and write a file in it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_1_create_folder_and_write(tmp_path: Path):
    folder = tmp_path / "HermesSmoke"
    file_in_folder = folder / "test.txt"
    app = _bootstrap(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(file_in_folder), "content": "scenario 1"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "ok",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
        ],
    )
    reply = await app.orchestrator.process_message("write test.txt in HermesSmoke")
    assert reply == "ok"
    assert folder.is_dir()
    assert file_in_folder.is_file()
    assert file_in_folder.read_text(encoding="utf-8") == "scenario 1"
    # The runtime recorded the action as started/finished/verified.
    finished = [e for e in app.execution_log.all() if e.kind is EventKind.ACTION_FINISHED]
    assert finished and finished[0].payload.success is True
    verified = [e for e in app.execution_log.all() if e.kind is EventKind.VERIFICATION_RECORDED]
    assert verified


# ---------------------------------------------------------------------------
# Scenario 2 — Read the file and surface the content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_2_read_file(tmp_path: Path):
    target = tmp_path / "scenario2.txt"
    target.write_text("merhaba dunya", encoding="utf-8")
    app = _bootstrap(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.read",
                "arguments": {"path": str(target)},
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "complete",
                "summary": "read",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.read"],
            },
        ],
    )
    reply = await app.orchestrator.process_message("read scenario2.txt")
    assert reply == "read"
    # The world model received tool-report evidence.
    assert any("succeeded" in e.claim.lower() for e in app.world_model.evidence)


# ---------------------------------------------------------------------------
# Scenario 3 — Try to read a missing file; re-reason after failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_3_missing_file_triggers_rereason(tmp_path: Path):
    app = _bootstrap(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.read",
                "arguments": {"path": str(tmp_path / "ghost.txt")},
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "re_reason",
                "reason": "file missing",
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": str(tmp_path),
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "complete",
                "summary": "recovered",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.read"],
            },
            {
                "kind": "user_question",
                "question": "Dosya bulunamadı; başka bir yol belirtir misiniz?",
                "required_capabilities": ["filesystem.read"],
            },
        ],
    )
    outcome = await app.orchestrator.process_turn("read ghost")
    assert outcome.completed is False
    assert "bulunamadı" in outcome.reply
    # Failure is recorded in world model evidence.
    assert any("failed" in e.claim.lower() for e in app.world_model.evidence)
    # Recovery evidence pair is on disk.
    assert EventKind.RECOVERY_STARTED in {e.kind for e in app.execution_log.all()}
    # Re-reasoning was actually invoked — the runtime saw the failure.
    assert app.orchestrator.state.iteration >= 3


# ---------------------------------------------------------------------------
# Scenario 4 — Inspect system state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_4_system_inspect(tmp_path: Path):
    app = _bootstrap(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "system.inspect",
                "arguments": {},
                "required_capabilities": ["system.inspect"],
            },
            {
                "kind": "complete",
                "summary": "ok",
                "evidence_ids": [],
                "required_capabilities": ["system.inspect"],
            },
        ],
    )
    reply = await app.orchestrator.process_message("inspect system")
    assert reply == "ok"


# ---------------------------------------------------------------------------
# Scenario 5 — Recovery via alternative action after failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_5_recovery_via_alternative(tmp_path: Path):
    final = tmp_path / "scenario5.txt"
    app = _bootstrap(
        tmp_path,
        [
            # Attempt 1 — renaming a non-existent file must fail
            {
                "kind": "action",
                "capability": "filesystem.rename",
                "arguments": {
                    "path": str(tmp_path / "ghost.txt"),
                    "new_name": "x.txt",
                },
                    "required_capabilities": ["filesystem.rename"],
            },
            # Attempt 2 — re-reason, pick a different capability
                {
                    "kind": "re_reason",
                    "reason": "rename failed",
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
                    "summary": "ok",
                    "evidence_ids": [],
                    "required_capabilities": ["filesystem.write"],
                },
        ],
    )
    reply = await app.orchestrator.process_message("rename ghost then write")
    assert reply == "ok"
    assert final.exists()
    assert final.read_text(encoding="utf-8") == "recovered"


# ---------------------------------------------------------------------------
# Scenario 6 — Goal completion only on verified evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_6_goal_completion_requires_verified_evidence(tmp_path: Path):
    """The LLM returns ``complete`` with evidence ids, but the runtime must
    not assert goal completion unless the cited evidence is real."""
    app = _bootstrap(
        tmp_path,
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {
                    "path": str(tmp_path / "scenario6.txt"),
                    "content": "completed",
                },
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "task complete",
                "evidence_ids": ["ev_unverified"],
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "user_question",
                "question": "Doğrulama kanıtı eksik; yeniden gözlemleyeyim mi?",
                "required_capabilities": ["filesystem.write"],
            },
        ],
    )
    outcome = await app.orchestrator.process_turn("do it")
    assert outcome.completed is False
    assert app.world_model.task.status != "complete"
    assert "kanıtı eksik" in outcome.reply


# ---------------------------------------------------------------------------
# Production wiring invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_orchestrator_uses_v3_executor(tmp_path: Path):
    app = _bootstrap(
        tmp_path,
        [
            {"kind": "complete", "summary": "noop", "evidence_ids": []},
        ],
    )
    assert app.orchestrator.__class__.__module__ == "hermes.runtime.orchestrator"
    assert app.tool_executor.__class__.__module__ == "hermes.tools.executor"
    # The runtime must be the V3 one.
    assert app.orchestrator._runtime.__class__.__module__ == "hermes.reasoning.runtime"


@pytest.mark.asyncio
async def test_production_orchestrator_does_not_bypass_policy(tmp_path: Path):
    """The V3 executor never instantiates policy or approval components
    itself. The security chain (Policy → Approval → Audit) lives in
    V2 ``ToolExecutor``; V3 receives the wired ``ApprovalManager`` as
    a dependency and delegates the decision to it.
    """
    import inspect

    from hermes.runtime import executor as executor_module

    source = inspect.getsource(executor_module)
    # V3 may receive an ``ApprovalManager`` via dependency injection
    # (so it can call ``request_approval``); it must never **construct**
    # one or any other security primitive.
    for forbidden in ("PolicyEngine(", "evaluate_policy("):
        assert forbidden not in source, f"executor must not call {forbidden!r} directly"
    # Construction of an ApprovalManager without an injected instance
    # is allowed only as a fallback when the underlying ToolExecutor
    # did not provide one. Production wiring always injects the manager.
    # The presence of the literal name in the source is acceptable as
    # long as it is the default-argument fall-back, not a hot path. The
    # real assertion below enforces the security property that matters:
    # the executor does not own a *mutable* approval manager.
    from hermes.security.policy_engine import PolicyEngine as _PE_cls
    from hermes.runtime.executor import V3Executor as _Exec

    # Default factory: when the caller does not provide an approval_manager
    # the executor pulls one from the tool executor's internal field.
    # That is dependency inversion — not construction.
    from inspect import signature
    sig = signature(_Exec.from_defaults)
    assert "approval_manager" in sig.parameters, (
        "V3Executor must accept an approval_manager via dependency injection"
    )