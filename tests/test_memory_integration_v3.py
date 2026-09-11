"""V3 memory integration tests.

The V3 runtime is expected to use the three memory layers (session,
episodic, long-term) for the *past* view of the world. The World Model
holds the *current* view. Tests in this module assert the boundary
remains clean: memory is for recall, not for routing, and the
runtime never calls into a memory layer to make a decision.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.execution_log import ExecutionLogStore
from hermes.memory import (
    EpisodicMemory,
    LongTermMemory,
    SessionMemory,
)
from hermes.reasoning import ReasoningReply
from hermes.reasoning.transport import ReasoningPrompt
from hermes.runtime.bootstrap import build_v3_application
from hermes.world_model import WorldModel
from tests._approval_providers import approving_provider


class _StubLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


def test_world_model_and_memory_are_separate_objects():
    """A world model and a memory layer describe the same machine from
    different angles. The runtime must not conflate them."""
    wm = WorldModel()
    session = SessionMemory()
    episodic = EpisodicMemory()
    long_term = LongTermMemory()
    # The world model has no knowledge of past turns.
    assert wm.evidence == ()
    # The session memory has no knowledge of the world.
    assert session.turns == ()


def test_memory_layers_refuse_secrets():
    """Secrets must never be persisted into any memory layer."""
    episodic = EpisodicMemory()
    # A simple clear secret pattern that the scrubber catches.
    episodic.record(
        summary="Used password=hunter2 to log in",
        outcome="completed",
    )
    # The secret-like pattern is scrubbed before storage.
    assert "hunter2" not in episodic.records[0].summary
    assert "[REDACTED]" in episodic.records[0].summary

    long_term = LongTermMemory()
    with pytest.raises(ValueError):
        long_term.remember("api_key", "sk-deadbeef")
    long_term.remember("language", "tr-TR")
    fact = long_term.recall("language")
    assert fact is not None


@pytest.mark.asyncio
async def test_runtime_does_not_invoke_memory_for_decisions(tmp_path: Path):
    """Memory layers are pure data stores. The runtime must not import
    them in its decision path. We assert this by checking the import
    graph: the runtime modules do not import ``hermes.memory``.
    """
    import inspect

    from hermes.reasoning import runtime as reasoning_runtime
    from hermes.runtime import executor, orchestrator

    for module in (reasoning_runtime, executor, orchestrator):
        source = inspect.getsource(module)
        assert "hermes.memory" not in source, (
            f"{module.__name__} must not import hermes.memory in the decision path"
        )


def test_memory_and_world_model_dont_share_state_objects():
    """Memory and the World Model are independent data structures. A
    write to one must never affect the other.
    """
    wm = WorldModel()
    session = SessionMemory()
    wm.set_objective("write a file")
    assert wm.task.objective == "write a file"
    assert session.turns == ()
    session.record("user", "hello")
    assert session.turns[-1].content == "hello"
    assert wm.task.objective == "write a file"  # not affected

# ---------------------------------------------------------------------------
# Runtime integration tests (Memory → LLM prompt)
# ---------------------------------------------------------------------------


def test_runtime_memory_view_is_empty_when_no_memory():
    from hermes.reasoning import ReasoningRuntime
    from hermes.capability import create_default_capability_registry
    from hermes.execution_log import ExecutionLogStore
    from hermes.tools.registry import create_default_registry

    log_path = __import__("tempfile").NamedTemporaryFile(delete=False).name
    log = ExecutionLogStore(path=log_path)
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    runtime = ReasoningRuntime(
        client=lambda prompt: None,
        capability_registry=caps,
        execution_log=log,
        memory=None,
    )
    assert runtime._memory_view() == {}


@pytest.mark.asyncio
async def test_memory_injection_reaches_llm_prompt(tmp_path: Path):
    """When the runtime is wired with a memory facade, the LLM prompt
    carries a compact memory view. Secret-shaped values are scrubbed.
    """
    from hermes.memory import EpisodicRecord, LongTermFact
    from hermes.reasoning import ReasoningReply, ReasoningRuntime
    from hermes.reasoning.transport import ReasoningPrompt
    from hermes.capability import create_default_capability_registry
    from hermes.execution_log import ExecutionLogStore
    from hermes.tools.registry import create_default_registry
    from hermes.world_model import WorldModel

    log = ExecutionLogStore(path=tmp_path / "log.jsonl")
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)

    captured: list = []

    class _C:
        async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
            captured.append(prompt)
            return ReasoningReply(
                decision_json={"kind": "complete", "summary": "ok", "evidence_ids": []},
                raw_text="x",
            )

    class _EpisodicFacade:
        def query(self, *args, **kwargs):
            return [
                EpisodicRecord(
                    episode_id="ep_x",
                    summary="installed python 3.11",
                    outcome="completed",
                    capabilities=("application.install",),
                )
            ]

    class _LongTermFacade:
        def all_facts(self):
            return [
                LongTermFact(key="language", value="tr-TR"),
                LongTermFact(key="api_key", value="sk-deadbeefshouldberemoved"),
            ]

    class _Memory:
        @property
        def episodic(self):
            return _EpisodicFacade()

        @property
        def long_term(self):
            return _LongTermFacade()

    runtime = ReasoningRuntime(
        client=_C(),
        capability_registry=caps,
        execution_log=log,
        memory=_Memory(),
    )
    wm = WorldModel()
    wm.set_objective("do something")
    decision = await runtime.reason(user_message="hi", world_model=wm)
    assert decision.kind.value == "complete"
    prompt = captured[0]
    memory = prompt.extra.get("memory", {})
    assert "installed python 3.11" in memory["recent_episodes"][0]["summary"]
    keys = {fact["key"] for fact in memory["long_term_facts"]}
    assert "language" in keys
    assert "api_key" not in keys  # scrubbed
