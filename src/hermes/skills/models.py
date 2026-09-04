"""Declarative procedures the agent can execute.

A skill says *what must happen* (capabilities), never *which tool to call*.
Tool choice is resolved at runtime from the capability registry, so a skill
keeps working when tools are added, renamed or replaced.

Everything a skill needs already exists elsewhere: risk lives in the policy
engine, verification in the verifier registry, retries in the recovery engine,
budgets in the execution guard. This module only describes the procedure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# {{ inputs.url }} / {{ steps.read_page.output }} / {{ context.last_created_file }}
_REFERENCE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_INPUT_REFERENCE = re.compile(r"\{\{\s*inputs\.([a-zA-Z0-9_]+)")


def _collect_input_names(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_input_names(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_input_names(item, found)
    elif isinstance(value, str):
        found.update(_INPUT_REFERENCE.findall(value))


class SkillRisk(StrEnum):
    """Advisory only. The policy engine still decides on the resolved tool."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SkillStepKind(StrEnum):
    CAPABILITY = "capability"
    SKILL = "skill"


@dataclass(frozen=True)
class SuccessCriteria:
    """Skill-level proof, on top of the tool's own verification.

    `observe_capability` re-reads the world with a read-only capability so a
    step like "open Word" can be judged by whether Word is actually running,
    not by whether the launch command returned zero.
    """

    output_contains: str = ""
    output_not_empty: bool = False
    min_length: int = 0
    path_exists: str = ""
    observe_capability: str = ""
    observe_inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not (
            self.output_contains
            or self.output_not_empty
            or self.min_length
            or self.path_exists
            or self.observe_capability
        )

    @classmethod
    def from_dict(cls, data: Any) -> SuccessCriteria:
        if not isinstance(data, dict):
            return cls()
        return cls(
            output_contains=str(data.get("output_contains") or ""),
            output_not_empty=bool(data.get("output_not_empty") or False),
            min_length=int(data.get("min_length") or 0),
            path_exists=str(data.get("path_exists") or ""),
            observe_capability=str(data.get("observe_capability") or ""),
            observe_inputs=dict(data.get("observe_inputs") or {}),
        )


@dataclass(frozen=True)
class SkillStep:
    step_id: str
    kind: SkillStepKind = SkillStepKind.CAPABILITY
    capability: str = ""
    skill_id: str = ""
    title: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    # Path into the tool's structured result that becomes this step's logical
    # output, so a skill can consume a value without knowing the whole shape.
    output_from: str = ""
    success_criteria: SuccessCriteria = field(default_factory=SuccessCriteria)
    optional: bool = False
    condition: str = ""

    @property
    def label(self) -> str:
        return self.title or self.capability or self.skill_id or self.step_id

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillStep:
        capability = str(data.get("capability") or "")
        nested = str(data.get("skill") or "")
        return cls(
            step_id=str(data.get("id") or ""),
            kind=SkillStepKind.SKILL if nested else SkillStepKind.CAPABILITY,
            capability=capability,
            skill_id=nested,
            title=str(data.get("title") or ""),
            inputs=dict(data.get("inputs") or {}),
            output=str(data.get("output") or ""),
            output_from=str(data.get("output_from") or ""),
            success_criteria=SuccessCriteria.from_dict(data.get("success_criteria")),
            optional=bool(data.get("optional") or False),
            condition=str(data.get("condition") or ""),
        )


@dataclass(frozen=True)
class Skill:
    skill_id: str
    title: str = ""
    description: str = ""
    # Semantic descriptions for a future LLM to match against. Deliberately not
    # used for substring routing — that is the pattern this layer replaces.
    intent_hints: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    steps: tuple[SkillStep, ...] = ()
    risk: SkillRisk = SkillRisk.LOW
    idempotency: str = "stateful"
    produces: str = ""
    # Lets a skill supersede a legacy mission handler without touching engine.py.
    legacy_logical_kind: str = ""

    @property
    def required_inputs(self) -> tuple[str, ...]:
        """Input names the steps actually reference.

        A caller can check these before starting the skill, instead of
        discovering on the first step that a value was never supplied. The
        names are the skill's own, which is why this cannot be inferred from
        capabilities alone.
        """
        found: set[str] = set()
        for step in self.steps:
            _collect_input_names(step.inputs, found)
            _collect_input_names(step.success_criteria.path_exists, found)
            _collect_input_names(step.success_criteria.output_contains, found)
            _collect_input_names(step.success_criteria.observe_inputs, found)
            _collect_input_names(step.condition, found)
        return tuple(sorted(found))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Skill:
        try:
            risk = SkillRisk(str(data.get("risk") or "low").strip().lower())
        except ValueError:
            risk = SkillRisk.LOW
        return cls(
            skill_id=str(data.get("id") or ""),
            title=str(data.get("title") or data.get("id") or ""),
            description=str(data.get("description") or ""),
            intent_hints=tuple(str(item) for item in (data.get("intent_hints") or [])),
            required_capabilities=tuple(
                str(item) for item in (data.get("required_capabilities") or [])
            ),
            prerequisites=tuple(str(item) for item in (data.get("prerequisites") or [])),
            steps=tuple(
                SkillStep.from_dict(item)
                for item in (data.get("steps") or [])
                if isinstance(item, dict)
            ),
            risk=risk,
            idempotency=str(data.get("idempotency") or "stateful"),
            produces=str(data.get("produces") or ""),
            legacy_logical_kind=str(data.get("legacy_logical_kind") or ""),
        )


@dataclass(frozen=True)
class SkillValidation:
    ok: bool
    errors: tuple[str, ...] = ()


def validate_skill(skill: Skill, known_capabilities: set[str]) -> SkillValidation:
    """Reject a skill that could not possibly run, before it is offered."""
    errors: list[str] = []

    if not skill.skill_id:
        errors.append("skill id eksik")
    if not skill.steps:
        errors.append(f"{skill.skill_id or 'skill'}: adim tanimlanmamis")

    seen: set[str] = set()
    for index, step in enumerate(skill.steps):
        where = step.step_id or f"adim {index + 1}"
        if not step.step_id:
            errors.append(f"{skill.skill_id}: {where} icin id eksik")
        elif step.step_id in seen:
            errors.append(f"{skill.skill_id}: tekrar eden adim id '{step.step_id}'")
        seen.add(step.step_id)

        if step.kind is SkillStepKind.CAPABILITY:
            if not step.capability:
                errors.append(f"{skill.skill_id}: {where} capability belirtmiyor")
            elif step.capability not in known_capabilities:
                errors.append(
                    f"{skill.skill_id}: {where} bilinmeyen capability '{step.capability}'"
                )
            observe = step.success_criteria.observe_capability
            if observe and observe not in known_capabilities:
                errors.append(
                    f"{skill.skill_id}: {where} bilinmeyen gozlem capability '{observe}'"
                )
        elif not step.skill_id:
            errors.append(f"{skill.skill_id}: {where} skill referansi bos")

    for capability in skill.required_capabilities:
        if capability not in known_capabilities:
            errors.append(f"{skill.skill_id}: bilinmeyen capability '{capability}'")

    return SkillValidation(ok=not errors, errors=tuple(errors))


# --- dynamic input -----------------------------------------------------


def _lookup(scope: dict[str, Any], path: str) -> Any:
    current: Any = scope
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def extract_output(value: Any, path: str) -> Any:
    """Pull `path` out of a tool result, or return it unchanged when empty."""
    if not path:
        return value
    if isinstance(value, dict):
        return _lookup(value, path)
    return getattr(value, path, None)


def resolve_value(value: Any, scope: dict[str, Any]) -> Any:
    """Replace {{ references }} using the running scope.

    A reference that is the whole string yields the raw object, so structured
    tool output survives instead of being stringified.
    """
    if isinstance(value, dict):
        return {key: resolve_value(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, scope) for item in value]
    if not isinstance(value, str):
        return value

    whole = _REFERENCE.fullmatch(value.strip())
    if whole:
        return _lookup(scope, whole.group(1))

    def substitute(match: re.Match[str]) -> str:
        found = _lookup(scope, match.group(1))
        return "" if found is None else str(found)

    return _REFERENCE.sub(substitute, value)
