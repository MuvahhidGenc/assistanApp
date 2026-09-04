"""Turning an understood goal into something the existing engine can run.

The model produces capabilities, never tool names; this module resolves those
capabilities against the registry (Phase B), prefers a declared skill when one
covers the goal (Phase E), and otherwise emits ordinary MissionSteps. From
there the normal chain applies unchanged: policy, approval, guard, verify,
recover.

The live probe showed `required_capabilities` is a scope hint rather than a
step list — it can carry an extra capability the sentence never asked for. So
a capability is only turned into a step when the arguments for it can actually
be supplied; anything else is reported as missing information, not guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.context.entity_decision import Confidence
from hermes.intent.models import AgentIntent, IntentStep
from hermes.mission.models import MissionStep, StepAction
from hermes.tools.capabilities import capability_risk_floor, select_tool_for_capability
from hermes.tools.registry import ToolRegistry

# Session facts that can stand in for an argument the user did not repeat.
_CONTEXT_INPUTS: tuple[tuple[str, str], ...] = (
    ("path", "active_file"),
    ("path", "last_created_file"),
    ("path", "last_opened_file"),
    ("url", "last_browser_url"),
    ("url", "last_url"),
    ("app", "last_application"),
)


class RouteKind(StrEnum):
    SKILL = "skill"
    CAPABILITY_PLAN = "capability_plan"
    QUESTION = "question"
    UNSUPPORTED = "unsupported"
    CONVERSATION = "conversation"


@dataclass
class RoutedPlan:
    kind: RouteKind
    skill_id: str = ""
    skill_inputs: dict[str, Any] = field(default_factory=dict)
    steps: list[MissionStep] = field(default_factory=list)
    question: str = ""
    reason: str = ""
    unavailable_capabilities: tuple[str, ...] = ()
    unfillable_capabilities: tuple[str, ...] = ()
    alternatives: dict[str, tuple[str, ...]] = field(default_factory=dict)
    needs_approval: bool = False

    @property
    def is_executable(self) -> bool:
        return self.kind in (RouteKind.SKILL, RouteKind.CAPABILITY_PLAN)


def build_input_pool(intent: AgentIntent, context: Any = None) -> dict[str, Any]:
    """Everything that could fill a tool argument, newest evidence first.

    The model's own references win over session state, because the user just
    said them.
    """
    pool: dict[str, Any] = {}

    if context is not None:
        for key, attribute in _CONTEXT_INPUTS:
            if key in pool:
                continue
            value = getattr(context, attribute, None)
            if value:
                pool[key] = value

    for key, value in intent.references.items():
        if value:
            pool[str(key)] = value

    for step in intent.plan:
        for key, value in step.inputs.items():
            if value:
                pool[str(key)] = value

    return pool


def suggest_alternatives(
    registry: ToolRegistry, capability: str, limit: int = 3
) -> tuple[str, ...]:
    """Available capabilities in the same domain as one we cannot provide.

    Derived from the registry, so it stays true as tools come and go.
    """
    domain = str(capability).split(".", 1)[0]
    available = registry.capabilities()
    same_domain = [c for c in available if c.split(".", 1)[0] == domain]
    return tuple(same_domain[:limit])


class IntentRouter:
    """Chooses how an understood goal should be carried out."""

    def __init__(
        self,
        registry: ToolRegistry,
        skills: Any = None,
        *,
        shell_capability: str = "terminal.execute",
    ) -> None:
        self._registry = registry
        self._skills = skills
        self._shell_capability = shell_capability

    def _shell_substitutes_for_a_gap(
        self, steps: list[MissionStep], unfillable: tuple[str, ...]
    ) -> bool:
        """Whether the shell is standing in for a capability we lack.

        Left unguarded the shell absorbs every capability the machine is
        missing: asked to fix a printer, the model returned five run_command
        steps rather than reporting that service control was unavailable,
        which turns capability routing into decoration. Running a command the
        user asked for is one step and leaves no gap behind; compensating for
        a gap means several, or one alongside a capability that went
        unfilled. Neither test looks at the wording.
        """
        shell_steps = [
            step
            for step in steps
            if step.metadata.get("capability") == self._shell_capability
        ]
        if not shell_steps:
            return False
        return len(shell_steps) > 1 or bool(unfillable)

    # --- skill matching -------------------------------------------------

    def match_skill(self, intent: AgentIntent, pool: dict[str, Any]) -> Any:
        """The skill whose declared capabilities the goal fully asks for.

        Matching on capabilities rather than wording is what keeps this from
        becoming another keyword table. A skill is only chosen when every input
        its steps reference is on hand, so a capability match never turns into
        a run that was always going to fail for want of a value.
        """
        if self._skills is None:
            return None
        wanted = set(intent.required_capabilities)
        if not wanted:
            return None

        best, best_coverage = None, 0
        for skill in self._skills.runnable_skills():
            required = set(skill.required_capabilities)
            if not required or not required <= wanted:
                continue
            if any(name not in pool for name in skill.required_inputs):
                continue
            if len(required) > best_coverage:
                best, best_coverage = skill, len(required)
        return best

    # --- routing --------------------------------------------------------

    def route(
        self,
        intent: AgentIntent,
        *,
        confidence: Confidence,
        context: Any = None,
    ) -> RoutedPlan:
        available = set(self._registry.capabilities())
        if intent.mode == "conversation" and not intent.plan and not intent.required_capabilities:
            return RoutedPlan(
                kind=RouteKind.CONVERSATION,
                question=intent.reply
                or intent.expected_outcome
                or "Nasil yardimci olabilirim?",
                reason="sohbet",
            )

        unavailable = tuple(
            c for c in intent.required_capabilities if c not in available
        )
        if unavailable:
            return RoutedPlan(
                kind=RouteKind.UNSUPPORTED,
                reason="gereken yetenek bu bilgisayarda yok",
                unavailable_capabilities=unavailable,
                alternatives={
                    capability: suggest_alternatives(self._registry, capability)
                    for capability in unavailable
                },
            )

        if confidence is Confidence.LOW:
            return RoutedPlan(
                kind=RouteKind.QUESTION,
                question=intent.clarifying_question
                or intent.reply
                or "Ne yapmami istedigini biraz daha acar misin?",
                reason="niyet yeterince anlasilamadi",
            )

        if not intent.required_capabilities and not intent.plan:
            return RoutedPlan(
                kind=RouteKind.QUESTION,
                question=intent.clarifying_question
                or intent.reply
                or "Ne yapmami istedigini biraz daha acar misin?",
                reason="yapilacak is belirtilmedi",
            )

        pool = build_input_pool(intent, context)
        needs_approval = confidence is Confidence.RISKY

        skill = self.match_skill(intent, pool)
        if skill is not None:
            return RoutedPlan(
                kind=RouteKind.SKILL,
                skill_id=skill.skill_id,
                skill_inputs=pool,
                needs_approval=needs_approval,
            )

        steps, unfillable = self._build_capability_steps(intent, pool)

        if self._shell_substitutes_for_a_gap(steps, unfillable):
            steps = [
                step
                for step in steps
                if step.metadata.get("capability") != self._shell_capability
            ]
            unfillable = (*unfillable, self._shell_capability)

        if intent.needs_user_input and intent.clarifying_question and (
            unfillable or not steps
        ):
            return RoutedPlan(
                kind=RouteKind.QUESTION,
                question=intent.clarifying_question,
                reason="eksik bilgi var",
                unfillable_capabilities=unfillable,
            )

        if not steps:
            return RoutedPlan(
                kind=RouteKind.QUESTION,
                question=intent.clarifying_question
                or "Bu islemi yapabilmem icin biraz daha bilgiye ihtiyacim var.",
                reason="hicbir adim icin yeterli bilgi yok",
                unfillable_capabilities=unfillable,
            )

        return RoutedPlan(
            kind=RouteKind.CAPABILITY_PLAN,
            steps=steps,
            unfillable_capabilities=unfillable,
            needs_approval=needs_approval,
        )

    def _build_capability_steps(
        self, intent: AgentIntent, pool: dict[str, Any]
    ) -> tuple[list[MissionStep], tuple[str, ...]]:
        """One step per planned capability we can actually call, in order.

        The plan is used in preference to the capability summary because the
        summary is a scope hint: live replies listed capabilities the sentence
        never asked for, and running one step per entry produced nonsense.
        """
        planned = intent.plan or tuple(
            IntentStep(capability=capability)
            for capability in intent.required_capabilities
        )

        steps: list[MissionStep] = []
        unfillable: list[str] = []
        previous_id = ""

        for index, planned_step in enumerate(planned):
            capability = planned_step.capability
            arguments = {**pool, **planned_step.inputs}
            selection = select_tool_for_capability(
                self._registry, capability, arguments
            )
            if selection is None:
                unfillable.append(capability)
                continue

            risk = capability_risk_floor(self._registry, capability)
            # A change to the machine with nothing to act on has no safe
            # meaning: control_service with no service, kill_process with no
            # pid. Sixteen tools still declare an empty input schema, so this
            # cannot be caught by argument fitting alone.
            if (
                not selection.arguments
                and risk is not None
                and risk.severity > RiskLevel.READ_ONLY.severity
            ):
                unfillable.append(capability)
                continue

            step_id = f"intent_{index}_{capability.replace('.', '_')}"
            steps.append(
                MissionStep(
                    step_id=step_id,
                    title=f"{capability} yetenegini kullaniyorum",
                    action=StepAction.TOOL,
                    tool_name=selection.tool_name,
                    tool_arguments=dict(selection.arguments),
                    depends_on=[previous_id] if previous_id else [],
                    risk_level=risk.value if risk is not None else None,
                    expected_result=intent.expected_outcome,
                    metadata={
                        "source": "intent_router",
                        "capability": capability,
                        "goal": intent.goal,
                    },
                )
            )
            previous_id = step_id

        return steps, tuple(unfillable)
