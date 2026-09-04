"""The structured goal a user message is turned into.

This is deliberately expressed in capabilities, never tool names: the model
says what has to happen, and the capability registry decides what to call.
That is what lets a task nobody anticipated still reach real execution.

Confidence reuses the Phase A decision bands so understanding and entity
resolution answer to the same rules: HIGH acts, MEDIUM acts using context,
LOW asks, RISKY needs approval.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.context.entity_decision import Confidence

# Calibrated against live replies: clearly stated tasks came back at 0.75-0.85,
# a task missing its target at 0.7, and an unintelligible one at 0.2.
HIGH_CONFIDENCE_MIN = 0.75
MEDIUM_CONFIDENCE_MIN = 0.45
_INTENT_MODES = frozenset({"task", "conversation", "question"})
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


@dataclass(frozen=True)
class IntentStep:
    """One capability the goal needs, with the values to carry it out.

    The keys in `inputs` are conventional parameter names, not tool argument
    names: the capability resolver filters them against whichever tool it
    picks, so the model never has to know what that tool will be.
    """

    capability: str
    inputs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> IntentStep | None:
        if not isinstance(data, dict):
            return None
        capability = str(data.get("capability") or "").strip()
        if not capability:
            return None
        raw_inputs = data.get("inputs")
        inputs = (
            {str(k): v for k, v in raw_inputs.items() if v not in (None, "")}
            if isinstance(raw_inputs, dict)
            else {}
        )
        return cls(capability=capability, inputs=inputs)


@dataclass(frozen=True)
class AgentIntent:
    goal: str = ""
    subgoals: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    plan: tuple[IntentStep, ...] = ()
    constraints: tuple[str, ...] = ()
    references: dict[str, str] = field(default_factory=dict)
    ambiguity: tuple[str, ...] = ()
    needs_user_input: bool = False
    clarifying_question: str = ""
    # What the model claims. Advisory only; never lowers a tool's real risk.
    risk_hint: str = ""
    reported_confidence: float = 0.0
    expected_outcome: str = ""
    mode: str = "task"
    reply: str = ""

    @property
    def is_multi_capability(self) -> bool:
        return len(set(self.required_capabilities)) > 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentIntent:
        def strings(key: str) -> tuple[str, ...]:
            value = data.get(key)
            if isinstance(value, str):
                value = [value]
            return tuple(
                str(item).strip() for item in (value or []) if str(item).strip()
            )

        raw_references = data.get("references")
        references: dict[str, str] = {}
        if isinstance(raw_references, dict):
            references = {
                str(key): str(value)
                for key, value in raw_references.items()
                if str(value).strip()
            }

        try:
            confidence = float(data.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0

        mode = str(data.get("mode") or "task").strip().lower()
        if mode not in _INTENT_MODES:
            mode = "task"

        steps = tuple(
            step
            for step in (
                IntentStep.from_dict(item) for item in (data.get("plan") or [])
            )
            if step is not None
        )
        capabilities = strings("required_capabilities")
        if steps and not capabilities:
            # The plan is the stronger statement; keep the summary consistent.
            seen: dict[str, None] = {}
            for step in steps:
                seen.setdefault(step.capability, None)
            capabilities = tuple(seen)

        return cls(
            goal=str(data.get("goal") or "").strip(),
            subgoals=strings("subgoals"),
            required_capabilities=capabilities,
            plan=steps,
            constraints=strings("constraints"),
            references=references,
            ambiguity=strings("ambiguity"),
            needs_user_input=bool(data.get("needs_user_input")),
            clarifying_question=str(data.get("clarifying_question") or "").strip(),
            risk_hint=str(data.get("risk_hint") or "").strip(),
            reported_confidence=max(0.0, min(1.0, confidence)),
            expected_outcome=str(data.get("expected_outcome") or "").strip(),
            mode=mode,
            reply=str(data.get("reply") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "subgoals": list(self.subgoals),
            "required_capabilities": list(self.required_capabilities),
            "plan": [
                {"capability": step.capability, "inputs": dict(step.inputs)}
                for step in self.plan
            ],
            "constraints": list(self.constraints),
            "references": dict(self.references),
            "ambiguity": list(self.ambiguity),
            "needs_user_input": self.needs_user_input,
            "clarifying_question": self.clarifying_question,
            "risk_hint": self.risk_hint,
            "confidence": self.reported_confidence,
            "expected_outcome": self.expected_outcome,
            "mode": self.mode,
            "reply": self.reply,
        }


@dataclass(frozen=True)
class IntentValidation:
    ok: bool = False
    errors: tuple[str, ...] = ()
    unknown_capabilities: tuple[str, ...] = ()
    unavailable_capabilities: tuple[str, ...] = ()

    @property
    def has_capability_gap(self) -> bool:
        return bool(self.unknown_capabilities or self.unavailable_capabilities)


def validate_intent(
    intent: AgentIntent,
    known_capabilities: set[str],
    available_capabilities: set[str] | None = None,
) -> IntentValidation:
    """Reject an intent that could not be acted on.

    `known` is the vocabulary; `available` is what this machine's tools can
    actually do right now. The split is what lets us say "I understood you,
    but I have no way to do it" instead of pretending not to understand.
    """
    errors: list[str] = []
    if not intent.goal:
        errors.append("amac (goal) bos")

    available = known_capabilities if available_capabilities is None else available_capabilities
    unknown = tuple(
        c for c in intent.required_capabilities if c not in known_capabilities
    )
    unavailable = tuple(
        c
        for c in intent.required_capabilities
        if c in known_capabilities and c not in available
    )

    conversational = intent.mode in ("conversation", "question") or intent.needs_user_input
    if not intent.required_capabilities and not conversational:
        errors.append("hicbir yetenek belirtilmemis")
    for capability in unknown:
        errors.append(f"bilinmeyen yetenek '{capability}'")

    return IntentValidation(
        ok=not errors,
        errors=tuple(errors),
        unknown_capabilities=unknown,
        unavailable_capabilities=unavailable,
    )


def decide_confidence(
    intent: AgentIntent, risk_floor: RiskLevel | None = None
) -> Confidence:
    """Map an intent onto the Phase A action bands.

    A destructive intent is RISKY no matter how sure the model sounded; the
    probe showed the model reaching for filesystem.delete at 0.85 confidence
    while declaring it needed no input.
    """
    if risk_floor is not None and risk_floor.severity >= RiskLevel.HIGH_RISK.severity:
        return Confidence.RISKY
    if intent.reported_confidence >= HIGH_CONFIDENCE_MIN:
        return Confidence.HIGH
    if intent.reported_confidence >= MEDIUM_CONFIDENCE_MIN:
        return Confidence.MEDIUM
    return Confidence.LOW


def extract_intent_json(raw: str) -> dict[str, Any] | None:
    """Recover the JSON object from a model reply.

    There is no schema-constrained decoding available, so malformed or
    chatty output is an ordinary case rather than an exception.
    """
    text = (raw or "").strip()
    if not text:
        return None

    if "```" in text:
        for block in text.split("```"):
            candidate = block.strip()
            for prefix in ("json", "JSON"):
                candidate = candidate.removeprefix(prefix).strip()
            if candidate.startswith("{"):
                text = candidate
                break

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _object_slice(raw: str) -> str | None:
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]


def repair_intent_json(raw: str) -> dict[str, Any] | None:
    """Best-effort recovery of a near-JSON intent object.

    Only mechanical repairs: trailing commas and Python literals. Anything
    that still is not an object is rejected rather than guessed into a plan.
    """
    parsed = extract_intent_json(raw)
    if parsed is not None:
        return parsed

    slice_ = _object_slice(raw)
    if slice_ is None:
        return None
    repaired = _TRAILING_COMMA.sub(r"\1", slice_)
    repaired = (
        repaired.replace(": True", ": true")
        .replace(": False", ": false")
        .replace(": None", ": null")
    )
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def llm_named_a_tool(data: dict[str, Any]) -> bool:
    """Whether the model tried to pick an executable tool itself."""
    if data.get("tool_name") or data.get("tool"):
        return True
    for key in ("plan", "steps"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and (item.get("tool_name") or item.get("tool")):
                return True
    return False
