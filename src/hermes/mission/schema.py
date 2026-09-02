from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.mission.models import MissionStep


class StepAction(StrEnum):
    TOOL = "tool"
    LOGICAL = "logical"


PLAN_JSON_EXAMPLE = {
    "steps": [
        {
            "step_id": "inspect_system",
            "title": "Sistem bilgisini topla",
            "action": "tool",
            "tool_name": "get_system_info",
            "tool_arguments": {},
            "depends_on": [],
            "risk_level": "read_only",
            "expected_result": "OS ve donanim bilgisi",
            "verification": {"required": True, "method": "output_present"},
        },
        {
            "step_id": "determine_missing",
            "title": "Eksik yazilimlari belirle",
            "action": "logical",
            "depends_on": ["inspect_system"],
            "expected_result": "Kurulacak paket listesi",
            "verification": {"required": False},
        },
    ]
}


@dataclass
class PlanningContext:
    user_goal: str
    system_state: dict[str, Any]
    tools: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    relevant_context: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "user_goal": self.user_goal,
            "system_state": self.system_state,
            "available_tools": self.tools,
            "skills": self.skills,
            "relevant_context": self.relevant_context,
        }


@dataclass
class PlanValidationResult:
    ok: bool
    steps: list[MissionStep] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
