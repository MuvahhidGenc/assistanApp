"""Phase 9 — agent planning data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.agent.local_intent import LocalIntent


class PlanningRoute(StrEnum):
    RESOLVED_INTENT = "resolved_intent"
    FAST_LOCAL = "fast_local"
    LOCAL_SEQUENCE = "local_sequence"
    MISSION = "mission"
    SERVER = "server"
    CLARIFY = "clarify"


class TaskCategory(StrEnum):
    FILESYSTEM = "filesystem"
    APPLICATION = "application"
    SYSTEM = "system"
    ORGANIZATION = "organization"
    INFORMATION = "information"
    MULTI_STEP = "multi_step"
    UNKNOWN = "unknown"


@dataclass
class GoalAnalysis:
    user_intent: str = ""
    desired_state: str = ""
    current_state_summary: str = ""
    missing_information: list[str] = field(default_factory=list)
    requires_filesystem_probe: bool = False
    is_compound: bool = False
    confidence: float = 0.0
    task_category: TaskCategory = TaskCategory.UNKNOWN
    required_capabilities: list[str] = field(default_factory=list)
    verification_criteria: list[str] = field(default_factory=list)


@dataclass
class AgentPlanningDecision:
    route: PlanningRoute = PlanningRoute.MISSION
    clarification: str = ""
    intent: LocalIntent | None = None
    local_sequence: list[LocalIntent] = field(default_factory=list)
    working_context: dict[str, Any] = field(default_factory=dict)
    goal_analysis: GoalAnalysis | None = None
    requires_confirmation: bool = False
    confirmation_reason: str = ""
    progress_hint: str = ""
    source: str = "agent_planner"
