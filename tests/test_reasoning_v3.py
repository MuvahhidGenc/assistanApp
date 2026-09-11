"""V3 Reasoning Runtime tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermes.capability import create_default_capability_registry
from hermes.execution_log import ExecutionLogStore
from hermes.execution_log.events import action_started_payload
from hermes.reasoning import (
    Action,
    Complete,
    Decision,
    DecisionKind,
    LlmReasoningClient,
    ObservationRequest,
    ReasoningPrompt,
    ReasoningReply,
    ReasoningRuntime,
    ReReason,
    UserQuestion,
)
from hermes.reasoning.decision import DecisionKind
from hermes.reasoning.transport import (
    HermesServerReasoningClient,
    _parse_decision_json,
)
from hermes.world_model import WorldModel, EvidenceRecord, EvidenceSource


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def world() -> WorldModel:
    return WorldModel()


@pytest.fixture
def log(tmp_path: Path) -> ExecutionLogStore:
    return ExecutionLogStore(path=tmp_path / "log.jsonl")


@pytest.fixture
def registry():
    return create_default_capability_registry()


class _StubClient:
    """Test double for `LlmReasoningClient`. Records calls, returns canned replies."""

    def __init__(self, replies: list[dict[str, Any]] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[ReasoningPrompt] = []

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        self.calls.append(prompt)
        if not self.replies:
            raise AssertionError("Stub client ran out of replies")
        next_reply = self.replies.pop(0)
        return ReasoningReply(decision_json=next_reply, raw_text=json.dumps(next_reply))


# ---------------------------------------------------------------------------
# Decision data model invariants
# ---------------------------------------------------------------------------


def test_action_decision_requires_action_payload():
    with pytest.raises(ValueError):
        Decision(kind=DecisionKind.ACTION, observation_request=ObservationRequest(observation_type="x"))


def test_observation_request_decision_requires_payload():
    with pytest.raises(ValueError):
        Decision(kind=DecisionKind.OBSERVATION_REQUEST, action=Action(capability="x"))


def test_user_question_decision_requires_payload():
    with pytest.raises(ValueError):
        Decision(kind=DecisionKind.USER_QUESTION, complete=Complete(summary="x"))


def test_complete_decision_requires_payload():
    with pytest.raises(ValueError):
        Decision(kind=DecisionKind.COMPLETE, action=Action(capability="x"))


def test_re_reason_decision_requires_payload():
    with pytest.raises(ValueError):
        Decision(kind=DecisionKind.RE_REASON, complete=Complete(summary="x"))


def test_factory_methods_produce_well_formed_decisions():
    decision = Decision.of_action(capability="filesystem.read", arguments={"path": "/x"})
    assert decision.kind is DecisionKind.ACTION
    assert decision.action is not None
    assert decision.action.capability == "filesystem.read"


def test_decision_repr_is_stable():
    """Decisions are frequently serialised; make sure the shape is plain."""
    decision = Decision.of_user_question("which file?", options=("a", "b"))
    assert decision.user_question is not None
    assert decision.user_question.options == ("a", "b")


# ---------------------------------------------------------------------------
# Prompt + parse boundaries
# ---------------------------------------------------------------------------


def test_parse_decision_json_accepts_well_formed():
    parsed = _parse_decision_json('{"kind":"complete","summary":"ok"}')
    assert parsed == {"kind": "complete", "summary": "ok"}


def test_parse_decision_json_strips_code_fence():
    parsed = _parse_decision_json("```json\n{\"kind\":\"action\",\"capability\":\"x\"}\n```")
    assert parsed["kind"] == "action"


def test_parse_decision_json_rejects_empty():
    with pytest.raises(ValueError):
        _parse_decision_json("")


def test_parse_decision_json_rejects_non_json():
    with pytest.raises(ValueError):
        _parse_decision_json("hello world")


def test_parse_decision_json_rejects_non_object():
    with pytest.raises(ValueError):
        _parse_decision_json("[1, 2, 3]")


@pytest.mark.asyncio
async def test_server_reasoning_client_repairs_one_non_json_reply():
    class _Server:
        def __init__(self):
            self.requests = []

        async def chat(self, request):
            self.requests.append(request)
            content = (
                "I cannot access that Windows path."
                if len(self.requests) == 1
                else (
                    '{"kind":"action","capability":"filesystem.list",'
                    '"arguments":{"path":"C:\\\\\\\\Temp"},'
                    '"required_capabilities":["filesystem.list"]}'
                )
            )
            return {"choices": [{"message": {"content": content}}]}

    server = _Server()
    client = HermesServerReasoningClient(server)
    prompt = ReasoningPrompt(
        user_message="list C:\\Temp",
        world_snapshot={},
        available_capabilities=(
            {
                "name": "filesystem.list",
                    "purpose": "List a directory",
                "risk_level": "read_only",
                    "side_effects": [],
                "input_schema": {
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
            },
        ),
        extra={"re_reason": "previous observation was stale"},
        correlation_id="turn_test",
        task_id="task_test",
        client_session_id="session_test",
    )

    reply = await client.reason(prompt)

    assert reply.decision_json["kind"] == "action"
    assert len(server.requests) == 2
    first_payload = json.loads(server.requests[0].message)
    assert first_payload["turn"]["re_reason"] == "previous observation was stale"
    repair = json.loads(server.requests[1].message)
    assert repair["protocol"].startswith("V3_RUNTIME_DECISION_REPAIR")
    assert repair["turn"] == first_payload["turn"]
    assert "original_request" not in repair


# ---------------------------------------------------------------------------
# ReasoningRuntime: prompt assembly + decision construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reason_calls_client_with_world_snapshot(world, log, registry):
    world.update_environment(active_window="notepad.exe")
    client = _StubClient(
        [
            {
                "kind": "user_question",
                "question": "what file?",
                "required_capabilities": [],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    decision = await runtime.reason(user_message="open the file", world_model=world)

    assert decision.kind is DecisionKind.USER_QUESTION
    assert decision.user_question is not None
    assert decision.user_question.question == "what file?"
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call.world_snapshot["environment"]["active_window"] == "notepad.exe"
    assert call.user_message == "open the file"
    # Capabilities summary is non-empty.
    assert len(call.available_capabilities) > 0


@pytest.mark.asyncio
async def test_model_capability_contract_has_schemas_but_no_tool_names(
    world, log, registry
):
    client = _StubClient(
        [
            {
                "kind": "complete",
                "summary": "done",
                "evidence_ids": [],
                "required_capabilities": [],
            }
        ]
    )
    runtime = ReasoningRuntime(
        client=client,
        capability_registry=registry,
        execution_log=log,
    )

    await runtime.reason(user_message="done", world_model=world)

    contracts = {
        item["name"]: item for item in client.calls[0].available_capabilities
    }
    write_contract = contracts["filesystem.write"]
    assert write_contract["input_schema"]["type"] == "object"
    assert "output_schema" in write_contract
    assert "default_tool" not in write_contract
    assert "allowed_overrides" not in write_contract


@pytest.mark.asyncio
async def test_reason_returns_action_decision(world, log, registry):
    client = _StubClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": "/tmp/x.txt", "content": "hi"},
                "rationale": "user asked for it",
                "required_capabilities": ["filesystem.write"],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    decision = await runtime.reason(user_message="write hi", world_model=world)
    assert decision.kind is DecisionKind.ACTION
    assert decision.action is not None
    assert decision.action.capability == "filesystem.write"
    assert decision.action.arguments == {"path": "/tmp/x.txt", "content": "hi"}


@pytest.mark.asyncio
async def test_reason_returns_observation_request(world, log, registry):
    client = _StubClient(
        [
            {
                "kind": "observation_request",
                    "observation_type": "screen.observe",
                "rationale": "need to see what's on screen",
                "required_capabilities": [],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    decision = await runtime.reason(user_message="look", world_model=world)
    assert decision.kind is DecisionKind.OBSERVATION_REQUEST
    assert decision.observation_request is not None
    assert decision.observation_request.observation_type == "screen.observe"


@pytest.mark.asyncio
async def test_reason_returns_complete(world, log, registry):
    world.record_evidence(
        EvidenceRecord.make(
            source=EvidenceSource.OBSERVATION,
            capability="filesystem.read",
            claim="observed",
        )
    )
    evidence_ids = [world.evidence[-1].evidence_id]
    client = _StubClient(
        [
            {
                "kind": "complete",
                "summary": "done",
                "evidence_ids": evidence_ids,
                "required_capabilities": [],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    decision = await runtime.reason(user_message="done", world_model=world)
    assert decision.kind is DecisionKind.COMPLETE
    assert decision.complete is not None
    assert decision.complete.summary == "done"
    assert decision.complete.evidence_ids == tuple(evidence_ids)


@pytest.mark.asyncio
async def test_reason_returns_re_reason(world, log, registry):
    client = _StubClient(
        [
            {
                "kind": "re_reason",
                "reason": "insufficient evidence",
                "required_capabilities": [],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    decision = await runtime.reason(user_message="again", world_model=world)
    assert decision.kind is DecisionKind.RE_REASON
    assert decision.re_reason is not None


@pytest.mark.asyncio
async def test_reason_rejects_unknown_kind(world, log, registry):
    client = _StubClient([{"kind": "magic", "required_capabilities": []}])
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    with pytest.raises(ValueError):
        await runtime.reason(user_message="x", world_model=world)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {
            "kind": "action",
            "capability": "",
            "arguments": {},
            "required_capabilities": [],
        },
        {
            "kind": "observation_request",
            "observation_type": "",
            "required_capabilities": [],
        },
        {
            "kind": "user_question",
            "question": "",
            "required_capabilities": [],
        },
        {
            "kind": "complete",
            "summary": "",
            "evidence_ids": [],
            "required_capabilities": [],
        },
    ],
)
async def test_reason_rejects_structurally_invalid_decisions(
    world, log, registry, reply
):
    runtime = ReasoningRuntime(
        client=_StubClient([reply]),
        capability_registry=registry,
        execution_log=log,
    )
    with pytest.raises(ValueError):
        await runtime.reason(user_message="x", world_model=world)


@pytest.mark.asyncio
async def test_re_reason_includes_reason_in_prompt(world, log, registry):
    client = _StubClient(
        [
            {
                "kind": "user_question",
                "question": "?",
                "required_capabilities": [],
            },
            {
                "kind": "action",
                "capability": "filesystem.read",
                "arguments": {"path": "/tmp/x"},
                "required_capabilities": ["filesystem.read"],
            },
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    await runtime.reason(user_message="x", world_model=world, correlation_id="corr-1")
    decision = await runtime.re_reason(
        user_message="x",
        world_model=world,
        correlation_id="corr-1",
        reason="observation was empty",
    )
    assert decision.kind is DecisionKind.ACTION
    assert len(client.calls) == 2
    assert client.calls[1].extra.get("re_reason") == "observation was empty"


@pytest.mark.asyncio
async def test_observation_request_then_action_loop(world, log, registry):
    """The runtime must be able to ask for evidence, then act on it."""
    client = _StubClient(
        [
            {
                "kind": "observation_request",
                "observation_type": "filesystem.list",
                "target": "/tmp",
                "required_capabilities": [],
            },
            {
                "kind": "action",
                "capability": "filesystem.read",
                "arguments": {"path": "/tmp/x"},
                "required_capabilities": ["filesystem.read"],
            },
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    first = await runtime.reason(user_message="find file", world_model=world)
    assert first.kind is DecisionKind.OBSERVATION_REQUEST
    second = await runtime.reason(user_message="find file", world_model=world)
    assert second.kind is DecisionKind.ACTION


@pytest.mark.asyncio
async def test_reasoning_loop_can_chain_re_reason_then_action(world, log, registry):
    """Continuous reasoning: re_reason first, then a concrete action."""
    client = _StubClient(
        [
            {
                "kind": "re_reason",
                "reason": "need more context",
                "required_capabilities": [],
            },
            {
                "kind": "action",
                "capability": "screen.observe",
                "arguments": {},
                "required_capabilities": ["screen.observe"],
            },
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    first = await runtime.reason(user_message="look", world_model=world)
    assert first.kind is DecisionKind.RE_REASON
    second = await runtime.reason(user_message="look", world_model=world)
    assert second.kind is DecisionKind.ACTION
    assert second.action is not None


@pytest.mark.asyncio
async def test_reason_correlation_filters_events(world, log, registry):
    log.append(action_started_payload("corr-1", "a1", "fs.read", "read_file", {}, "client"))
    log.append(action_started_payload("corr-2", "a2", "fs.write", "write_file", {}, "client"))
    client = _StubClient(
        [
            {
                "kind": "complete",
                "summary": "ok",
                "evidence_ids": [],
                "required_capabilities": [],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    await runtime.reason(user_message="x", world_model=world, correlation_id="corr-1")
    # Only events tagged corr-1 must be in the recent_events payload.
    payload = client.calls[0]
    for envelope in payload.recent_events:
        assert envelope["correlation_id"] == "corr-1"


# ---------------------------------------------------------------------------
# Anti-routing invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_does_not_inspect_user_language(world, log, registry):
    """The runtime does not regex on user_message — it passes it whole."""
    client = _StubClient(
        [
            {
                "kind": "user_question",
                "question": "?",
                "required_capabilities": [],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    await runtime.reason(user_message="lütfen dosyayı aç", world_model=world)
    call = client.calls[0]
    # The message is forwarded verbatim — no keyword manipulation.
    assert call.user_message == "lütfen dosyayı aç"


@pytest.mark.asyncio
async def test_runtime_does_not_call_security_layer(world, log, registry):
    """Defence-in-depth: runtime does not invoke Policy/Approval directly."""
    client = _StubClient(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": "/tmp/x", "content": "x"},
                "required_capabilities": ["filesystem.write"],
            }
        ]
    )
    runtime = ReasoningRuntime(client=client, capability_registry=registry, execution_log=log)
    decision = await runtime.reason(user_message="x", world_model=world)
    assert decision.action is not None
    # Action carries capability + rationale, not security/approval metadata.
    # Those concerns live in the orchestrator.
    assert not hasattr(decision.action, "approval_required")


def test_reasoning_modules_do_not_import_legacy_semantic_routing():
    import hermes.reasoning as pkg

    modules = [
        pkg.runtime.__file__,
        pkg.transport.__file__,
        pkg.decision.__file__,
    ]
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
    for path in modules:
        text = open(path, encoding="utf-8").read()
        for token in forbidden:
            assert token not in text, f"{path} must not reference {token!r}"

def test_scrub_payload_redacts_secrets_in_nested_structure():
    """``_scrub_payload`` recursively walks dicts, lists, and tuples.
    A malicious browser tool returning a page with a secret inside a
    nested dict must not leak through to the LLM prompt.
    """
    from hermes.reasoning.runtime import _scrub_payload

    payload = {
        "page": "the body of this page is clean text",
        "meta": {
            "url": "https://example.com",
            "headers": ["Authorization: Bearer sk-abcdefghijklmnop1234"],
        },
        "items": [
            {"text": "ok", "ok": True},
            "raw: api_key=AKIA1234567890ABCDEF",
        ],
    }
    out = _scrub_payload(payload)
    # All secret-shaped substrings are gone.
    text = str(out)
    assert "sk-abcdefghijklmnop1234" not in text
    assert "AKIA1234567890ABCDEF" not in text
    # Sanity: non-secret text is preserved.
    assert "the body of this page is clean text" in out["page"]
    assert out["meta"]["url"] == "https://example.com"
    assert out["items"][0]["ok"] is True
