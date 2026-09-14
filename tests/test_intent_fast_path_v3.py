"""Tests for the local fast path, conversation history and prompt improvements."""

from __future__ import annotations

import json
import sys
import pytest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hermes.capability import create_default_capability_registry
from hermes.execution_log import ExecutionLogStore
from hermes.reasoning.decision import Decision, DecisionKind
from hermes.reasoning.fast_path import FastPathPlanner
from hermes.reasoning.runtime import ReasoningRuntime
from hermes.reasoning.transport import (
    ReasoningPrompt,
    ReasoningReply,
    _build_system_prompt,
    _build_user_payload,
)
from hermes.tools.registry import create_default_registry
from hermes.world_model import WorldModel


@pytest.fixture()
def _registry():
    return create_default_capability_registry(create_default_registry())


def _make_planner(tool_registry=None):
    return FastPathPlanner(tool_registry=tool_registry or create_default_registry())


# ---- planner: core capability mapping -----------------------------------

def test_planner_maps_google_to_browser_navigate():
    p = _make_planner()
    d = p.resolve("google'u ac", WorldModel())
    assert d is not None
    assert d.kind is DecisionKind.ACTION
    assert d.action.capability == "browser.navigate"
    assert d.action.arguments["url"] == "https://www.google.com"


def test_planner_maps_tolerant_short_message_google_161():
    """'google ı' (broken Turkish, no verb) resolves without an LLM call."""
    p = _make_planner()
    d = p.resolve("google ı", WorldModel())
    assert d is not None
    assert d.kind is DecisionKind.ACTION
    assert d.action.capability == "browser.navigate"
    assert d.action.arguments["url"] == "https://www.google.com"


def test_planner_maps_tolerant_chrome_without_verb():
    p = _make_planner()
    d = p.resolve("chrome", WorldModel())
    assert d is not None
    assert d.kind is DecisionKind.ACTION
    assert d.action.capability == "application.open"
    assert d.action.arguments["app"] == "chrome"


def test_planner_maps_volume_level():
    p = _make_planner()
    d = p.resolve("sesi 50 yap", WorldModel())
    assert d is not None
    assert d.kind is DecisionKind.ACTION
    assert d.action.capability == "system.configure"
    assert d.action.arguments == {"action": "set", "level": 50}


def test_planner_maps_volume_mute():
    p = _make_planner()
    d = p.resolve("sustur", WorldModel())
    assert d is not None
    assert d.kind is DecisionKind.ACTION
    assert d.action.capability == "system.configure"
    assert d.action.arguments == {"action": "mute"}


def test_planner_maps_noteepad_open():
    p = _make_planner()
    d = p.resolve("not defterini ac", WorldModel())
    assert d is not None
    assert d.kind is DecisionKind.ACTION
    assert d.action.capability == "application.open"
    assert d.action.arguments["app"] == "notepad"


def test_planner_returns_none_for_unmatched():
    p = _make_planner()
    d = p.resolve("nukleer fizik anlat", WorldModel())
    assert d is None


def test_planner_ambiguous_gives_question():
    """'dosyanin icerigini oku' without file context triggers a user question."""
    p = _make_planner()
    d = p.resolve("dosyanin icerigini oku", WorldModel())
    assert d is not None
    assert d.kind is DecisionKind.USER_QUESTION
    assert len(d.user_question.question) > 5


# ---- runtime: fast path gating ------------------------------------------

class _FakeClient:
    def __init__(self, reply: ReasoningReply | None = None) -> None:
        self.calls: list[str] = []
        self._reply = reply or ReasoningReply(
            decision_json={"kind": "complete", "summary": "test-done"}
        )

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(prompt.user_message)
        return self._reply


def _build_reasoning_fast(runtime: ReasoningRuntime, *, fast_path_eligible: bool = True):
    world = WorldModel()
    import asyncio
    return asyncio.run(
        runtime.reason(
            user_message="google'u ac",
            world_model=world,
            correlation_id="run_test",
            fast_path_eligible=fast_path_eligible,
        )
    )


def test_runtime_fast_path_skips_llm_when_eligible():
    cap_reg = create_default_capability_registry(create_default_registry())
    log = ExecutionLogStore(path=Path(__file__).resolve().parent / "_test_exec.jsonl")
    client = _FakeClient()
    runtime = ReasoningRuntime(
        client=client,
        capability_registry=cap_reg,
        execution_log=log,
        fast_path_enabled=True,
        tool_registry=create_default_registry(),
    )
    d = _build_reasoning_fast(runtime, fast_path_eligible=True)
    assert d.kind is DecisionKind.ACTION
    assert d.action.capability == "browser.navigate"
    assert len(client.calls) == 0, "LLM should not be called when fast path fires"


def test_runtime_fast_path_off_calls_llm():
    cap_reg = create_default_capability_registry(create_default_registry())
    log = ExecutionLogStore(path=Path(__file__).resolve().parent / "_test_exec2.jsonl")
    client = _FakeClient()
    runtime = ReasoningRuntime(
        client=client,
        capability_registry=cap_reg,
        execution_log=log,
        fast_path_enabled=False,
    )
    d = _build_reasoning_fast(runtime, fast_path_eligible=True)
    assert d.kind is DecisionKind.COMPLETE
    assert len(client.calls) == 1, "LLM must be called when fast path is disabled"


def test_runtime_fast_path_not_eligible_calls_llm():
    cap_reg = create_default_capability_registry(create_default_registry())
    log = ExecutionLogStore(path=Path(__file__).resolve().parent / "_test_exec3.jsonl")
    client = _FakeClient()
    runtime = ReasoningRuntime(
        client=client,
        capability_registry=cap_reg,
        execution_log=log,
        fast_path_enabled=True,
        tool_registry=create_default_registry(),
    )
    d = _build_reasoning_fast(runtime, fast_path_eligible=False)
    assert d.kind is DecisionKind.COMPLETE
    assert len(client.calls) == 1


# ---- transport: conversation history in payload ---------------------------

def test_payload_includes_conversation_history():
    prompt = ReasoningPrompt(
        user_message="google ac",
        world_snapshot={},
        available_capabilities=(),
        conversation_history=(
            {"role": "user", "content": "selam"},
            {"role": "assistant", "content": "merhaba nasil yardimci olayim"},
        ),
    )
    payload = json.loads(_build_user_payload(prompt))
    history = payload.get("conversation_history")
    assert isinstance(history, list)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["content"].startswith("merhaba")


def test_payload_omits_empty_history():
    prompt = ReasoningPrompt(
        user_message="test",
        world_snapshot={},
        available_capabilities=(),
        conversation_history=(),
    )
    payload = json.loads(_build_user_payload(prompt))
    assert "conversation_history" not in payload


# ---- transport: Turkish guidance in system prompt -------------------------

def test_system_prompt_contains_turkish_guidance():
    prompt = ReasoningPrompt(
        user_message="test",
        world_snapshot={},
        available_capabilities=(),
    )
    sp = _build_system_prompt(prompt)
    assert "google'u ac" in sp
    assert "browser.navigate" in sp
    assert "clarifying question" in sp.lower()
    assert "Cok adimli" in sp or "cok adimli" in sp.lower() or "bariz" in sp.lower()
