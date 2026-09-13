"""Reasoning transport — the boundary between runtime and server-side LLM.

The runtime asks the LLM "what next?" through `LlmReasoningClient`. The
client constructs a `ReasoningPrompt`, hands it to whatever serves the
model, and parses the structured reply into a `ReasoningReply`.

The transport does **not**:

  * decide anything — the LLM does,
  * cache the LLM's answer beyond a single call,
  * retry on its own — the runtime owns retry policy,
  * own a session — the server (HermesServerClient) does that.

The default implementation wraps `HermesServerClient.chat` (the V2
transport). It is intentionally thin so swapping the server backend
later does not require changing the runtime.
"""

from __future__ import annotations

import json as _json_mod
from dataclasses import dataclass, field
from typing import Any, Protocol

from hermes.capability import (
    CapabilityContract,
    CapabilityRegistry,
)
from hermes.execution_log import ExecutionLogStore
from hermes.world_model import WorldModel


@dataclass(frozen=True)
class ReasoningPrompt:
    """Inputs to the reasoning call.

    The runtime fills these from the live World Model + Execution Log; the
    LLM never sees raw user language or arbitrary conversation history.
    """

    user_message: str
    world_snapshot: dict[str, Any]
    available_capabilities: tuple[dict[str, Any], ...]
    recent_events: tuple[dict[str, Any], ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    task_id: str = ""
    client_session_id: str = ""


@dataclass(frozen=True)
class ReasoningReply:
    """Structured LLM reply.

    The reply is *always* parsed from JSON. The transport never silently
    ignores malformed output — it raises so the runtime can decide whether
    to ask the user or re-reason.
    """

    decision_json: dict[str, Any]
    raw_text: str = ""


class LlmReasoningClient(Protocol):
    """Pluggable interface to the server-side LLM."""

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply: ...


# ---------------------------------------------------------------------------
# Hermes server-backed client
# ---------------------------------------------------------------------------


class HermesServerReasoningClient:
    """Default reasoning client. Wraps `HermesServerClient.chat`.

    The transport is intentionally small: it asks the server to produce a
    JSON decision in the schema the runtime understands. Anything else
    is a parse error, raised so the runtime can decide what to do.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    async def _call_chat(self, request: Any) -> str:
        import asyncio as _aio

        response = await _aio.wait_for(
            self._server.chat(request), timeout=60.0
        )
        return _extract_text(response)

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        from hermes.server.models import ChatRequest
        from hermes.reasoning.validation import (
            DecisionContractError,
            validate_decision_payload,
        )

        system_prompt = _build_system_prompt(prompt)
        user_payload = _build_user_payload(prompt)
        request = ChatRequest(
            message=user_payload,
            system=system_prompt,
            stream=False,
            response_format={"type": "json_object"},
        )
        text = await self._call_chat(request)

        def _validate(text_: str) -> ReasoningReply:
            parsed = _parse_decision_json(text_)
            validate_decision_payload(
                parsed,
                available_capabilities=prompt.available_capabilities,
                world_snapshot=prompt.world_snapshot,
                correlation_id=prompt.correlation_id,
                check_requirement_transition=False,
                check_input_schema=False,
                require_required_capabilities=False,
            )
            return ReasoningReply(decision_json=parsed, raw_text=text_)

        # First attempt: parse + fail-closed contract validation. Any
        # non-conforming answer (malformed JSON or a structurally invalid
        # decision) gets ONE bounded repair call.
        try:
            return _validate(text)
        except (ValueError, DecisionContractError) as exc:
            validation_error = str(exc)

        # One-shot repair: re-sends the original structured context with a
        # bounded repair instruction. The server may not recover second time;
        # a second failure is fatal so callers can fail closed.
        repair_msg = _build_repair_payload(
            user_payload, text, validation_error
        )
        repair_request = ChatRequest(
            message=repair_msg,
            system=(
                "Repair protocol: convert the attached bad LLM reply into a "
                "single JSON object matching the V3 runtime decision schema. "
                "Return ONLY the JSON object — no prose, no markdown fences."
            ),
            stream=False,
            response_format={"type": "json_object"},
        )
        repair_text = await self._call_chat(repair_request)
        try:
            return _validate(repair_text)
        except (ValueError, DecisionContractError) as inner:
            raise ValueError(
                f"reasoning repair failed: {inner}"
            ) from inner


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _capability_summary(contract: CapabilityContract) -> dict[str, Any]:
    return {
        "name": contract.name,
        "purpose": contract.purpose,
        "risk_level": contract.risk_level.value,
        "side_effects": [s.value for s in contract.side_effects],
        "observation_value": list(contract.observation_value),
        "known_limitations": list(contract.known_limitations),
        "preconditions": list(contract.preconditions),
    }


def _build_system_prompt(prompt: ReasoningPrompt) -> str:
    caps_lines = [
        f"- {c['name']}: {c['purpose']} (risk={c['risk_level']}, "
        f"side_effects={','.join(c['side_effects']) or 'none'})"
        for c in prompt.available_capabilities
    ]
    caps_text = "\n".join(caps_lines) if caps_lines else "(none)"
    return (
        "You are the reasoning engine of a Windows desktop agent. The user "
        "has sent a message; you must produce a single JSON object "
        "describing your next move. Pick exactly one of:\n"
        "  - action: call a capability\n"
        "  - observation_request: gather read-only evidence first\n"
        "  - user_question: ask the user to disambiguate\n"
        "  - complete: the objective is met (with evidence)\n"
        "  - re_reason: call me again with more context\n"
        "Never propose a tool name directly — refer to a capability. Never "
        "claim success without evidence; use observation_request until you "
        "have verified what you need. If reality contradicts the goal, do "
        "not declare complete; re-reason.\n"
        "Available capabilities:\n"
        f"{caps_text}\n"
        "JSON schema: {\"kind\": str, \"capability\": str?, \"arguments\": "
        "object?, \"rationale\": str?, \"expected_observation\": str?, "
        "\"observation_type\": str?, \"target\": str?, \"question\": str?, "
        "\"options\": list?, \"summary\": str?, \"evidence_ids\": list?, "
        "\"reason\": str?}. No extra prose."
    )


def _build_user_payload(prompt: ReasoningPrompt) -> str:
    snapshot = prompt.world_snapshot
    if not isinstance(snapshot, dict):
        snapshot = {}

    task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
    raw_requirements = task.get("requirements")
    if not isinstance(raw_requirements, list):
        raw_requirements = []
    current_requirements = [
        {
            "capability": str(item.get("capability") or "").strip(),
            "satisfied": bool(item.get("satisfied", False)),
            "evidence": [
                str(value)
                for value in (item.get("evidence") or [])
                if isinstance(value, (str, int))
            ],
        }
        for item in raw_requirements
        if isinstance(item, dict)
    ]

    evidence_list = snapshot.get("evidence")
    if not isinstance(evidence_list, list):
        evidence_list = []
    turn_correlation = str(prompt.correlation_id or "").strip()
    if turn_correlation:
        current_evidence = [
            e for e in evidence_list
            if isinstance(e, dict)
            and str(e.get("correlation_id", "") or "").strip() == turn_correlation
        ]
    else:
        current_evidence = list(evidence_list)

    current_ids = {
        str(e.get("evidence_id") or "") for e in current_evidence
        if isinstance(e, dict)
    }
    historical_evidence_summaries = [
        {
            "evidence_id": str(e.get("evidence_id") or ""),
            "capability": str(e.get("capability") or ""),
            "source": str(e.get("source") or ""),
            "claim": str(e.get("claim") or ""),
            "completion_eligible": False,
        }
        for e in evidence_list
        if isinstance(e, dict) and str(e.get("evidence_id") or "") not in current_ids
    ]

    requirement_evidence_ids = [
        str(value)
        for item in raw_requirements
        if isinstance(item, dict)
        for value in (item.get("evidence") or [])
        if isinstance(value, (str, int))
    ]
    completion_eligible_evidence_ids = [
        eid for eid in requirement_evidence_ids if eid in current_ids
    ]

    reference_values = snapshot.get("references")
    if not isinstance(reference_values, dict):
        reference_values = {}
    evidence_by_id: dict[str, Any] = {}
    for evidence in evidence_list:
        if isinstance(evidence, dict) and str(evidence.get("evidence_id") or ""):
            evidence_by_id[str(evidence.get("evidence_id"))] = evidence
    verified_references: dict[str, Any] = {}
    for key, reference in reference_values.items():
        if not isinstance(reference, dict):
            continue
        extra = reference.get("extra")
        extra = extra if isinstance(extra, dict) else {}
        verification_status = extra.get("verification_status")
        if (verification_status is not None and verification_status != "verified") or (
            verification_status is None and reference.get("verified") is not True
        ):
            continue
        entry = dict(reference)
        provenance = str(reference.get("provenance") or "").strip()
        provenance_correlation_id = ""
        if provenance:
            matching = evidence_by_id.get(provenance)
            if matching is not None:
                provenance_correlation_id = str(
                    matching.get("correlation_id") or ""
                ).strip()
        entry["provenance_correlation_id"] = provenance_correlation_id
        verified_references[key] = entry

    historical_context: dict[str, Any] = {
        "historical_evidence_summaries": historical_evidence_summaries,
        "previous_successful_tasks": [],
        "relevant_historical_facts": [],
    }
    extra = prompt.extra if isinstance(prompt.extra, dict) else {}
    memory_view = extra.get("memory")
    if isinstance(memory_view, dict):
        episodes = memory_view.get("recent_episodes")
        if isinstance(episodes, list):
            historical_context["previous_successful_tasks"] = [
                {
                    "summary": str(ep.get("summary") or ""),
                    "outcome": str(ep.get("outcome") or ""),
                }
                for ep in episodes
                if isinstance(ep, dict)
                and ep.get("outcome") in ("success", "completed")
            ]
        facts = memory_view.get("long_term_facts")
        if isinstance(facts, list):
            historical_context["relevant_historical_facts"] = list(facts)

    turn = {
        "client_session_id": str(prompt.client_session_id or ""),
        "task_id": str(prompt.task_id or ""),
        "correlation_id": turn_correlation,
        "user_message": str(prompt.user_message or ""),
        "objective": str(task.get("objective") or prompt.user_message or ""),
        "status": str(task.get("status") or ""),
        "current_requirements": current_requirements,
        "uncertainty": task.get("uncertainty") or [],
        "relevant_context": task.get("relevant_context") or {},
        "recent_events": list(prompt.recent_events),
        "re_reason": str(extra.get("re_reason") or ""),
    }

    payload: dict[str, Any] = {
        "protocol": "V3_RUNTIME_DECISION_v1",
        "turn": turn,
        "user_message": str(prompt.user_message or ""),
        "world_snapshot": snapshot,
        "recent_events": list(prompt.recent_events),
        "current_evidence": current_evidence,
        "historical_context": historical_context,
        "completion_eligible_evidence_ids": completion_eligible_evidence_ids,
        "verified_references": verified_references,
        "available_capabilities": list(prompt.available_capabilities),
    }
    environment = snapshot.get("environment")
    if isinstance(environment, dict):
        payload["environment"] = environment
    return _json_mod.dumps(payload, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of a V2 chat response."""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "message", "text", "output"):
            value = response.get(key)
            if isinstance(value, str):
                return value
        # Walk common chat shapes.
        for key in ("choices", "messages"):
            values = response.get(key)
            if isinstance(values, list) and values:
                first = values[0]
                if isinstance(first, dict):
                    inner = first.get("message") or first
                    if isinstance(inner, dict):
                        content = inner.get("content")
                        if isinstance(content, str):
                            return content
                    text = first.get("text")
                    if isinstance(text, str):
                        return text
    raise ValueError(f"Could not extract text from server response: {type(response).__name__}")


def _parse_decision_json(text: str) -> dict[str, Any]:
    """Parse the assistant's JSON reply. Raises on malformed input."""
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Reasoning reply is empty")
    candidates = [stripped]
    for marker in ("```json", "```"):
        idx = stripped.find(marker)
        if idx >= 0:
            end = stripped.find("```", idx + len(marker))
            if end > idx:
                candidates.insert(0, stripped[idx + len(marker):end].strip())
    for candidate in candidates:
        try:
            parsed = _json_mod.loads(candidate)
        except _json_mod.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"Reasoning reply was not valid JSON: {stripped[:200]!r}")

def _build_repair_payload(
    original_payload: str,
    broken_reply: str,
    validation_error: str = "",
) -> str:
    """Request one bounded schema repair from non-compliant gateways."""
    context = _json_mod.loads(original_payload)
    context["protocol"] = (
        "V3_RUNTIME_DECISION_REPAIR: The previous answer violated the "
        "protocol. Return exactly one JSON object and no prose. Do not "
        "execute the task on the server."
    )
    context["repair"] = {
            "allowed_kinds": [
                "action",
                "observation_request",
                "user_question",
                "complete",
                "re_reason",
            ],
            "required_schema": {
                "kind": "string",
                "capability": "string when kind=action",
                "arguments": "object when kind=action",
                "observation_type": (
                    "non-empty registered read-only capability when "
                    "kind=observation_request"
                ),
                "parameters": "object when kind=observation_request",
                "target": "string when kind=observation_request",
                "question": "string when kind=user_question",
                "summary": "string when kind=complete",
                "evidence_ids": "array when kind=complete",
                "required_capabilities": (
                    "required array containing only available capability names"
                ),
                "memory_facts": "array of explicit durable user facts",
            },
            "invalid_previous_answer": (broken_reply or "")[:1500],
            "validation_error": (validation_error or "")[:500],
    }
    return _json_mod.dumps(context, ensure_ascii=False, sort_keys=True)


