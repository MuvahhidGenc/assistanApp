"""Turning an understood goal into something the existing engine can run.

The model produces capabilities, never tool names; this module resolves those
capabilities against the registry (Phase B), prefers a declared skill when one
covers the goal (Phase E), and otherwise emits ordinary MissionSteps. From
there the normal chain applies unchanged: policy, approval, guard, verify,
recover.

The live probe showed `required_capabilities` is a scope hint rather than a
step list — it can carry an extra capability the sentence never asked for. A
capability becomes a step only when its arguments can be supplied now or
bound from a prior step's structured output. Anything still unfillable is a
question, not a partial plan that later reports success.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.context.entity_decision import Confidence
from hermes.intent.models import AgentIntent, IntentStep, canonical_required_capabilities
from hermes.mission.models import MissionStep, StepAction
from hermes.tools.capabilities import (
    bindable_fields,
    capability_risk_floor,
    select_tool_accepting,
    select_tool_for_capability,
)
from hermes.tools.registry import ToolRegistry

# Type-specific session facts. Historical file paths are not dumped as `path`.
_CONTEXT_INPUTS: tuple[tuple[str, str], ...] = (
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
        focus = getattr(context, "active_focus", None)
        focus_id = getattr(focus, "identifier", None) if focus is not None else None
        focus_type = getattr(focus, "type", "") if focus is not None else ""
        invalidated = getattr(context, "is_invalidated", None)
        if focus_id and not (callable(invalidated) and invalidated(focus_id)):
            if focus_type == "file":
                pool["path"] = focus_id
            elif focus_type in {"url", "browser_page"}:
                pool["url"] = focus_id
            elif focus_type == "screen_entity":
                pool["session_entity_id"] = focus_id
        if focus_type == "screen_entity" or focus is None:
            session_id = getattr(context, "last_screen_entity_id", None)
            if session_id:
                pool.setdefault("session_entity_id", session_id)
        for key, attribute in _CONTEXT_INPUTS:
            if key in pool:
                continue
            value = getattr(context, attribute, None)
            if value:
                pool[key] = value

    for key, value in intent.references.items():
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


def _canonical_intent_steps(intent: AgentIntent) -> tuple[IntentStep, ...]:
    """Plan steps first, then required capabilities the plan omitted."""
    ordered: list[IntentStep] = list(intent.plan)
    present = {step.capability for step in ordered}
    for capability in intent.required_capabilities:
        if capability not in present:
            ordered.append(IntentStep(capability=capability))
            present.add(capability)
    if ordered:
        return tuple(ordered)
    return tuple(
        IntentStep(capability=capability)
        for capability in canonical_required_capabilities(intent)
    )


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
            c for c in canonical_required_capabilities(intent) if c not in available
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
        skill_pool = dict(pool)
        for planned in intent.plan:
            for key, value in planned.inputs.items():
                if value:
                    skill_pool[str(key)] = value
        needs_approval = confidence is Confidence.RISKY

        skill = self.match_skill(intent, skill_pool)
        if skill is not None:
            return RoutedPlan(
                kind=RouteKind.SKILL,
                skill_id=skill.skill_id,
                skill_inputs=skill_pool,
                needs_approval=needs_approval,
            )

        steps, unfillable = self._build_capability_steps(intent, pool)
        if not steps and any(str(item).startswith("do_not:") for item in intent.constraints):
            return RoutedPlan(
                kind=RouteKind.CONVERSATION,
                question=intent.reply or "Tamam, onu yapmayacagim.",
                reason="yasaklanan_eylem",
            )

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

        if unfillable:
            missing = ", ".join(unfillable)
            return RoutedPlan(
                kind=RouteKind.QUESTION,
                question=intent.clarifying_question
                or (
                    "Bu gorevin zorunlu bir adimini su an tamamlayamam: "
                    f"{missing}. Eksik bilgiyi netlestirir misin?"
                ),
                reason="zorunlu yetenek cozulemedi",
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
        """One step per canonical required capability, in order.

        Plan steps keep their inputs. Capabilities listed only in
        required_capabilities are appended so a short plan cannot shrink
        the goal. Unfillable leftovers become a question, not a partial plan.
        """
        planned = _canonical_intent_steps(intent)
        forbidden = {
            item.split(":", 1)[1].strip()
            for item in intent.constraints
            if str(item).startswith("do_not:") and ":" in str(item)
        }

        steps: list[MissionStep] = []
        unfillable: list[str] = []
        previous_id = ""

        for index, planned_step in enumerate(planned):
            capability = planned_step.capability
            if capability in forbidden:
                continue
            arguments = {**pool, **planned_step.inputs}
            selection = select_tool_for_capability(
                self._registry, capability, arguments
            )
            bindings: list[dict[str, str]] = []
            if selection is None:
                bound = self._bind_from_prior_steps(capability, steps)
                if bound is None:
                    unfillable.append(capability)
                    continue
                tool_name, bindings = bound
                selection_name = tool_name
                selection_args: dict[str, Any] = {}
            else:
                selection_name = selection.tool_name
                selection_args = dict(selection.arguments)
                extra = self._supplement_screen_state_binding(capability, steps)
                if extra:
                    bindings = list(bindings) + extra

            risk = capability_risk_floor(self._registry, capability)
            # A change to the machine with nothing to act on has no safe
            # meaning: control_service with no service, kill_process with no
            # pid. Sixteen tools still declare an empty input schema, so this
            # cannot be caught by argument fitting alone.
            if (
                not selection_args
                and not bindings
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
                    tool_name=selection_name,
                    tool_arguments=selection_args,
                    depends_on=[previous_id] if previous_id else [],
                    risk_level=risk.value if risk is not None else None,
                    expected_result=intent.expected_outcome,
                    argument_bindings=bindings,
                    metadata={
                        "source": "intent_router",
                        "capability": capability,
                        "goal": intent.goal,
                        "deferred_binding": bool(bindings),
                    },
                )
            )
            previous_id = step_id

        return steps, tuple(unfillable)

    def _supplement_screen_state_binding(
        self, capability: str, prior_steps: list[MissionStep]
    ) -> list[dict[str, str]]:
        """Bind the latest observe snapshot into resolve even when reference is known."""
        if capability != "screen.resolve":
            return []
        for prior in reversed(prior_steps):
            producer = str((prior.metadata or {}).get("capability") or "")
            if producer != "screen.observe":
                continue
            if "screen_state" not in bindable_fields(producer, capability, self._registry):
                continue
            return [
                {
                    "argument": "screen_state",
                    "source_step_id": prior.step_id,
                    "source_field": "screen_state",
                }
            ]
        return []

    def _bind_from_prior_steps(
        self, capability: str, prior_steps: list[MissionStep]
    ) -> tuple[str, list[dict[str, str]]] | None:
        """Bind a required input from a previous step's structured output.

        Field names are conventional (path, url, content). No tool-name switch.
        """
        for prior in reversed(prior_steps):
            producer = str((prior.metadata or {}).get("capability") or "")
            fields = bindable_fields(producer, capability, self._registry)
            if not fields:
                continue
            tool_name = select_tool_accepting(self._registry, capability, fields[0])
            if not tool_name:
                continue
            return (
                tool_name,
                [
                    {
                        "argument": field,
                        "source_step_id": prior.step_id,
                        "source_field": field,
                    }
                    for field in fields
                ],
            )
        return None
