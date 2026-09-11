"""Regression tests for the Client -> VPS reasoning context contract."""

from __future__ import annotations

import json

from hermes.reasoning.transport import (
    ReasoningPrompt,
    _build_repair_payload,
    _build_user_payload,
)


def _prompt() -> ReasoningPrompt:
    return ReasoningPrompt(
        user_message="Create the document in it.",
        correlation_id="turn_current",
        task_id="task_current",
        client_session_id="session_current",
        recent_events=(
            {"correlation_id": "turn_current", "kind": "action_finished"},
        ),
        available_capabilities=(
            {
                "name": "document.create",
                "purpose": "Create a document",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "verification": {"method": "document_format"},
                "risk_level": "normal_modification",
                "side_effects": ["filesystem_write"],
            },
        ),
        world_snapshot={
            "task": {
                "objective": "Create the document in the verified folder.",
                "status": "in_progress",
                "requirements": [
                    {
                        "capability": "document.create",
                        "satisfied": True,
                        "evidence": ["ev_current_verified"],
                    }
                ],
                "uncertainty": [],
                "relevant_context": {},
            },
            "environment": {
                "extra": {
                    "known_folders": {
                        "desktop": "C:\\Users\\Tester\\Desktop",
                    }
                }
            },
            "references": {
                "last_folder": {
                    "key": "last_folder",
                    "kind": "folder",
                    "value": "C:\\Users\\Tester\\Desktop\\deneme12",
                    "provenance": "ev_old_verified",
                    "extra": {"verification_status": "verified"},
                },
                "unverified_file": {
                    "key": "unverified_file",
                    "kind": "file",
                    "value": "C:\\unsafe.txt",
                    "provenance": "ev_unverified",
                    "extra": {"verification_status": "unknown"},
                },
            },
            "evidence": [
                {
                    "evidence_id": "ev_old_verified",
                    "source": "verifier",
                    "capability": "filesystem.write",
                    "claim": "folder verified",
                    "correlation_id": "turn_old",
                    "data": {"status": "verified"},
                },
                {
                    "evidence_id": "ev_current_observation",
                    "source": "observation",
                    "capability": "document.create",
                    "claim": "document observed",
                    "correlation_id": "turn_current",
                    "data": {"exists": True},
                },
                {
                    "evidence_id": "ev_current_verified",
                    "source": "verifier",
                    "capability": "document.create",
                    "claim": "document verified",
                    "correlation_id": "turn_current",
                    "data": {"status": "verified"},
                },
            ],
        },
        extra={
            "memory": {
                "recent_episodes": [
                    {
                        "summary": "Created a verified folder",
                        "outcome": "completed",
                        "capabilities": ["filesystem.write"],
                    }
                ],
                "long_term_facts": [{"key": "language", "value": "tr-TR"}],
                "session_turns": [
                    {
                        "role": "assistant",
                        "content": 'Reasoning failed: {"kind":""}',
                    },
                    {
                        "role": "assistant",
                        "content": "stale evidence rejection ev_old",
                    },
                ],
            }
        },
    )


def test_current_turn_identity_and_task_are_explicit():
    payload = json.loads(_build_user_payload(_prompt()))

    assert payload["turn"] == {
        "client_session_id": "session_current",
        "task_id": "task_current",
        "correlation_id": "turn_current",
        "user_message": "Create the document in it.",
        "objective": "Create the document in the verified folder.",
        "status": "in_progress",
        "current_requirements": [
            {
                "capability": "document.create",
                "satisfied": True,
                "evidence": ["ev_current_verified"],
            }
        ],
        "uncertainty": [],
        "relevant_context": {},
        "recent_events": [
            {"correlation_id": "turn_current", "kind": "action_finished"}
        ],
        "re_reason": "",
    }


def test_current_and_historical_evidence_are_separate():
    payload = json.loads(_build_user_payload(_prompt()))

    assert {
        item["evidence_id"] for item in payload["current_evidence"]
    } == {"ev_current_observation", "ev_current_verified"}
    historical = payload["historical_context"]["historical_evidence_summaries"]
    assert [item["evidence_id"] for item in historical] == ["ev_old_verified"]
    assert historical[0]["completion_eligible"] is False


def test_completion_evidence_contains_only_current_verified_requirement_evidence():
    payload = json.loads(_build_user_payload(_prompt()))

    assert payload["completion_eligible_evidence_ids"] == [
        "ev_current_verified"
    ]
    assert "ev_old_verified" not in payload["completion_eligible_evidence_ids"]
    assert "ev_current_observation" not in payload[
        "completion_eligible_evidence_ids"
    ]


def test_only_verified_references_are_present_with_provenance_correlation():
    payload = json.loads(_build_user_payload(_prompt()))

    assert set(payload["verified_references"]) == {"last_folder"}
    reference = payload["verified_references"]["last_folder"]
    assert reference["value"] == "C:\\Users\\Tester\\Desktop\\deneme12"
    assert reference["provenance"] == "ev_old_verified"
    assert reference["provenance_correlation_id"] == "turn_old"


def test_reasoning_presentation_omits_raw_session_failures():
    payload = json.loads(_build_user_payload(_prompt()))
    encoded = json.dumps(payload, ensure_ascii=False)

    assert "session_turns" not in encoded
    assert "Reasoning failed" not in encoded
    assert "stale evidence rejection" not in encoded
    assert payload["historical_context"]["previous_successful_tasks"]
    assert payload["historical_context"]["relevant_historical_facts"] == [
        {"key": "language", "value": "tr-TR"}
    ]


def test_repair_reuses_structured_context_without_escaped_original_request():
    original = _build_user_payload(_prompt())
    repair = json.loads(
        _build_repair_payload(
            original,
            '{"kind":"complete",""summary":"broken"}',
            "invalid JSON",
        )
    )

    assert repair["turn"]["correlation_id"] == "turn_current"
    assert repair["verified_references"]["last_folder"]["kind"] == "folder"
    assert repair["current_evidence"]
    assert "original_request" not in repair
    assert repair["repair"]["validation_error"] == "invalid JSON"


def test_capability_contract_and_windows_desktop_are_preserved():
    payload = json.loads(_build_user_payload(_prompt()))

    assert payload["environment"]["extra"]["known_folders"]["desktop"] == (
        "C:\\Users\\Tester\\Desktop"
    )
    capability = payload["available_capabilities"][0]
    assert {
        "name",
        "purpose",
        "input_schema",
        "output_schema",
        "verification",
    } <= set(capability)
