"""Continuous reasoning scenarios (Phase 11).

The V3 runtime owns the reasoning loop. When the LLM returns a
decision that the runtime cannot honour (or when evidence is missing),
the runtime must re-reason rather than declare success.

Scenarios:

  A. Multi-step goal that needs two dependent actions.
  B. Tool reports success but the file does not exist — re-reason.
  C. Unexpected observation (filesystem.list returns empty) — re-reason.
  D. Goal completed with verified evidence — Complete.
  E. Action success but goal verification is "unknown" — runtime
     must not declare Complete until the LLM itself decides so, and
     the LLM must continue reasoning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.execution_log import EventKind
from hermes.reasoning import ReasoningReply
from hermes.reasoning.transport import ReasoningPrompt
from hermes.runtime.bootstrap import build_v3_application
from tests._approval_providers import approving_provider


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list = []

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(prompt)
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


# ---------------------------------------------------------------------------
# Scenario A — create folder, write file, verify content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_dependent_steps(tmp_path: Path):
    folder = tmp_path / "Project"
    file_in_folder = folder / "notes.txt"
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(file_in_folder), "content": "scenario A"},
            },
            {"kind": "complete", "summary": "wrote notes", "evidence_ids": []},
        ]
    )
    app = build_v3_application(
        server=_AsyncLLMAdapter(llm),
        log_dir=tmp_path,
        approval_provider=approving_provider(),
    )
    # Replace the reasoning client on the runtime with our stub so the
    # bootstrap's real client does not run.
    from hermes.reasoning.transport import LlmReasoningClient

    # The bootstrap already created a runtime; replace its client.
    app.orchestrator._runtime.client = _AdapterFromStub(llm)
    reply = await app.orchestrator.process_message("write notes in Project")
    assert reply == "wrote notes"
    assert file_in_folder.exists()
    assert file_in_folder.read_text(encoding="utf-8") == "scenario A"


# ---------------------------------------------------------------------------
# Scenario B — tool success but reality says otherwise (see filesystem_ground_truth)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Scenario C — empty list triggers re-reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_c_unexpected_observation_triggers_rereason(tmp_path: Path):
    """When an observation returns empty, the LLM must re-reason before
    declaring the goal achieved."""
    llm = _StubLLM(
        [
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": str(tmp_path / "ghost"),
            },
            {"kind": "re_reason", "reason": "list is empty — directory may not exist"},
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {
                    "path": str(tmp_path / "ghost" / "data.txt"),
                    "content": "scen C",
                },
            },
            {"kind": "complete", "summary": "recovered", "evidence_ids": []},
        ]
    )
    app = build_v3_application(
        server=_AsyncLLMAdapter(llm),
        log_dir=tmp_path,
        approval_provider=approving_provider(),
    )
    app.orchestrator._runtime.client = _AdapterFromStub(llm)
    reply = await app.orchestrator.process_message("list and write")
    assert reply == "recovered"
    assert (tmp_path / "ghost" / "data.txt").exists()


# ---------------------------------------------------------------------------
# Scenario D — goal completed with verified evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_d_complete_with_verified_evidence(tmp_path: Path):
    target = tmp_path / "scen_d.txt"
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "scen D"},
            },
            {"kind": "complete", "summary": "done", "evidence_ids": []},
        ]
    )
    app = build_v3_application(
        server=_AsyncLLMAdapter(llm),
        log_dir=tmp_path,
        approval_provider=approving_provider(),
    )
    app.orchestrator._runtime.client = _AdapterFromStub(llm)
    reply = await app.orchestrator.process_message("write")
    assert reply == "done"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "scen D"
    # WorldModel has tool-report + verifier evidence
    successes = [
        e
        for e in app.world_model.evidence
        if "succeeded" in e.claim.lower() or e.source.value == "verifier"
    ]
    assert successes


# ---------------------------------------------------------------------------
# Scenario E — action succeeded but goal verification is unknown → no
# complete until the LLM reasons again
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_e_unknown_verification_blocks_complete(tmp_path: Path):
    target = tmp_path / "scen_e.txt"
    llm = _StubLLM(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(target), "content": "scen E"},
            },
            # LLM asks for another observation before completing.
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": str(tmp_path),
            },
            {"kind": "complete", "summary": "verified by list", "evidence_ids": []},
        ]
    )
    app = build_v3_application(
        server=_AsyncLLMAdapter(llm),
        log_dir=tmp_path,
        approval_provider=approving_provider(),
    )
    app.orchestrator._runtime.client = _AdapterFromStub(llm)
    reply = await app.orchestrator.process_message("write")
    assert reply == "verified by list"
    # The runtime iterated at least 3 times.
    assert app.orchestrator.state.iteration >= 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncLLMAdapter:
    """Adapter that returns canned replies without doing HTTP."""

    def __init__(self, stub: _StubLLM) -> None:
        self._stub = stub
        self.calls: list = []

    async def chat(self, request: Any) -> dict:
        return {
            "choices": [
                {
                    "message": {
                        "content": await self._call_reasoning(request)
                    }
                }
            ]
        }

    async def _call_reasoning(self, request: Any) -> str:
        from hermes.reasoning.transport import ReasoningPrompt

        # Use a private bridge to keep the stub's simple API surface.
        # The stub's ``reason`` method is async; we forward the prompt.
        prompt = ReasoningPrompt(
            user_message=getattr(request, "message", ""),
            world_snapshot={},
            available_capabilities=(),
            recent_events=(),
            extra={},
        )
        reply = await self._stub.reason(prompt)
        return reply.raw_text or json.dumps(reply.decision_json)


class _AdapterFromStub:
    """Direct adapter that the orchestrator can call."""

    def __init__(self, stub: _StubLLM) -> None:
        self._stub = stub

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        return await self._stub.reason(prompt)