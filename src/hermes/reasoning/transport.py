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
from hermes.reasoning.validation import validate_decision_payload
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

    async def reason(self, prompt: ReasoningPrompt) -> ReasoningReply:
        from hermes.server.models import ChatRequest

        system_prompt = _build_system_prompt(prompt)
        user_payload = _build_user_payload(prompt)
        text = ""
        validation_error = ""
        for attempt in range(2):
            message = (
                user_payload
                if attempt == 0
                else _build_repair_payload(
                    user_payload,
                    text,
                    validation_error,
                )
            )
            request = ChatRequest(
                message=message,
                system=system_prompt,
                stream=False,
                response_format={"type": "json_object"},
            )
            response = await self._server.chat(request)
            text = _extract_text(response)
            try:
                parsed = _parse_decision_json(text)
                validate_decision_payload(
                    parsed,
                    available_capabilities=prompt.available_capabilities,
                    world_snapshot=prompt.world_snapshot,
                    correlation_id=prompt.correlation_id,
                )
            except ValueError as exc:
                validation_error = str(exc)
                if attempt == 0:
                    continue
                raise
            return ReasoningReply(decision_json=parsed, raw_text=text)
        raise ValueError("Reasoning reply was not valid JSON")


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
        "not declare complete; re-reason. Include required_capabilities on "
        "every response: the full set of capabilities required by the current "
        "user objective, including requirements already verified during this "
        "turn. Keep the set stable unless the user revises the objective. "
        "When the objective requires multiple independent results from the "
        "same capability, repeat that capability once per required result. "
        "Use memory_facts only when the user explicitly asks you to remember "
        "a durable preference or fact. Never put credentials, passwords, "
        "tokens, API keys, secrets, or private keys in memory_facts. "
        "Resolve contextual targets "
        "from verified_references and environment; never invent an absolute "
        "Windows user path. Historical context may help select an action target "
        "but must never be used as proof that the current task is complete. "
        "For complete decisions, cite only IDs listed in "
        "completion_eligible_evidence_ids.\n"
        "Available capabilities:\n"
        f"{caps_text}\n"
        "JSON schema: {\"kind\": str, \"capability\": str?, \"arguments\": "
        "object?, \"rationale\": str?, \"expected_observation\": str?, "
        "\"observation_type\": non-empty string when kind=observation_request, "
        "\"parameters\": object when kind=observation_request, "
        "\"target\": str?, \"question\": str?, "
        "\"options\": list?, \"summary\": str?, \"evidence_ids\": list?, "
        "\"reason\": str?, \"required_capabilities\": list[str], "
        "\"memory_facts\": list[{\"key\": str, \"value\": str}]?}. "
        "required_capabilities must always be present and must contain only "
        "names from Available capabilities. observation_type must name a "
        "read-only Available capability. No extra prose."
    )


def _build_user_payload(prompt: ReasoningPrompt) -> str:
    if not prompt.client_session_id.strip():
        raise ValueError("Reasoning request requires client_session_id")
    if not prompt.task_id.strip():
        raise ValueError("Reasoning request requires task_id")
    if not prompt.correlation_id.strip():
        raise ValueError("Reasoning request requires correlation_id")
    snapshot = prompt.world_snapshot
    task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
    environment = (
        snapshot.get("environment")
        if isinstance(snapshot.get("environment"), dict)
        else {}
    )
    evidence = (
        snapshot.get("evidence")
        if isinstance(snapshot.get("evidence"), list)
        else []
    )
    references = (
        snapshot.get("references")
        if isinstance(snapshot.get("references"), dict)
        else {}
    )
    current_evidence = [
        dict(item)
        for item in evidence
        if isinstance(item, dict)
        and str(item.get("correlation_id") or "") == prompt.correlation_id
    ]
    verified_references = _verified_references(references, evidence)
    historical_evidence = _historical_evidence_summaries(
        evidence,
        current_correlation_id=prompt.correlation_id,
    )
    memory = prompt.extra.get("memory")
    memory_view = dict(memory) if isinstance(memory, dict) else {}
    payload = {
        "protocol": (
            "V3_RUNTIME_DECISION: Do not execute the task on the server. "
            "Return exactly one JSON decision object for the Windows client. "
            "Use a capability name, never a tool name, and emit no prose."
        ),
        "turn": {
            "client_session_id": prompt.client_session_id,
            "task_id": prompt.task_id,
            "correlation_id": prompt.correlation_id,
            "user_message": prompt.user_message,
            "objective": str(task.get("objective") or prompt.user_message),
            "status": str(task.get("status") or "open"),
            "current_requirements": list(task.get("requirements") or []),
            "uncertainty": list(task.get("uncertainty") or []),
            "relevant_context": dict(task.get("relevant_context") or {}),
            "recent_events": list(prompt.recent_events),
            "re_reason": str(prompt.extra.get("re_reason") or ""),
        },
        "environment": dict(environment),
        "verified_references": verified_references,
        "current_evidence": current_evidence,
        "completion_eligible_evidence_ids": _completion_eligible_evidence_ids(
            current_evidence,
            list(task.get("requirements") or []),
        ),
        "historical_context": {
            "previous_successful_tasks": list(
                memory_view.get("recent_episodes") or []
            ),
            "historical_reference_summaries": [
                {
                    "key": key,
                    "kind": reference.get("kind"),
                    "value": reference.get("value"),
                    "provenance": reference.get("provenance"),
                    "provenance_correlation_id": reference.get(
                        "provenance_correlation_id"
                    ),
                }
                for key, reference in verified_references.items()
            ],
            "historical_evidence_summaries": historical_evidence,
            "relevant_historical_facts": list(
                memory_view.get("long_term_facts") or []
            ),
        },
        # Some deployed Hermes gateways do not forward the OpenAI system
        # role to the underlying model. Keep the authoritative capability
        # contract in the user envelope as well so the reasoning boundary
        # remains valid on those gateways.
        "available_capabilities": list(prompt.available_capabilities),
    }
    return _json_mod.dumps(payload, ensure_ascii=False, sort_keys=True)


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


def _verified_references(
    references: dict[str, Any],
    evidence: list[Any],
) -> dict[str, dict[str, Any]]:
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in evidence
        if isinstance(item, dict) and str(item.get("evidence_id") or "")
    }
    verified: dict[str, dict[str, Any]] = {}
    for key, raw_reference in references.items():
        if not isinstance(raw_reference, dict):
            continue
        extra = raw_reference.get("extra")
        if not isinstance(extra, dict) or extra.get("verification_status") != "verified":
            continue
        reference = dict(raw_reference)
        provenance = evidence_by_id.get(str(reference.get("provenance") or ""))
        reference["provenance_correlation_id"] = (
            str(provenance.get("correlation_id") or "")
            if isinstance(provenance, dict)
            else ""
        )
        verified[str(key)] = reference
    return verified


def _historical_evidence_summaries(
    evidence: list[Any],
    *,
    current_correlation_id: str,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if str(item.get("correlation_id") or "") == current_correlation_id:
            continue
        source = str(item.get("source") or "")
        data = item.get("data")
        if source not in {"observation", "verifier"}:
            continue
        if source == "verifier" and (
            not isinstance(data, dict) or data.get("status") != "verified"
        ):
            continue
        summaries.append(
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "source": source,
                "capability": str(item.get("capability") or ""),
                "claim": str(item.get("claim") or ""),
                "correlation_id": str(item.get("correlation_id") or ""),
                "completion_eligible": False,
            }
        )
    return summaries


def _completion_eligible_evidence_ids(
    current_evidence: list[dict[str, Any]],
    requirements: list[Any],
) -> list[str]:
    requirement_evidence = {
        str(evidence_id)
        for requirement in requirements
        if isinstance(requirement, dict)
        for evidence_id in requirement.get("evidence", [])
    }
    eligible: list[str] = []
    for item in current_evidence:
        evidence_id = str(item.get("evidence_id") or "")
        source = str(item.get("source") or "")
        data = item.get("data")
        if not evidence_id or source not in {"observation", "verifier"}:
            continue
        if source == "verifier" and (
            not isinstance(data, dict) or data.get("status") != "verified"
        ):
            continue
        if requirements and evidence_id not in requirement_evidence:
            continue
        eligible.append(evidence_id)
    return eligible


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