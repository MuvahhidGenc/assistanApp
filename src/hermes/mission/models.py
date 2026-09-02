from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

MISSION_SCHEMA_VERSION = 5


class MissionStatus(StrEnum):
    CREATED = "created"
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    ACTIVE = "active"
    PAUSED = "paused"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_FOR_USER = "waiting_for_user"


_RUNNING_STATUSES = frozenset(
    {
        MissionStatus.RUNNING,
        MissionStatus.ACTIVE,
        MissionStatus.PLANNING,
        MissionStatus.RECOVERING,
    }
)


def normalize_mission_status(raw: str) -> MissionStatus:
    text = (raw or "").strip().lower()
    if text == MissionStatus.ACTIVE.value:
        return MissionStatus.RUNNING
    if text == MissionStatus.PENDING.value:
        return MissionStatus.CREATED
    try:
        return MissionStatus(text)
    except ValueError:
        return MissionStatus.CREATED


def mission_status_is_running(status: MissionStatus) -> bool:
    return status in _RUNNING_STATUSES


class MissionStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    VERIFICATION_FAILED = "verification_failed"


class StepAction(StrEnum):
    TOOL = "tool"
    LOGICAL = "logical"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MissionStep:
    """Single step within a mission. Tool execution still goes through ToolExecutor."""

    step_id: str
    title: str
    status: MissionStepStatus = MissionStepStatus.PENDING
    action: StepAction = StepAction.TOOL
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    risk_level: str | None = None
    expected_result: str | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    execution_status: str | None = None
    observation: dict[str, Any] = field(default_factory=dict)
    verification_status: str | None = None
    verification_method: str | None = None
    verification_details: dict[str, Any] = field(default_factory=dict)
    verified_at: str | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    result_summary: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    argument_bindings: list[dict[str, str]] = field(default_factory=list)
    execution_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "status": self.status.value,
            "action": self.action.value,
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "depends_on": self.depends_on,
            "risk_level": self.risk_level,
            "expected_result": self.expected_result,
            "verification": self.verification,
            "execution_status": self.execution_status,
            "observation": self.observation,
            "verification_status": self.verification_status,
            "verification_method": self.verification_method,
            "verification_details": self.verification_details,
            "verified_at": self.verified_at,
            "observations": self.observations,
            "result_summary": self.result_summary,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
            "argument_bindings": self.argument_bindings,
            "execution_target": self.execution_target,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionStep:
        status_raw = str(data.get("status", MissionStepStatus.PENDING.value))
        try:
            status = MissionStepStatus(status_raw)
        except ValueError:
            status = MissionStepStatus.PENDING
        action_raw = str(data.get("action") or StepAction.TOOL.value)
        try:
            action = StepAction(action_raw)
        except ValueError:
            action = StepAction.TOOL if data.get("tool_name") else StepAction.LOGICAL
        depends_on = [str(item) for item in (data.get("depends_on") or [])]
        verification = dict(data.get("verification") or {})
        bindings_raw = data.get("argument_bindings") or []
        argument_bindings = [
            {str(k): str(v) for k, v in item.items()}
            for item in bindings_raw
            if isinstance(item, dict)
        ]
        return cls(
            step_id=str(data.get("step_id") or uuid4().hex[:12]),
            title=str(data.get("title") or ""),
            status=status,
            action=action,
            tool_name=data.get("tool_name"),
            tool_arguments=dict(data.get("tool_arguments") or {}),
            depends_on=depends_on,
            risk_level=data.get("risk_level"),
            expected_result=data.get("expected_result"),
            verification=verification,
            execution_status=data.get("execution_status"),
            observation=dict(data.get("observation") or {}),
            verification_status=data.get("verification_status"),
            verification_method=data.get("verification_method"),
            verification_details=dict(data.get("verification_details") or {}),
            verified_at=data.get("verified_at"),
            observations=list(data.get("observations") or []),
            result_summary=data.get("result_summary"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            metadata=dict(data.get("metadata") or {}),
            argument_bindings=argument_bindings,
            execution_target=data.get("execution_target"),
        )


@dataclass
class Mission:
    """
    Versioned, persistable mission state.

    Designed for future memory integration (working / episodic / long-term).
    """

    mission_id: str
    user_goal: str
    status: MissionStatus = MissionStatus.PENDING
    schema_version: int = MISSION_SCHEMA_VERSION
    current_step_id: str | None = None
    steps: list[MissionStep] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    recovery_attempts: list[dict[str, Any]] = field(default_factory=list)
    important_decisions: list[dict[str, Any]] = field(default_factory=list)
    user_interventions: list[dict[str, Any]] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    working_context: dict[str, Any] = field(default_factory=dict)
    summary: str | None = None
    plan_source: str | None = None
    plan_created_at: str | None = None
    plan_validated: bool = False
    recovery_strategy: str | None = None
    recovery_reason: str | None = None
    recovery_result: str | None = None
    recovery_started_at: str | None = None
    recovery_finished_at: str | None = None
    waiting_for_user_reason: str | None = None
    failed_step_id: str | None = None
    recovery_count: int = 0
    last_error: str | None = None
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps(self) -> int:
        from hermes.mission.models import MissionStepStatus

        return sum(1 for step in self.steps if step.status == MissionStepStatus.COMPLETED)

    @property
    def failed_step(self) -> MissionStep | None:
        from hermes.mission.models import MissionStepStatus

        if self.failed_step_id:
            for step in self.steps:
                if step.step_id == self.failed_step_id:
                    return step
        for step in self.steps:
            if step.status in (MissionStepStatus.FAILED, MissionStepStatus.VERIFICATION_FAILED):
                return step
        return None

    @property
    def original_goal(self) -> str:
        return self.user_goal

    @property
    def parsed_goal(self) -> dict[str, Any]:
        raw = self.working_context.get("parsed_goal")
        return dict(raw) if isinstance(raw, dict) else {}

    @property
    def current_step(self) -> MissionStep | None:
        if not self.current_step_id:
            return None
        for step in self.steps:
            if step.step_id == self.current_step_id:
                return step
        return None

    @property
    def progress_ratio(self) -> float:
        if not self.steps:
            if self.status == MissionStatus.COMPLETED:
                return 1.0
            if self.status in (MissionStatus.RUNNING, MissionStatus.ACTIVE, MissionStatus.PLANNING):
                return 0.0
            return 0.0
        completed = sum(1 for step in self.steps if step.status == MissionStepStatus.COMPLETED)
        return completed / len(self.steps)

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mission_id": self.mission_id,
            "user_goal": self.user_goal,
            "status": (
                MissionStatus.RUNNING.value
                if self.status in (MissionStatus.RUNNING, MissionStatus.ACTIVE)
                else self.status.value
            ),
            "current_step_id": self.current_step_id,
            "steps": [step.to_dict() for step in self.steps],
            "observations": self.observations,
            "tool_results": self.tool_results,
            "errors": self.errors,
            "recovery_attempts": self.recovery_attempts,
            "important_decisions": self.important_decisions,
            "user_interventions": self.user_interventions,
            "memory_refs": self.memory_refs,
            "working_context": self.working_context,
            "summary": self.summary,
            "plan_source": self.plan_source,
            "plan_created_at": self.plan_created_at,
            "plan_validated": self.plan_validated,
            "recovery_strategy": self.recovery_strategy,
            "recovery_reason": self.recovery_reason,
            "recovery_result": self.recovery_result,
            "recovery_started_at": self.recovery_started_at,
            "recovery_finished_at": self.recovery_finished_at,
            "waiting_for_user_reason": self.waiting_for_user_reason,
            "failed_step_id": self.failed_step_id,
            "recovery_count": self.recovery_count,
            "last_error": self.last_error,
            "context_snapshot": self.context_snapshot,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mission:
        status_raw = str(data.get("status", MissionStatus.CREATED.value))
        status = normalize_mission_status(status_raw)
        steps_raw = data.get("steps") or []
        steps = [MissionStep.from_dict(item) for item in steps_raw if isinstance(item, dict)]
        return cls(
            mission_id=str(data.get("mission_id") or uuid4().hex),
            user_goal=str(data.get("user_goal") or ""),
            status=status,
            schema_version=int(data.get("schema_version") or MISSION_SCHEMA_VERSION),
            current_step_id=data.get("current_step_id"),
            steps=steps,
            observations=list(data.get("observations") or []),
            tool_results=list(data.get("tool_results") or []),
            errors=list(data.get("errors") or []),
            recovery_attempts=list(data.get("recovery_attempts") or []),
            important_decisions=list(data.get("important_decisions") or []),
            user_interventions=list(data.get("user_interventions") or []),
            memory_refs=[str(ref) for ref in (data.get("memory_refs") or [])],
            working_context=dict(data.get("working_context") or {}),
            summary=data.get("summary"),
            plan_source=data.get("plan_source"),
            plan_created_at=data.get("plan_created_at"),
            plan_validated=bool(data.get("plan_validated") or data.get("metadata", {}).get("plan_validated")),
            recovery_strategy=data.get("recovery_strategy"),
            recovery_reason=data.get("recovery_reason"),
            recovery_result=data.get("recovery_result"),
            recovery_started_at=data.get("recovery_started_at"),
            recovery_finished_at=data.get("recovery_finished_at"),
            waiting_for_user_reason=data.get("waiting_for_user_reason"),
            failed_step_id=data.get("failed_step_id"),
            recovery_count=int(data.get("recovery_count") or 0),
            last_error=data.get("last_error"),
            context_snapshot=dict(data.get("context_snapshot") or {}),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
            metadata=dict(data.get("metadata") or {}),
        )

    @classmethod
    def create(cls, user_goal: str, *, initial_steps: list[MissionStep] | None = None) -> Mission:
        mission_id = uuid4().hex
        steps = initial_steps or []
        current_step_id = steps[0].step_id if steps else None
        return cls(
            mission_id=mission_id,
            user_goal=user_goal.strip(),
            status=MissionStatus.RUNNING if steps else MissionStatus.CREATED,
            current_step_id=current_step_id,
            steps=steps,
        )
