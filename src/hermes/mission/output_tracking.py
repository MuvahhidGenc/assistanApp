from __future__ import annotations

import re

from hermes.mission.models import Mission, MissionStep, MissionStepStatus

OUTPUT_PRODUCING_TOOLS = frozenset({"write_file", "create_word_document"})

_COUNT_WORDS = {
    "iki": 2,
    "2": 2,
    "üç": 3,
    "uc": 3,
    "3": 3,
    "dört": 4,
    "dort": 4,
    "4": 4,
    "beş": 5,
    "bes": 5,
    "5": 5,
}


def infer_expected_outputs(user_goal: str, steps: list[MissionStep] | None = None) -> int | None:
    text = (user_goal or "").casefold()
    match = re.search(
        r"\b(iki|2|üç|uc|3|dört|dort|4|beş|bes|5)\s+(?:adet\s+)?(?:ayrı\s+)?(?:ayri\s+)?(?:dosya|file|çıktı|cikti)\b",
        text,
    )
    if match:
        return _COUNT_WORDS.get(match.group(1), None)

    if steps:
        planned = sum(
            1
            for step in steps
            if step.tool_name in OUTPUT_PRODUCING_TOOLS and step.action.value == "tool"
        )
        if planned > 1:
            return planned
    return None


def initialize_output_tracking(mission: Mission) -> None:
    expected = infer_expected_outputs(mission.user_goal, mission.steps)
    if expected is None:
        return
    mission.working_context["expected_outputs"] = expected
    mission.working_context.setdefault("completed_outputs", 0)


def record_output_completion(mission: Mission, step: MissionStep) -> None:
    if step.tool_name not in OUTPUT_PRODUCING_TOOLS:
        return
    if step.status != MissionStepStatus.COMPLETED:
        return
    mission.working_context["completed_outputs"] = int(
        mission.working_context.get("completed_outputs") or 0
    ) + 1
    records = list(mission.working_context.get("output_records") or [])
    actual_path = None
    output = step.metadata.get("tool_output")
    if isinstance(output, dict):
        actual_path = output.get("path")
    records.append(
        {
            "step_id": step.step_id,
            "tool_name": step.tool_name,
            "path": actual_path or (step.tool_arguments or {}).get("path"),
        }
    )
    mission.working_context["output_records"] = records


def outputs_requirement_met(mission: Mission) -> bool:
    expected = mission.working_context.get("expected_outputs")
    if expected is None:
        return True
    completed = int(mission.working_context.get("completed_outputs") or 0)
    return completed >= int(expected)


def missing_outputs_summary(mission: Mission) -> str:
    expected = int(mission.working_context.get("expected_outputs") or 0)
    completed = int(mission.working_context.get("completed_outputs") or 0)
    return (
        f"Beklenen cikti sayisi tamamlanmadi ({completed}/{expected}). "
        "Mission erken tamamlanmamaliydi."
    )
