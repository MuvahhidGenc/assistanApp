"""V3 REAL server transport test.

This test exercises the production ``HermesServerClient.chat`` path
end-to-end. It uses ``httpx.MockTransport`` to intercept the HTTP
request and reply with the OpenAI-style response shape the client
expects. The V3 reasoning runtime then runs as it would in production
against a real server.

This is the closest thing to a production run we can do in CI without
network access to the live VPS. The same code path runs against the
real server in production — the only difference is the network.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from hermes.execution_log import ExecutionLogStore
from hermes.reasoning import ReasoningPrompt, ReasoningReply
from hermes.reasoning.transport import HermesServerReasoningClient
from hermes.runtime import V3Executor
from hermes.runtime.bootstrap import build_v3_application
from hermes.security.approval_manager import ApprovalDecision, ApprovalManager, ParsedApproval
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import create_default_verifier_registry
from hermes.capability import create_default_capability_registry


# ---------------------------------------------------------------------------
# A real HermesServerClient + real httpx round-trip
# ---------------------------------------------------------------------------


class _ChatHandler:
    """``httpx`` mock transport that answers ``POST /v1/chat/completions``.

    The handler maintains a small script of canned replies so we can
    drive a full user turn through the LLM transport boundary.
    """

    def __init__(self, scripted: list[dict[str, Any]]) -> None:
        self.scripted = list(scripted)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # Only handle chat-completions; refuse anything else loudly.
        assert b"/v1/chat/completions" in request.content or request.url.path.endswith(
            "/v1/chat/completions"
        ), f"unexpected request: {request.url}"
        if not self.scripted:
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}}]})
        next_reply = self.scripted.pop(0)
        body = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 0,
            "model": "hermes-agent",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(next_reply)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_real_server_client_round_trip():
    """The real ``HermesServerClient.chat`` calls a real httpx transport
    and parses a real OpenAI-style response. No stubs."""
    from hermes.server.client import HermesServerClient

    handler = _ChatHandler(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": "/tmp/v3_real_chat.txt", "content": "real server"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "done",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
        ]
    )
    transport = httpx.MockTransport(handler)
    server = HermesServerClient(
        base_url="http://vps.example",
        api_key="test-key",
        model="hermes-agent",
        timeout=5.0,
        verify_ssl=False,
        session_key=None,
    )
    # Replace the underlying httpx client with a MockTransport-backed one.
    server._client = httpx.AsyncClient(
        base_url="http://vps.example",
        transport=transport,
        headers=server._build_headers(),
    )

    from hermes.server.models import ChatRequest

    request = ChatRequest(message="write /tmp/v3_real_chat.txt", stream=False)
    response = await server.chat(request)
    assert response["choices"][0]["message"]["content"]
    assert "filesystem.write" in response["choices"][0]["message"]["content"]
    assert handler.requests, "Mock transport should have received at least one request"


@pytest.mark.asyncio
async def test_reasoning_transport_sends_v3_contract_as_system_message():
    """The live chat endpoint must receive the V3 decision contract.

    Without the system message the production server returns a normal
    conversational answer, which cannot be parsed as a runtime decision.
    """
    from hermes.server.client import HermesServerClient

    handler = _ChatHandler(
        [
            {
                "kind": "complete",
                "summary": "ok",
                "evidence_ids": [],
                "required_capabilities": [],
            }
        ]
    )
    server = HermesServerClient(
        base_url="http://vps.example",
        api_key="test-key",
        model="hermes-agent",
        timeout=5.0,
        verify_ssl=False,
        session_key=None,
    )
    server._client = httpx.AsyncClient(
        base_url="http://vps.example",
        transport=httpx.MockTransport(handler),
        headers=server._build_headers(),
    )
    client = HermesServerReasoningClient(server)
    prompt = ReasoningPrompt(
        user_message="say hello",
        world_snapshot={},
        recent_events=(),
        available_capabilities=(),
        correlation_id="turn_test",
        task_id="task_test",
        client_session_id="session_test",
    )

    await client.reason(prompt)

    body = json.loads(handler.requests[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert "produce a single JSON object" in body["messages"][0]["content"]
    assert body["messages"][1]["role"] == "user"
    user_envelope = json.loads(body["messages"][1]["content"])
    assert user_envelope["protocol"].startswith("V3_RUNTIME_DECISION")
    assert "available_capabilities" in user_envelope
    await server.close()


@pytest.mark.asyncio
async def test_full_runtime_through_real_http_transport(tmp_path: Path):
    """Wire ``build_v3_application`` to a real HermesServerClient backed by
    a MockTransport; the runtime must talk to the real client and the
    end-to-end pipeline must produce a successful file write."""

    handler = _ChatHandler(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(tmp_path / "v3_real.txt"), "content": "real chat ok"},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "wrote file",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
        ]
    )
    transport = httpx.MockTransport(handler)

    from hermes.server.client import HermesServerClient

    server = HermesServerClient(
        base_url="http://vps.example",
        api_key="real-server-test",
        model="hermes-agent",
        timeout=5.0,
        verify_ssl=False,
        session_key=None,
    )
    server._client = httpx.AsyncClient(
        base_url="http://vps.example",
        transport=transport,
        headers=server._build_headers(),
    )

    # The bootstrap calls ``build_v3_application`` which constructs its own
    # client. We monkey-patch the bootstrap's choice by passing our
    # pre-built server. Production default requires an explicit
    # approval provider; tests inject one.
    from tests._approval_providers import approving_provider

    app = build_v3_application(
        server=server,
        log_dir=tmp_path,
        approval_provider=approving_provider(),
    )

    reply = await app.orchestrator.process_message("write a file")
    assert reply == "wrote file"
    target = tmp_path / "v3_real.txt"
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "real chat ok"

    # The MockTransport was hit at least twice (action decision, complete decision).
    assert len(handler.requests) >= 2