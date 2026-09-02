from __future__ import annotations

import json
import re
from typing import Any

from hermes.mission.models import MissionStep, StepAction
from hermes.mission.schema import PlanValidationResult
from hermes.tools.execution_target import canonical_execution_target
from hermes.tools.manifest import input_schema_for_tool
from hermes.tools.registry import ToolRegistry

_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_plan_json(text: str) -> list[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return []
    candidates = [raw]
    for match in _JSON_BLOCK.finditer(raw):
        candidates.insert(0, match.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
            return [item for item in parsed["steps"] if isinstance(item, dict)]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict) and isinstance(parsed.get("steps"), list):
                return [item for item in parsed["steps"] if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
    return []


def _validate_arguments(tool_name: str, arguments: dict[str, Any], registry: ToolRegistry) -> list[str]:
    errors: list[str] = []
    if not isinstance(arguments, dict):
        return [f"{tool_name}: tool_arguments must be an object"]

    tool = registry.get(tool_name)
    schema = input_schema_for_tool(tool_name, tool)
    required = schema.get("required") or []
    properties = schema.get("properties") or {}

    for field in required:
        if field not in arguments:
            errors.append(f"{tool_name}: missing required argument '{field}'")

    for key, value in arguments.items():
        if key not in properties:
            continue
        expected = properties[key] or {}
        expected_type = expected.get("type")
        if expected_type == "string" and not isinstance(value, str):
            errors.append(f"{tool_name}: argument '{key}' must be string")
        elif expected_type == "integer" and not isinstance(value, int):
            errors.append(f"{tool_name}: argument '{key}' must be integer")
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(f"{tool_name}: argument '{key}' must be boolean")
        elif expected_type == "array" and not isinstance(value, list):
            errors.append(f"{tool_name}: argument '{key}' must be array")
        elif expected_type == "object" and not isinstance(value, dict):
            errors.append(f"{tool_name}: argument '{key}' must be object")

    return errors


def _detect_dependency_cycle(step_ids: set[str], depends_map: dict[str, list[str]]) -> str | None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> str | None:
        if node in visiting:
            return node
        if node in visited:
            return None
        visiting.add(node)
        for dep in depends_map.get(node, []):
            found = visit(dep)
            if found:
                return found
        visiting.remove(node)
        visited.add(node)
        return None

    for step_id in step_ids:
        cycle = visit(step_id)
        if cycle:
            return cycle
    return None


def validate_plan_steps(raw_steps: list[dict[str, Any]], registry: ToolRegistry) -> PlanValidationResult:
    errors: list[str] = []
    if not raw_steps:
        return PlanValidationResult(ok=False, errors=["Plan contains no steps"])

    seen_ids: set[str] = set()
    depends_map: dict[str, list[str]] = {}
    mission_steps: list[MissionStep] = []

    for index, raw in enumerate(raw_steps, start=1):
        step_id = str(raw.get("step_id") or "").strip()
        if not step_id:
            errors.append(f"Step {index}: missing step_id")
            continue
        if step_id in seen_ids:
            errors.append(f"Step {index}: duplicate step_id '{step_id}'")
            continue
        seen_ids.add(step_id)

        title = str(raw.get("title") or "").strip()
        if not title:
            errors.append(f"Step {step_id}: missing title")

        action_raw = str(raw.get("action") or "").strip().lower()
        if action_raw not in ("tool", "logical"):
            if raw.get("tool_name"):
                action_raw = "tool"
            else:
                action_raw = "logical"

        action = StepAction.TOOL if action_raw == "tool" else StepAction.LOGICAL
        tool_name = raw.get("tool_name")
        tool_arguments = dict(raw.get("tool_arguments") or {})
        depends_on = [str(item) for item in (raw.get("depends_on") or []) if str(item).strip()]
        depends_map[step_id] = depends_on

        risk_level = raw.get("risk_level")
        expected_result = raw.get("expected_result")
        verification = dict(raw.get("verification") or {})
        execution_target: str | None = None

        if action == StepAction.TOOL:
            if not tool_name or not isinstance(tool_name, str):
                errors.append(f"Step {step_id}: tool action requires tool_name")
            else:
                tool_name = tool_name.strip()
                definition = next((d for d in registry.list_tools() if d.name == tool_name), None)
                if definition is None:
                    errors.append(f"Step {step_id}: unknown tool '{tool_name}'")
                else:
                    canonical = canonical_execution_target(tool_name, registry)
                    execution_target = canonical.value
                    planner_target = raw.get("execution_target")
                    if planner_target and str(planner_target).lower() != canonical.value:
                        errors.append(
                            f"Step {step_id}: planner cannot override execution_target for '{tool_name}'"
                        )
                    if risk_level and str(risk_level) != definition.risk_level.value:
                        errors.append(
                            f"Step {step_id}: risk_level mismatch for '{tool_name}'"
                        )
                    errors.extend(_validate_arguments(tool_name, tool_arguments, registry))
                    if tool_name == "search_files":
                        verification.setdefault("required", True)
                        verification["method"] = "search_files_filesystem"
                    if tool_name == "copy_file":
                        verification.setdefault("required", True)
                        verification["method"] = "copy_file_filesystem"
        else:
            if tool_name:
                errors.append(f"Step {step_id}: logical action must not include tool_name")

        mission_steps.append(
            MissionStep(
                step_id=step_id,
                title=title or step_id,
                action=action,
                tool_name=str(tool_name).strip() if tool_name else None,
                tool_arguments=tool_arguments,
                depends_on=depends_on,
                risk_level=str(risk_level) if risk_level else None,
                expected_result=str(expected_result) if expected_result else None,
                verification=verification,
                execution_target=execution_target,
                metadata=dict(raw.get("metadata") or {}),
                argument_bindings=list(raw.get("argument_bindings") or []),
            )
        )

    for step_id, deps in depends_map.items():
        for dep in deps:
            if dep not in seen_ids:
                errors.append(f"Step {step_id}: depends_on unknown step '{dep}'")

    cycle = _detect_dependency_cycle(seen_ids, depends_map)
    if cycle:
        errors.append(f"Plan dependency cycle detected at '{cycle}'")

    return PlanValidationResult(ok=not errors, steps=mission_steps, errors=errors)


def validate_mission_steps(steps: list[MissionStep], registry: ToolRegistry) -> PlanValidationResult:
    raw = [step.to_dict() for step in steps]
    return validate_plan_steps(raw, registry)
