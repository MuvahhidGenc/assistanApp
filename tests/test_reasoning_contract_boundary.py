"""Fail-closed tests for the external reasoning trust boundary."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hermes.reasoning.transport import HermesServerReasoningClient, ReasoningPrompt
from hermes.reasoning.validation import (
    DecisionContractError,
    validate_decision_payload,
)
from hermes.runtime.bootstrap import build_v3_application


_CAPABILITIES = (
    {
        "name": "filesystem.write",
        "purpose": "Write a file",
        "risk_level": "low_risk",
        "side_effects": ["filesystem_write"],
        "input_schema": {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    },
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
    {
        "name": "document.create",
        "purpose": "Create a document",
        "risk_level": "low_risk",
        "side_effects": ["filesystem_write"],
        "input_schema": {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
    },
    {
        "name": "document.read",
        "purpose": "Read a document",
        "risk_level": "read_only",
        "side_effects": [],
        "input_schema": {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
    },
)


def _world(
    *,
    requirements: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "task": {
            "objective": "test objective",
            "requirements": requirements or [],
        },
        "evidence": evidence or [],
        "references": {},
        "environment": {},
    }


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "observation_request",
            "observation_type": "",
            "parameters": {},
            "required_capabilities": [],
        },
        {
            "kind": "observation_request",
            "parameters": {},
            "required_capabilities": [],
        },
        {
            "kind": "observation_request",
            "observation_type": "filesystem.unknown",
            "parameters": {},
            "required_capabilities": [],
        },
        {
            "kind": "action",
            "capability": "filesystem.write",
            "arguments": {"path": "x", "content": "y"},
        },
        {
            "kind": "action",
            "capability": "filesystem.write",
            "arguments": {"path": "x", "content": "y"},
            "required_capabilities": None,
        },
        {
            "kind": "action",
            "capability": "filesystem.write",
            "arguments": {"path": "x", "content": "y"},
            "required_capabilities": "filesystem.write",
        },
        {
            "kind": "action",
            "capability": "filesystem.unknown",
            "arguments": {},
            "required_capabilities": ["filesystem.unknown"],
        },
        {
            "kind": "action",
            "capability": "write_file",
            "arguments": {},
            "required_capabilities": ["write_file"],
        },
        {
            "kind": "complete",
            "evidence_ids": [],
            "required_capabilities": [],
        },
    ],
    ids=[
        "empty-observation-type",
        "missing-observation-type",
        "unknown-observation-type",
        "missing-required-capabilities",
        "null-required-capabilities",
        "wrong-required-capabilities-type",
        "unknown-capability",
        "tool-name-as-capability",
        "malformed-complete",
    ],
)
def test_invalid_external_decisions_are_rejected(payload):
    with pytest.raises(DecisionContractError):
        validate_decision_payload(
            payload,
            available_capabilities=_CAPABILITIES,
            world_snapshot=_world(),
            correlation_id="turn_current",
        )


def test_empty_required_capabilities_is_valid_when_no_capability_is_claimed():
    validate_decision_payload(
        {
            "kind": "user_question",
            "question": "Which file?",
            "options": [],
            "required_capabilities": [],
        },
        available_capabilities=_CAPABILITIES,
        world_snapshot=_world(),
    )


@pytest.mark.parametrize(
    "injected",
    [
        ["document.read"],
        ["document.create", "filesystem.list"],
    ],
    ids=["stale-requirement", "irrelevant-requirement-injection"],
)
def test_requirement_set_cannot_change_within_current_task(injected):
    world = _world(
        requirements=[
            {
                "capability": "document.create",
                "satisfied": False,
                "evidence": [],
            }
        ]
    )
    with pytest.raises(
        DecisionContractError,
        match="changed within the current task",
    ):
        validate_decision_payload(
            {
                "kind": "re_reason",
                "reason": "continue",
                "required_capabilities": injected,
            },
            available_capabilities=_CAPABILITIES,
            world_snapshot=world,
        )


@pytest.mark.parametrize(
    "evidence",
    [
        [],
        [
            {
                "evidence_id": "ev_fake",
                "source": "verifier",
                "capability": "document.create",
                "correlation_id": "turn_current",
                "data": {"status": "verified"},
            }
        ],
        [
            {
                "evidence_id": "ev_real",
                "source": "verifier",
                "capability": "document.create",
                "correlation_id": "turn_old",
                "data": {"status": "verified"},
            }
        ],
    ],
    ids=["fake-evidence", "unknown-evidence-id", "stale-evidence"],
)
def test_complete_rejects_fake_or_stale_evidence(evidence):
    evidence_id = (
        "ev_missing"
        if not evidence or evidence[0]["evidence_id"] == "ev_fake"
        else evidence[0]["evidence_id"]
    )
    with pytest.raises(DecisionContractError):
        validate_decision_payload(
            {
                "kind": "complete",
                "summary": "done",
                "evidence_ids": [evidence_id],
                "required_capabilities": [],
            },
            available_capabilities=_CAPABILITIES,
            world_snapshot=_world(evidence=evidence),
            correlation_id="turn_current",
        )


def test_complete_accepts_current_verified_requirement_evidence():
    evidence = {
        "evidence_id": "ev_current",
        "source": "verifier",
        "capability": "document.create",
        "correlation_id": "turn_current",
        "data": {"status": "verified"},
    }
    validate_decision_payload(
        {
            "kind": "complete",
            "summary": "document created",
            "evidence_ids": ["ev_current"],
            "required_capabilities": ["document.create"],
        },
        available_capabilities=_CAPABILITIES,
        world_snapshot=_world(
            requirements=[
                {
                    "capability": "document.create",
                    "satisfied": True,
                    "evidence": ["ev_current"],
                }
            ],
            evidence=[evidence],
        ),
        correlation_id="turn_current",
    )


@pytest.mark.asyncio
async def test_structural_contract_failure_gets_one_controlled_repair():
    class Server:
        def __init__(self) -> None:
            self.requests = []

        async def chat(self, request):
            self.requests.append(request)
            content = (
                json.dumps(
                    {
                        "kind": "observation_request",
                        "observation_type": "",
                        "parameters": {},
                        "required_capabilities": [],
                    }
                )
                if len(self.requests) == 1
                else json.dumps(
                    {
                        "kind": "observation_request",
                        "observation_type": "filesystem.list",
                        "target": "C:\\Temp",
                        "parameters": {},
                        "required_capabilities": [],
                    }
                )
            )
            return {"choices": [{"message": {"content": content}}]}

    server = Server()
    reply = await HermesServerReasoningClient(server).reason(
        ReasoningPrompt(
            user_message="list the folder",
            world_snapshot=_world(),
            available_capabilities=_CAPABILITIES,
            correlation_id="turn_test",
            task_id="task_test",
            client_session_id="session_test",
        )
    )

    assert reply.decision_json["observation_type"] == "filesystem.list"
    assert len(server.requests) == 2
    repair = json.loads(server.requests[1].message)
    assert "observation_type" in repair["repair"]["validation_error"]
    assert repair["turn"]["correlation_id"] == "turn_test"
    assert "original_request" not in repair


@pytest.mark.asyncio
async def test_malformed_json_after_repair_fails_closed_in_application(tmp_path):
    class Server:
        async def chat(self, request):
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"kind":"complete",""summary":"done"}'
                        }
                    }
                ]
            }

    app = build_v3_application(server=Server(), log_dir=tmp_path)
    reply = await app.process_message("test")

    assert reply.startswith("Reasoning failed:")
    assert app.world_model.task.status == "failed"
    assert app.world_model.task.status != "complete"
