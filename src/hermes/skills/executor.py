"""Runs a declarative skill through the existing execution machinery.

Nothing here re-implements execution. Each step resolves a capability to a tool
(Phase B), runs it through ToolExecutor so policy, approval and verification
apply (Phase D), falls back to RecoveryEngine on failure, and is bounded by
ExecutionGuard (Phase C). This module only sequences those parts and carries
data between steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from hermes.mission.execution_guard import (
    DEFAULT_LIMITS,
    ExecutionGuard,
    GuardLimits,
    GuardVerdict,
)
from hermes.mission.models import Mission, MissionStep
from hermes.mission.recovery import FailureKind, RecoveryEngine
from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.skills.loader import load_executable_skills
from hermes.skills.models import (
    Skill,
    SkillStep,
    SkillStepKind,
    SuccessCriteria,
    extract_output,
    resolve_value,
)
from hermes.tools.capabilities import select_tool_for_capability
from hermes.tools.executor import ToolApprovalRequiredError, ToolExecutor
from hermes.tools.registry import ToolRegistry
from hermes.tools.verifiers.registry import is_read_only_observe_tool

logger = structlog.get_logger(__name__)

_CONTEXT_FIELDS = (
    "active_file",
    "active_folder",
    "last_created_file",
    "last_opened_file",
    "last_verified_file",
    "last_url",
    "last_browser_url",
)


@dataclass
class SkillStepResult:
    step_id: str
    label: str
    capability: str = ""
    tool_name: str = ""
    success: bool = False
    skipped: bool = False
    output: Any = None
    verification_status: str = ""
    recovered: bool = False
    error: str = ""

    @property
    def failed(self) -> bool:
        return not self.success and not self.skipped


@dataclass
class SkillOutcome:
    skill_id: str
    success: bool = False
    summary: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    steps: list[SkillStepResult] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    requires_approval: bool = False
    guard_stop: str = ""

    @property
    def failed_step(self) -> SkillStepResult | None:
        return next((step for step in self.steps if step.failed), None)


def _context_scope(context: Any) -> dict[str, Any]:
    if context is None:
        return {}
    scope = {name: getattr(context, name, None) for name in _CONTEXT_FIELDS}
    recent = getattr(context, "recent_files", None)
    scope["recent_files"] = list(recent or [])
    return scope


class SkillExecutor:
    """Executes skills. Selection of *which* skill belongs to the caller."""

    def __init__(
        self,
        registry: ToolRegistry,
        tool_executor: ToolExecutor,
        *,
        skills: dict[str, Skill] | None = None,
        recovery: RecoveryEngine | None = None,
        guard_limits: GuardLimits | None = None,
    ) -> None:
        self._registry = registry
        self._executor = tool_executor
        self._guard_limits = guard_limits or DEFAULT_LIMITS
        self._recovery = recovery or RecoveryEngine(
            registry=registry,
            evaluate_policy=tool_executor._policy.evaluate,  # noqa: SLF001
        )
        if skills is None:
            skills, rejected = load_executable_skills(set(registry.capabilities()))
            for skill_id, validation in rejected.items():
                logger.warning(
                    "skill_rejected", skill=skill_id, errors=list(validation.errors)
                )
        self._skills = skills

    # --- discovery ------------------------------------------------------

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def for_logical_kind(self, logical_kind: str) -> Skill | None:
        """A skill that supersedes a legacy mission handler, if one exists."""
        if not logical_kind:
            return None
        return next(
            (s for s in self._skills.values() if s.legacy_logical_kind == logical_kind),
            None,
        )

    def runnable_skills(self) -> list[Skill]:
        """Skills whose every required capability is provided by some tool."""
        available = set(self._registry.capabilities())
        return [
            skill
            for skill in self._skills.values()
            if set(skill.required_capabilities) <= available
        ]

    # --- execution ------------------------------------------------------

    async def execute(
        self,
        skill_id: str,
        *,
        inputs: dict[str, Any] | None = None,
        mission: Mission | None = None,
        context: Any = None,
        run_id: str = "",
    ) -> SkillOutcome:
        skill = self._skills.get(skill_id)
        if skill is None:
            return SkillOutcome(
                skill_id=skill_id,
                summary=f"'{skill_id}' adinda bir islem tanimli degil.",
            )

        carrier = mission or Mission(mission_id=f"skill:{skill_id}", user_goal=skill.title)
        guard = ExecutionGuard(carrier, self._guard_limits)
        resolved_inputs = dict(inputs or {})

        verdict = guard.check_skill(skill_id, resolved_inputs)
        if not verdict.allowed:
            return self._stopped(skill_id, verdict)

        guard.enter_skill(skill_id, resolved_inputs)
        try:
            return await self._run_steps(
                skill,
                guard=guard,
                carrier=carrier,
                inputs=resolved_inputs,
                context=context,
                run_id=run_id,
            )
        finally:
            guard.exit_skill()

    def _stopped(self, skill_id: str, verdict: GuardVerdict) -> SkillOutcome:
        return SkillOutcome(
            skill_id=skill_id,
            summary=verdict.reason,
            guard_stop=str(verdict.stop or ""),
            user_messages=[verdict.reason],
        )

    async def _run_steps(
        self,
        skill: Skill,
        *,
        guard: ExecutionGuard,
        carrier: Mission,
        inputs: dict[str, Any],
        context: Any,
        run_id: str,
    ) -> SkillOutcome:
        outcome = SkillOutcome(skill_id=skill.skill_id, success=True)
        scope: dict[str, Any] = {
            "inputs": inputs,
            "steps": {},
            "context": _context_scope(context),
        }

        for step in skill.steps:
            if step.condition and not resolve_value(step.condition, scope):
                outcome.steps.append(
                    SkillStepResult(step_id=step.step_id, label=step.label, skipped=True)
                )
                continue

            if step.kind is SkillStepKind.SKILL:
                result = await self._run_nested(
                    step, guard=guard, carrier=carrier, scope=scope,
                    context=context, run_id=run_id, outcome=outcome,
                )
            else:
                result = await self._run_capability_step(
                    step, guard=guard, carrier=carrier, scope=scope, run_id=run_id,
                    outcome=outcome,
                )

            outcome.steps.append(result)
            scope["steps"][step.step_id] = {"output": result.output}
            if step.output:
                outcome.outputs[step.output] = result.output

            if result.failed and not step.optional:
                outcome.success = False
                outcome.summary = result.error or f"{result.label} tamamlanamadi."
                return outcome

        outcome.summary = outcome.summary or f"{skill.title or skill.skill_id} tamamlandi."
        return outcome

    async def _run_nested(
        self,
        step: SkillStep,
        *,
        guard: ExecutionGuard,
        carrier: Mission,
        scope: dict[str, Any],
        context: Any,
        run_id: str,
        outcome: SkillOutcome,
    ) -> SkillStepResult:
        nested_inputs = resolve_value(dict(step.inputs), scope)
        nested = await self.execute(
            step.skill_id,
            inputs=nested_inputs,
            mission=carrier,
            context=context,
            run_id=run_id,
        )
        outcome.user_messages.extend(nested.user_messages)
        if nested.requires_approval:
            outcome.requires_approval = True
        if nested.guard_stop:
            outcome.guard_stop = nested.guard_stop
        return SkillStepResult(
            step_id=step.step_id,
            label=step.label,
            success=nested.success,
            output=nested.outputs,
            error="" if nested.success else nested.summary,
        )

    async def _run_capability_step(
        self,
        step: SkillStep,
        *,
        guard: ExecutionGuard,
        carrier: Mission,
        scope: dict[str, Any],
        run_id: str,
        outcome: SkillOutcome,
    ) -> SkillStepResult:
        result = SkillStepResult(
            step_id=step.step_id, label=step.label, capability=step.capability
        )
        step_inputs = resolve_value(dict(step.inputs), scope)

        selection = select_tool_for_capability(self._registry, step.capability, step_inputs)
        if selection is None:
            result.error = (
                f"{step.label} icin '{step.capability}' yetenegini saglayan "
                "kullanilabilir bir arac bulamadim."
            )
            return result
        result.tool_name = selection.tool_name

        verdict = guard.check_action(selection.tool_name, selection.arguments)
        if not verdict.allowed:
            outcome.guard_stop = str(verdict.stop or "")
            outcome.user_messages.append(verdict.reason)
            result.error = verdict.reason
            return result

        mission_step = MissionStep(
            step_id=f"{outcome.skill_id}:{step.step_id}",
            title=step.label,
            tool_name=selection.tool_name,
            tool_arguments=dict(selection.arguments),
            metadata={"skill_id": outcome.skill_id, "capability": step.capability},
        )

        try:
            payload = await self._execute_tool(
                selection.tool_name, selection.arguments, run_id
            )
        except ToolApprovalRequiredError as exc:
            # A skill must never talk its way past the approval gate.
            outcome.requires_approval = True
            result.error = f"{step.label} icin onayin gerekiyor: {exc.reason}"
            outcome.user_messages.append(result.error)
            return result

        guard.record_action(
            selection.tool_name, selection.arguments, made_progress=payload.success
        )
        result.output = extract_output(payload.output, step.output_from)
        result.verification_status = payload.verification_status or ""

        if not payload.success:
            recovered, payload = await self._recover(
                carrier, mission_step, payload, run_id=run_id, outcome=outcome
            )
            if not recovered:
                if outcome.requires_approval:
                    result.error = f"{step.label} icin onayin gerekiyor."
                else:
                    result.error = payload.error or f"{step.label} basarisiz oldu."
                return result
            result.recovered = True
            result.output = extract_output(payload.output, step.output_from)
            result.verification_status = payload.verification_status or ""

        criteria_error = await self._check_criteria(
            step.success_criteria, result.output, scope, run_id
        )
        if criteria_error:
            result.error = f"{step.label}: {criteria_error}"
            return result

        result.success = True
        return result

    async def _execute_tool(
        self, tool_name: str, arguments: dict[str, Any], run_id: str
    ) -> ToolResultPayload:
        call = ToolCallRequest(name=tool_name, arguments=dict(arguments))
        return await self._executor.execute_tool_call(call, run_id=run_id)

    async def _recover(
        self,
        carrier: Mission,
        mission_step: MissionStep,
        payload: ToolResultPayload,
        *,
        run_id: str,
        outcome: SkillOutcome,
    ) -> tuple[bool, ToolResultPayload]:
        """Hand failure to the existing recovery engine rather than retrying here.

        The engine runs the corrective action itself, so the successful payload
        is captured from the callback instead of re-executing the tool.
        """
        attempts: list[ToolResultPayload] = []

        async def execute_tool(name: str, arguments: dict[str, Any], rid: str):
            attempt = await self._execute_tool(name, arguments, rid)
            attempts.append(attempt)
            return attempt

        async def verify(step: MissionStep, result: Any, rid: str) -> dict[str, Any]:
            status = getattr(result, "verification_status", None)
            if status:
                return {"verification_status": status}
            return {
                "verification_status": "verified"
                if getattr(result, "success", False)
                else "failed"
            }

        # A step that ran but could not be confirmed needs different strategies
        # than one that never ran at all.
        failure_kind = (
            FailureKind.VERIFICATION
            if payload.verification_status == "failed"
            else FailureKind.EXECUTION
        )

        recovery = await self._recovery.attempt_recovery(
            carrier,
            mission_step,
            failure_kind=failure_kind,
            error_text=payload.error or "",
            last_result=payload,
            verification_details=payload.verification_details or {},
            run_id=run_id,
            execute_tool=execute_tool,
            verify=verify,
        )
        outcome.user_messages.extend(recovery.user_messages)
        if recovery.requires_approval:
            outcome.requires_approval = True
        if not recovery.recovered:
            if recovery.last_error:
                payload = payload.model_copy(update={"error": recovery.last_error})
            return False, payload
        succeeded = next((item for item in reversed(attempts) if item.success), None)
        if succeeded is None:
            return False, payload
        return True, succeeded

    async def _check_criteria(
        self,
        criteria: SuccessCriteria,
        output: Any,
        scope: dict[str, Any],
        run_id: str,
    ) -> str:
        """Empty string means satisfied; otherwise a human-readable reason."""
        if criteria.is_empty:
            return ""

        observed = output
        if criteria.observe_capability:
            observed = await self._observe(criteria, scope, run_id)
            if observed is None:
                return (
                    f"'{criteria.observe_capability}' ile durumu dogrulayamadim."
                )

        if criteria.path_exists:
            target = resolve_value(criteria.path_exists, scope)
            if not target or not Path(str(target)).expanduser().exists():
                return f"beklenen dosya olusmadi ({target})."

        if criteria.output_not_empty and not observed:
            return "beklenen cikti bos geldi."

        if criteria.min_length and len(str(observed or "")) < criteria.min_length:
            return (
                f"cikti beklenenden kisa ({len(str(observed or ''))} < {criteria.min_length})."
            )

        if criteria.output_contains:
            expected = str(resolve_value(criteria.output_contains, scope) or "")
            if expected and expected.casefold() not in str(observed or "").casefold():
                return f"beklenen icerik bulunamadi ({expected})."

        return ""

    async def _observe(
        self, criteria: SuccessCriteria, scope: dict[str, Any], run_id: str
    ) -> Any:
        """Re-read the world with a read-only capability to judge the step."""
        observe_inputs = resolve_value(dict(criteria.observe_inputs), scope)
        selection = select_tool_for_capability(
            self._registry, criteria.observe_capability, observe_inputs
        )
        if selection is None:
            return None
        tool = self._registry.get(selection.tool_name)
        if tool is None or not is_read_only_observe_tool(
            selection.tool_name, tool.get_definition().risk_level
        ):
            return None
        call = ToolCallRequest(name=selection.tool_name, arguments=selection.arguments)
        payload = await self._executor.execute_tool_call(
            call, run_id=run_id, skip_approval=True
        )
        return payload.output if payload.success else None
