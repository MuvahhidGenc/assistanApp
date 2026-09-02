"""Phase 9 — autonomous agent planning layer."""
from __future__ import annotations

from typing import Any

from hermes.agent.goal_parser import parse_goal
from hermes.agent.plan_analysis import analyze_goal, build_agent_state_snapshot
from hermes.agent.plan_models import AgentPlanningDecision, GoalAnalysis, PlanningRoute
from hermes.agent.risk_gate import assess_message_risk
from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence
from hermes.context.conversational_context import ConversationalContext
from hermes.mission.selection import should_create_mission, should_route_to_mission
from hermes.tools.registry import ToolRegistry
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class AgentPlanner:
    """
    Unified planning/routing layer above GoalRouter and Mission Selection.

    Does not replace ReferenceResolver, GoalRouter, or MissionEngine — orchestrates them.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def evaluate(
        self,
        message: str,
        ctx: ConversationalContext,
        *,
        resolved_references: dict[str, str] | None = None,
    ) -> AgentPlanningDecision:
        text = (message or "").strip()
        refs = dict(resolved_references or {})
        parsed = parse_goal(text, ctx, resolved_references=refs)
        parsed_dict = parsed.to_dict()
        state = build_agent_state_snapshot(ctx)
        analysis = analyze_goal(text, ctx, parsed_goal_dict=parsed_dict)
        risk = assess_message_risk(text)

        from hermes.mission.write_content import is_literal_composite_file_mission

        if is_literal_composite_file_mission(text):
            return AgentPlanningDecision(
                route=PlanningRoute.SERVER,
                working_context={
                    "parsed_goal": parsed_dict,
                    "resolved_references": refs,
                    "agent_state": state,
                },
                goal_analysis=analysis,
                source="agent_planner_defer_literal_composite",
            )

        working_context: dict[str, Any] = {
            "parsed_goal": parsed_dict,
            "resolved_references": refs,
            "agent_state": state,
            "goal_analysis": {
                "user_intent": analysis.user_intent,
                "desired_state": analysis.desired_state,
                "current_state_summary": analysis.current_state_summary,
                "missing_information": analysis.missing_information,
                "is_compound": analysis.is_compound,
                "confidence": analysis.confidence,
                "task_category": analysis.task_category.value,
                "required_capabilities": analysis.required_capabilities,
                "verification_criteria": analysis.verification_criteria,
            },
        }

        from hermes.agent.general_goal import classify_generic_goal

        working_context["generic_goal"] = classify_generic_goal(
            text, ctx, parsed_goal_dict=parsed_dict, agent_state=state
        ).to_dict()

        if risk.requires_confirmation:
            return AgentPlanningDecision(
                route=PlanningRoute.CLARIFY,
                clarification=f"{risk.reason} Devam etmemi istiyor musun?",
                working_context=working_context,
                goal_analysis=analysis,
                requires_confirmation=True,
                confirmation_reason=risk.reason,
                source="agent_planner_risk",
            )

        if self._should_skip_clarification(text, ctx, refs, parsed_dict):
            parsed_dict["ambiguity"] = ""

        if parsed.ambiguity == "referans_netlestirme" and not refs and not self._has_verified_target(ctx):
            if analysis.confidence >= 0.8 and analysis.is_compound:
                pass
            else:
                return AgentPlanningDecision(
                    route=PlanningRoute.CLARIFY,
                    clarification="Hangi dosya veya klasoru kastettigini netlestirir misin?",
                    working_context=working_context,
                    goal_analysis=analysis,
                    source="agent_planner_clarify",
                )

        if should_route_to_mission(text):
            progress = self._progress_hint(text, analysis)
            return AgentPlanningDecision(
                route=PlanningRoute.MISSION,
                working_context=working_context,
                goal_analysis=analysis,
                progress_hint=progress,
                source="agent_planner_mission",
            )

        from hermes.agent.plan_analysis import is_create_and_audit_goal, is_organize_files_goal
        from hermes.agent.general_goal import is_general_mission_goal

        if is_organize_files_goal(text) or is_create_and_audit_goal(text) or is_general_mission_goal(text):
            return AgentPlanningDecision(
                route=PlanningRoute.MISSION,
                working_context=working_context,
                goal_analysis=analysis,
                progress_hint=self._progress_hint(text, analysis),
                source="agent_planner_dynamic_goal",
            )

        if should_create_mission(text) and analysis.is_compound:
            from hermes.mission.write_content import is_literal_composite_file_mission

            if not is_literal_composite_file_mission(text):
                return AgentPlanningDecision(
                    route=PlanningRoute.MISSION,
                    working_context=working_context,
                    goal_analysis=analysis,
                    progress_hint="Gorevi planliyorum.",
                    source="agent_planner_compound",
                )

        if has_actionable_sequence(text):
            from hermes.mission.write_content import is_literal_composite_file_mission

            if not is_literal_composite_file_mission(text):
                sequence = plan_local_sequence(text)
                if len(sequence) >= 2:
                    return AgentPlanningDecision(
                        route=PlanningRoute.LOCAL_SEQUENCE,
                        local_sequence=sequence,
                        working_context=working_context,
                        goal_analysis=analysis,
                        source="agent_planner_sequence",
                    )

        from hermes.agent.server_tasks import should_defer_to_server

        if should_defer_to_server(text):
            return AgentPlanningDecision(
                route=PlanningRoute.SERVER,
                working_context=working_context,
                goal_analysis=analysis,
                source="agent_planner_server",
            )

        return AgentPlanningDecision(
            route=PlanningRoute.SERVER,
            working_context=working_context,
            goal_analysis=analysis,
            source="agent_planner_default",
        )

    @staticmethod
    def _has_verified_target(ctx: ConversationalContext) -> bool:
        return bool(
            ctx.last_verified_file
            or ctx.last_verified_folder
            or ctx.last_created_file
            or ctx.active_file
        )

    @staticmethod
    def _should_skip_clarification(
        text: str,
        ctx: ConversationalContext,
        refs: dict[str, str],
        parsed_dict: dict[str, Any],
    ) -> bool:
        if refs.get("target_file") or refs.get("target_folder"):
            return True
        if ctx.last_verified_file or ctx.last_created_file:
            lower = text.casefold()
            deictic = any(token in lower for token in ("onu", "bunu", "son olustur", "son oluştur", "son dosya"))
            if deictic:
                return True
        if parsed_dict.get("confidence", 0) >= 0.85 and parsed_dict.get("is_multi_step"):
            return True
        return False

    @staticmethod
    def _progress_hint(message: str, analysis: GoalAnalysis) -> str:
        from hermes.agent.plan_models import TaskCategory

        lower = message.casefold()
        if analysis.task_category == TaskCategory.ORGANIZATION:
            return "Dosyalari inceliyorum."
        if analysis.task_category == TaskCategory.INFORMATION:
            return "Bilgisayari kontrol ediyorum."
        if analysis.task_category == TaskCategory.MULTI_STEP:
            return "Gerekli adimlari planliyorum."
        if "pdf" in lower:
            return "PDF dosyalarini arıyorum."
        if analysis.requires_filesystem_probe:
            return "Dosyalari tarıyorum."
        if "olustur" in lower or "oluştur" in lower:
            return "Gerekli adimlari planliyorum."
        return "Gorevi planliyorum."
