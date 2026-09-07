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
        request = ChatRequest(
            message=user_payload,
            system=system_prompt,
            stream=False,
        )
        response = await self._server.chat(request)
        text = _extract_text(response)
        parsed = _parse_decision_json(text)
        return ReasoningReply(decision_json=parsed, raw_text=text)


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
    payload = {
        "user_message": prompt.user_message,
        "world_snapshot": prompt.world_snapshot,
        "recent_events": list(prompt.recent_events),
    }
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