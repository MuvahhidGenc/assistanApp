"""Filesystem ground-truth test.

A tool that reports ``success=True`` is not the same as a tool whose
side effect actually took place. The V3 runtime must consult the
verifier and refuse to mark the action successful — and the user
turn must re-reason — when reality contradicts the tool's report.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hermes.execution_log import EventKind, ExecutionLogStore
from hermes.reasoning import ReasoningReply
from hermes.reasoning.transport import ReasoningPrompt
from hermes.runtime.bootstrap import build_v3_application
from hermes.security.approval_manager import ApprovalDecision, ParsedApproval
from tests._approval_providers import approving_provider


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(prompt)
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


def _build_runtime_with_liar_tool(tmp_path: Path, *, liar: bool):
    """Build a V3 runtime whose ``write_file`` tool lies when ``liar``.

    The liar tool returns ``success=True`` and reports a path, but does
    not actually touch the filesystem. The verifier (``WriteFileVerifier``)
    then must catch the mismatch.
    """
    from hermes.reasoning import ReasoningRuntime
    from hermes.runtime.executor import V3Executor
    from hermes.runtime.orchestrator import V3Orchestrator
    from hermes.security.approval_manager import ApprovalManager
    from hermes.security.policy_engine import AuditLogger, PolicyEngine
    from hermes.tools.executor import ToolExecutor
    from hermes.tools.registry import create_default_registry
    from hermes.tools.verifiers.registry import create_default_verifier_registry
    from hermes.capability import create_default_capability_registry

    log = ExecutionLogStore(path=tmp_path / "log.jsonl")
    tools = create_default_registry()
    if liar:
        # Replace write_file with a liar.
        from hermes.tools.windows.file_tools import WriteFileTool
        from hermes.tools.base import ToolExecutionResult

        class LiarWriteFile(WriteFileTool):
            async def execute(self, **kwargs: Any) -> ToolExecutionResult:
                path = kwargs.get("path") or kwargs.get("file") or ""
                return ToolExecutionResult(
                    success=True,
                    output={"path": path, "size": len(kwargs.get("content", "")), "exists": True, "verified_listing": []},
                )

        for tool in tools._tools.values():
            if tool.name == "write_file":
                tools._tools["write_file"] = LiarWriteFile()
                break
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
    return log, executor


@pytest.mark.asyncio
async def test_liar_write_file_does_not_create_file(tmp_path: Path):
    """The verifier must catch a tool that reports success but did
    not actually write the file. The runtime must not declare the
    action verified."""
    log, executor = _build_runtime_with_liar_tool(tmp_path, liar=True)
    target = tmp_path / "out.txt"
    from hermes.reasoning import ReasoningRuntime
    from hermes.runtime.orchestrator import V3Orchestrator
    caps = executor.capability_registry
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "should be there"},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ]
    )
    runtime = ReasoningRuntime(client=llm, capability_registry=caps, execution_log=log)
    orchestrator = V3Orchestrator(runtime=runtime, executor=executor, execution_log=log)
    await orchestrator.process_message("write a file")
    # The file does not exist on disk.
    assert not target.exists()
    # The action was recorded as failed by the verifier.
    finished = [e for e in log.all() if e.kind is EventKind.ACTION_FINISHED]
    assert finished
    # The tool claimed success=True, but the verifier recorded a non-passing
    # verification result. The runtime must not present the action as fully
    # verified.
    verification = [e for e in log.all() if e.kind is EventKind.VERIFICATION_RECORDED]
    assert verification
    assert verification[0].payload.status in {"failed", "unknown"}


@pytest.mark.asyncio
async def test_honest_write_file_passes_verification(tmp_path: Path):
    """Baseline: an honest tool that writes the file passes verification."""
    log, executor = _build_runtime_with_liar_tool(tmp_path, liar=False)
    target = tmp_path / "out.txt"
    from hermes.reasoning import ReasoningRuntime
    from hermes.runtime.orchestrator import V3Orchestrator
    caps = executor.capability_registry
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "real content"},
            },
            {"kind": "complete", "summary": "ok", "evidence_ids": []},
        ]
    )
    runtime = ReasoningRuntime(client=llm, capability_registry=caps, execution_log=log)
    orchestrator = V3Orchestrator(runtime=runtime, executor=executor, execution_log=log)
    await orchestrator.process_message("write a file")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "real content"

class _LiarMoveFile:
    """Pretends to move a file but does not touch the filesystem."""

    async def execute(self, **kwargs):
        from hermes.tools.base import ToolExecutionResult
        return ToolExecutionResult(success=True, output={"path": kwargs.get("path")})


class _LiarDelete:
    async def execute(self, **kwargs):
        from hermes.tools.base import ToolExecutionResult
        return ToolExecutionResult(success=True, output={"path": kwargs.get("path")})


def test_filesystem_change_verifier_catches_liar_move(tmp_path: Path):
    """A tool that claims success but did not move the file is caught."""
    from hermes.tools.verifiers.specific import FilesystemChangeVerifier
    from hermes.tools.verifiers.context import VerifierContext

    source = tmp_path / "src.txt"
    source.write_text("hello", encoding="utf-8")
    dest = tmp_path / "dest.txt"
    # Liar claims success but did not actually move.
    ctx = VerifierContext(
        tool_name="move_file",
        tool_arguments={"path": str(source), "destination": str(dest)},
        execution_success=True,
        execution_output={"path": str(source)},
        run_id="r1",
    )
    result = asyncio.run(FilesystemChangeVerifier().verify(ctx))
    assert result.status.value == "failed"
    assert "dest_missing" in result.details.get("reason", "")


def test_filesystem_change_verifier_detects_real_move(tmp_path: Path):
    from hermes.tools.verifiers.specific import FilesystemChangeVerifier
    from hermes.tools.verifiers.context import VerifierContext

    source = tmp_path / "src.txt"
    source.write_text("hello", encoding="utf-8")
    dest = tmp_path / "dest.txt"
    source.rename(dest)
    ctx = VerifierContext(
        tool_name="move_file",
        tool_arguments={"path": str(source), "destination": str(dest)},
        execution_success=True,
        execution_output={"path": str(dest)},
        run_id="r1",
    )
    result = asyncio.run(FilesystemChangeVerifier().verify(ctx))
    assert result.status.value == "verified"


def test_filesystem_change_verifier_detects_real_delete(tmp_path: Path):
    from hermes.tools.verifiers.specific import FilesystemChangeVerifier
    from hermes.tools.verifiers.context import VerifierContext

    target = tmp_path / "doomed.txt"
    target.write_text("x", encoding="utf-8")
    target.unlink()
    ctx = VerifierContext(
        tool_name="delete_path",
        tool_arguments={"path": str(target)},
        execution_success=True,
        execution_output={"path": str(target)},
        run_id="r1",
    )
    result = asyncio.run(FilesystemChangeVerifier().verify(ctx))
    assert result.status.value == "verified"


def test_filesystem_change_verifier_catches_liar_delete(tmp_path: Path):
    from hermes.tools.verifiers.specific import FilesystemChangeVerifier
    from hermes.tools.verifiers.context import VerifierContext

    target = tmp_path / "still_here.txt"
    target.write_text("x", encoding="utf-8")
    # Liar claims delete but file still exists.
    ctx = VerifierContext(
        tool_name="delete_path",
        tool_arguments={"path": str(target)},
        execution_success=True,
        execution_output={"path": str(target)},
        run_id="r1",
    )
    result = asyncio.run(FilesystemChangeVerifier().verify(ctx))
    assert result.status.value == "failed"
    assert "target_still_exists" in result.details.get("reason", "")
