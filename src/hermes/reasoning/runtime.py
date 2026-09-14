"""ReasoningRuntime — the agent's semantic decision owner.

The runtime is the *only* layer that decides what Hermes does next. It
asks the LLM, parses the reply into a typed `Decision`, and returns it.
Nothing else.

Architecture:

  User Message
        │
  ┌─────┴──────┐         ┌────────────────────┐
  │ World Model │ ◄────── │   ReasoningRuntime │
  └─────┬──────┘         └──────────┬─────────┘
        │                           │
        │     ┌────────────────┐    │
        └────►│ Execution Log  │◄───┘
              └────────────────┘
                       │
                       ▼
              server-side LLM
                       │
                       ▼
                    Decision

Re-reasoning is the default: when an action returns and the world has
moved, the runtime is called again with the new context. There is no
persistent plan to "follow"; the LLM is asked what to do next given
what we know now.

The runtime owns:

  * prompt assembly (world + log + capabilities),
  * call to the LLM transport,
  * parsing of the LLM reply,
  * construction of the typed Decision.

The runtime does **not**:

  * execute actions,
  * evaluate security policy,
  * remember anything across calls (Memory does that),
  * retry on parse failure — the orchestrator does.
"""

from __future__ import annotations

import json as _json
import logging
import re
from collections.abc import Iterable

_LOGGER = logging.getLogger(__name__)
from dataclasses import dataclass, field, replace
from typing import Any

from hermes.capability import (
    CapabilityContract,
    CapabilityRegistry,
    windows_capabilities,
)
from hermes.execution_log import ExecutionLogStore, EventEnvelope
from hermes.reasoning.decision import Decision, DecisionKind, MemoryFact
from hermes.reasoning.transport import (
    LlmReasoningClient,
    ReasoningPrompt,
    ReasoningReply,
)


@dataclass
class ReasoningRuntime:
    """Stateless reasoning shell. One instance per agent loop is enough.

    The runtime optionally takes a ``memory`` adapter that exposes past
    episodes and long-term facts. The LLM is given a compact memory
    view alongside the world model snapshot, so it can decide based on
    both "what the world is now" (world model) and "what the user/system
    did before" (memory). The memory layer is consulted read-only; the
    runtime never mutates it. Callers (orchestrator) own write paths.
    """

    client: LlmReasoningClient
    capability_registry: CapabilityRegistry
    execution_log: ExecutionLogStore
    max_re_reason_iterations: int = 6
    recent_event_limit: int = 24
    memory: Any = None  # optional: EpisodeStore + LongTermStore facade

    # Local fast path. When enabled, ``reason`` first asks
    # ``FastPathPlanner`` whether this explicit command can be answered
    # without a server LLM round-trip. Disabled by default so existing
    # callers/tests keep their exact behaviour; the production bootstrap
    # turns it on explicitly.
    tool_registry: Any = None
    fast_path_enabled: bool = False
    _fast_path_planner: Any = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def reason(
        self,
        *,
        user_message: str,
        world_model: Any,
        correlation_id: str = "",
        conversation_history: list[dict[str, str]] | None = None,
        fast_path_eligible: bool = True,
    ) -> Decision:
        """Ask the LLM what to do next.

        The runtime reads the world + log + memory, calls the LLM once,
        parses the reply, and returns a typed Decision. It does not
        loop, retry, or execute — the orchestrator owns the loop.

        ``conversation_history`` is the recent (role, content) turns of
        this session so the LLM can resolve cross-turn references like
        "onu ac". ``fast_path_eligible`` lets the orchestrator gate the
        local fast path to the first reasoning iteration of a turn.
        """
        decision = self._try_fast_path(
            user_message,
            world_model,
            fast_path_eligible=fast_path_eligible,
            correlation_id=correlation_id,
        )
        if decision is not None:
            return decision
        snapshot = world_model.snapshot()
        events = list(self._recent_events(correlation_id))
        capabilities = tuple(
            _serialise_capability(cap, self.capability_registry.get(cap.capability))
            for cap in windows_capabilities(self.capability_registry)
        )
        memory_view = self._memory_view()
        extra: dict[str, Any] = {}
        if isinstance(snapshot.get("extra"), dict):
            extra.update(snapshot["extra"])
        env = snapshot.get("environment") if isinstance(snapshot.get("environment"), dict) else None
        if isinstance(env, dict):
            env_extra = env.get("extra") if isinstance(env.get("extra"), dict) else None
            if isinstance(env_extra, dict) and env_extra:
                for k, v in env_extra.items():
                    if k not in extra:
                        extra[k] = v
        if memory_view:
            extra["memory"] = memory_view
        prompt = ReasoningPrompt(
            user_message=user_message,
            world_snapshot=snapshot,
            available_capabilities=capabilities,
            recent_events=tuple(events),
            extra=extra,
            correlation_id=correlation_id,
            task_id=_task_id_for(correlation_id),
            client_session_id=self._client_session_id(),
            conversation_history=tuple(
                _bounded_history(conversation_history, limit=8)
            ),
        )
        reply = await self.client.reason(prompt)
        return self._build_decision(reply, correlation_id)

    # ------------------------------------------------------------------
    # Fast path
    # ------------------------------------------------------------------

    def _try_fast_path(
        self,
        user_message: str,
        world_model: Any,
        *,
        fast_path_eligible: bool,
        correlation_id: str,
    ) -> Decision | None:
        if not self.fast_path_enabled or not fast_path_eligible:
            return None
        try:
            planner = self._fast_path_planner
            if planner is None:
                from hermes.reasoning.fast_path import FastPathPlanner

                planner = FastPathPlanner(tool_registry=self.tool_registry)
                self._fast_path_planner = planner
            decision = planner.resolve(user_message, world_model)
        except Exception as exc:
            _LOGGER.debug("fast_path error: %s", exc, exc_info=True)
            return None
        if decision is None:
            return None
        from dataclasses import replace as _replace

        _LOGGER.debug("fast_path resolved %r -> %s", user_message, decision.kind)
        return _replace(decision, correlation_id=correlation_id)

    def _memory_view(self) -> dict[str, Any]:
        """Build a compact memory view for the LLM prompt.

        The view is read-only and safe: long-term facts are scrubbed of
        secret-shaped values; episodic records are summarised. If no
        memory is wired the view is empty.
        """
        if self.memory is None:
            return {}
        view: dict[str, Any] = {}
        episodes = list(self.memory.episodic.query() or [])
        # Keep the most recent 5 episodes, summarise them.
        view["recent_episodes"] = [
            {
                "summary": _scrub_value(getattr(ep, "summary", "")),
                "outcome": getattr(ep, "outcome", ""),
                "capabilities": list(getattr(ep, "capabilities", ()) or ()),
            }
            for ep in episodes[-5:]
        ]
        facts = list(self.memory.long_term.all_facts() or [])
        # Filter out obvious secret-shaped values defensively.
        view["long_term_facts"] = [
            {"key": fact.key, "value": _scrub_value(fact.value)}
            for fact in facts
            if not _looks_like_secret(fact.value)
        ]
        return view

    def _client_session_id(self) -> str:
        if self.memory is None:
            return ""
        return str(getattr(self.memory, "active_session_id", "") or "").strip()

    async def re_reason(
        self,
        *,
        user_message: str,
        world_model: Any,
        correlation_id: str = "",
        reason: str = "",
        conversation_history: list[dict[str, str]] | None = None,
    ) -> Decision:
        """Convenience wrapper for the re-reason loop.

        The runtime does not actually do extra work — the orchestrator
        can simply call `reason(...)` again after it has updated the
        world model. This wrapper exists so call sites are explicit
        about "we are calling the LLM again, here is why".
        """
        # `reason` always reads the fresh world snapshot, so a fresh call
        # *is* the re-reason. The `reason` text is appended to the prompt's
        # extra so the LLM can see it.
        snapshot = world_model.snapshot()
        snapshot.setdefault("extra", {})["re_reason"] = reason
        events = list(self._recent_events(correlation_id))
        capabilities = tuple(
            _serialise_capability(cap, self.capability_registry.get(cap.capability))
            for cap in windows_capabilities(self.capability_registry)
        )
        memory_view = self._memory_view()
        extra = {"re_reason": reason}
        env = snapshot.get("environment") if isinstance(snapshot.get("environment"), dict) else None
        if isinstance(env, dict):
            env_extra = env.get("extra") if isinstance(env.get("extra"), dict) else None
            if isinstance(env_extra, dict) and env_extra:
                for k, v in env_extra.items():
                    if k not in extra:
                        extra[k] = v
        if memory_view:
            extra["memory"] = memory_view
        prompt = ReasoningPrompt(
            user_message=user_message,
            world_snapshot=snapshot,
            available_capabilities=capabilities,
            recent_events=tuple(events),
            extra=extra,
            correlation_id=correlation_id,
            task_id=_task_id_for(correlation_id),
            client_session_id=self._client_session_id(),
            conversation_history=tuple(
                _bounded_history(conversation_history, limit=8)
            ),
        )
        reply = await self.client.reason(prompt)
        return self._build_decision(reply, correlation_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _recent_events(self, correlation_id: str) -> Iterable[dict[str, Any]]:
        """Return recent events as plain dicts so they are JSON-serialisable.

        The runtime always passes plain data to the LLM transport; the
        envelopes never leave the boundary. The runtime exposes this as
        an iterable of dicts; callers can convert to a list when needed.
        """
        envelopes = (
            list(self.execution_log.events_for(correlation_id))
            if correlation_id
            else list(self.execution_log.all())
        )

        def _iter():
            for envelope in envelopes:
                yield {
                    "event_id": envelope.event_id,
                    "kind": envelope.kind.value,
                    "recorded_at": envelope.recorded_at,
                    "correlation_id": envelope.correlation_id,
                    "action_id": envelope.action_id,
                    "payload": _scrub_payload(_payload_to_dict(envelope.payload)),
                }

        return _iter()

    def _build_decision(self, reply: ReasoningReply, correlation_id: str) -> Decision:
        data = reply.decision_json
        kind_raw = data.get("kind")
        try:
            kind = DecisionKind(str(kind_raw))
        except ValueError as exc:
            raise ValueError(
                f"LLM reply has unknown decision kind: {kind_raw!r}"
            ) from exc

        if kind is DecisionKind.ACTION:
            capability = _required_text(data, "capability", kind)
            return _with_required_capabilities(
                Decision.of_action(
                    capability=capability,
                    arguments=dict(data.get("arguments") or {}),
                    rationale=str(data.get("rationale") or ""),
                    expected_observation=str(data.get("expected_observation") or ""),
                    correlation_id=correlation_id,
                ),
                data,
            )
        if kind is DecisionKind.OBSERVATION_REQUEST:
            return _with_required_capabilities(
                Decision.of_observation_request(
                    observation_type=_required_text(data, "observation_type", kind),
                    target=str(data.get("target") or ""),
                    rationale=str(data.get("rationale") or ""),
                    parameters=dict(data.get("parameters") or {}),
                    correlation_id=correlation_id,
                ),
                data,
            )
        if kind is DecisionKind.USER_QUESTION:
            options = tuple(str(o) for o in (data.get("options") or ()))
            return _with_required_capabilities(
                Decision.of_user_question(
                    question=_required_text(data, "question", kind),
                    why=str(data.get("why") or data.get("rationale") or ""),
                    options=options,
                    correlation_id=correlation_id,
                ),
                data,
            )
        if kind is DecisionKind.COMPLETE:
            evidence_ids = tuple(str(e) for e in (data.get("evidence_ids") or ()))
            return _with_required_capabilities(
                Decision.of_complete(
                    summary=_required_text(data, "summary", kind),
                    evidence_ids=evidence_ids,
                    correlation_id=correlation_id,
                ),
                data,
            )
        if kind is DecisionKind.RE_REASON:
            return _with_required_capabilities(
                Decision.of_re_reason(
                    reason=str(data.get("reason") or ""),
                    correlation_id=correlation_id,
                ),
                data,
            )
        # Defensive: future enum values raise.
        raise ValueError(f"Unsupported decision kind: {kind!r}")


def _required_text(
    data: dict[str, Any], field_name: str, kind: DecisionKind
) -> str:
    value = str(data.get(field_name) or "").strip()
    if not value:
        raise ValueError(
            f"LLM {kind.value} decision requires non-empty {field_name!r}"
        )
    return value


def _with_required_capabilities(
    decision: Decision, data: dict[str, Any]
) -> Decision:
    raw = data.get("required_capabilities") or ()
    if not isinstance(raw, (list, tuple)):
        raise ValueError("LLM decision field 'required_capabilities' must be an array")
    required = tuple(
        capability
        for capability in (str(value).strip() for value in raw)
        if capability
    )
    raw_facts = data.get("memory_facts") or ()
    if not isinstance(raw_facts, (list, tuple)):
        raise ValueError("LLM decision field 'memory_facts' must be an array")
    facts: list[MemoryFact] = []
    for raw_fact in raw_facts:
        key, value = _coerce_memory_fact(raw_fact)
        if not key or not value:
            # A malformed fact is not worth failing the whole turn; drop it
            # and let the memory layer keep whatever is cleanly expressible.
            continue
        facts.append(MemoryFact(key=key, value=value))
    return replace(
        decision,
        required_capabilities=required,
        memory_facts=tuple(facts),
    )


def _coerce_memory_fact(raw: Any) -> tuple[str, str]:
    """Best-effort extraction of (key, value) from a memory fact.

    The LLM often returns facts as objects with ``key``/``value`` fields, but
    real models slip into looser shapes (a bare string, ``"key: value"``, or
    ``"key=value"``). Hermes must not crash the whole turn because one fact is
    phrased loosely — return ("", "") for anything unparseable and let the
    caller skip it.
    """
    if isinstance(raw, dict):
        key = str(raw.get("key") or "").strip()
        value = str(raw.get("value") or "").strip()
        return key, value
    if hasattr(raw, "key") and hasattr(raw, "value"):
        return str(getattr(raw, "key", "") or "").strip(), str(getattr(raw, "value", "") or "").strip()
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return "", ""
        # "key: value" or "key = value" (first separator wins).
        for sep in (":", "=", "->"):
            if sep in text:
                key, _, value = text.partition(sep)
                key = key.strip().strip("\"'")
                value = value.strip().strip("\"'")
                if key:
                    return key, value
        return "", ""
    return "", ""


def _payload_to_dict(payload: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(payload):
        return asdict(payload)
    if isinstance(payload, dict):
        return dict(payload)
    return {"value": str(payload)}


def _task_id_for(correlation_id: str) -> str:
    normalized = str(correlation_id or "").strip()
    if normalized.startswith("turn_"):
        return f"task_{normalized[5:]}"
    return f"task_{normalized}" if normalized else ""


def _serialise_capability(summary: Any, contract: CapabilityContract | None = None) -> dict[str, Any]:
    return {
        "name": summary.capability,
        "risk_level": summary.risk_level,
        "execution_target": summary.execution_target,
        "has_verifier": summary.has_verifier,
        "purpose": _purpose_for(contract, summary.capability),
        "input_schema": dict(contract.input_schema) if contract else {},
        "output_schema": dict(contract.output_schema) if contract else {},
        "side_effects": [s.value for s in contract.side_effects] if contract else [],
        "preconditions": list(contract.preconditions) if contract else [],
        "known_limitations": list(contract.known_limitations) if contract else [],
        "observation_value": list(contract.observation_value) if contract else [],
        "failure_semantics": contract.failure_semantics.value if contract else "",
        "estimated_cost": contract.estimated_cost if contract else "",
        "verification": (
            {
                "method": contract.verification.method,
                "timeout_seconds": contract.verification.timeout_seconds,
            }
            if contract and contract.verification
            else None
        ),
    }


def _purpose_for(contract: CapabilityContract | None, capability: str) -> str:
    if contract is not None and contract.purpose:
        return contract.purpose
    try:
        from hermes.capability._defaults import _PURPOSE
    except ImportError:  # pragma: no cover
        return capability
    return _PURPOSE.get(capability, capability)


# Defensive secret-shape filter for the LLM-facing memory view. We are
# paranoid on purpose: the memory stores are user-facing and the
# runtime never wants to leak a credential into a prompt.
_SECRET_PATTERNS = (
    re.compile(r"password\s*[:=]", re.IGNORECASE),
    re.compile(r"api[_-]?key\s*[:=]", re.IGNORECASE),
    re.compile(r"secret\s*[:=]", re.IGNORECASE),
    re.compile(r"token\s*[:=]", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
)


def _looks_like_secret(value: str) -> bool:
    if not value:
        return False
    return any(p.search(value) for p in _SECRET_PATTERNS)


def _scrub_value(value: str, *, max_len: int = 2000) -> str:
    """Defensive scrubber for any value that goes into the LLM prompt.

    - If the value matches a secret pattern, return a redaction marker
      so the model never sees the secret text.
    - Otherwise, truncate to ``max_len`` characters with a marker so a
      huge binary blob does not blow up the prompt window.

    This is best-effort defence in depth. The memory and log layers
    also enforce scrubbing on write, so the prompt should never carry
    a secret even before this scrubber runs.
    """
    if value is None:
        return ""
    text = str(value)
    if _looks_like_secret(text):
        return "[REDACTED]"
    if len(text) > max_len:
        return text[:max_len] + f"... [truncated {len(text) - max_len} chars]"
    return text


def _scrub_payload(payload: Any, *, max_str: int = 4000) -> Any:
    """Recursively scrub every string in a payload.

    Used for ``recent_events`` so the LLM never sees raw browser page
    text, command output, or untrusted file content. Non-string
    scalars pass through unchanged.
    """
    if payload is None or isinstance(payload, (bool, int, float)):
        return payload
    if isinstance(payload, str):
        return _scrub_value(payload, max_len=max_str)
    if isinstance(payload, dict):
        return {str(k): _scrub_payload(v) for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [_scrub_payload(item) for item in payload]
    return str(payload)


def _bounded_history(
    conversation_history: list[dict[str, str]] | None, *, limit: int = 8
) -> list[dict[str, str]]:
    """Normalise + bound raw turns to (role, content) dicts for the prompt.

    Content is truncated so a long multi-turn session cannot blow the
    reasoning payload; only the last ``limit`` turns are kept.
    """
    if not conversation_history:
        return []
    bounded: list[dict[str, str]] = []
    for turn in conversation_history:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            bounded.append({"role": role, "content": content[:800]})
    return bounded[-limit:]