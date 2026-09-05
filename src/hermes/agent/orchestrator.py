from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable  # noqa: F401
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from hermes.agent.response_builder import ResponseBuilder
from hermes.client.local_context import LocalClientContext
from hermes.config.settings import AppSettings, RiskLevel
from hermes.security import (
    ApprovalManager,
    AuditLogger,
    PolicyEngine,
    map_decision_to_hermes_choice,
)
from hermes.server.client import HermesServerClient, HermesServerError
from hermes.server.models import (
    ApprovalRequest,
    HermesApprovalSubmit,
    Run,
    RunEvent,
    RunEventType,
    SessionCreate,
    ToolCallRequest,  # noqa: F401
)
from hermes.tools.executor import ToolApprovalRequiredError, ToolExecutor
from hermes.tools.manifest import LocalToolRequest, format_tool_result_message, parse_local_tool_request
from hermes.tools.registry import ToolRegistry, create_default_registry
from hermes.utils.logging import get_logger

from hermes.mission.store import MissionStore

logger = get_logger(__name__)

_MAX_LOCAL_TOOL_ITERATIONS = 25
_LLM_UNAVAILABLE_NOTICE = (
    "Anlama katmanina su an ulasilamadi; bilinen yerel yollarla devam ediyorum."
)


def _describe_capability_gap(plan: Any) -> str:
    """Say what is missing and what could be done instead.

    Understanding a request but lacking the means to carry it out is a
    different answer from not understanding it, and the user is owed the
    difference.
    """
    missing = ", ".join(plan.unavailable_capabilities) or "gereken yetenek"
    lines = [f"Bunu su an yapamiyorum: {missing} bu bilgisayarda mevcut degil."]

    offered = sorted(
        {
            name
            for names in plan.alternatives.values()
            for name in names
        }
    )
    if offered:
        lines.append("Bunun yerine su yeteneklerle yardimci olabilirim: " + ", ".join(offered) + ".")
    return "\n".join(lines)


class AgentPhase(StrEnum):
    IDLE = "idle"
    UNDERSTANDING = "understanding"
    OBSERVING = "observing"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_FOR_USER = "waiting_for_user"


SESSION_RENEWED_NOTICE = (
    "Yeni oturum baslatildi. Sunucu onceki konusmayi hatirlamiyor "
    "(restart veya oturum suresi doldu)."
)


@dataclass
class AgentState:
    phase: AgentPhase = AgentPhase.IDLE
    status_message: str = ""
    current_run_id: str | None = None
    current_session_id: str | None = None
    step_count: int = 0
    plan_steps: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_tool_results: list[dict[str, Any]] = field(default_factory=list)
    session_renewed: bool = False
    session_notice: str | None = None


StatusCallback = Callable[[AgentPhase, str, dict[str, Any]], Awaitable[None] | None]


class AgentOrchestrator:
    """Client-side agent loop using Hermes Agent Runs API."""

    def __init__(
        self,
        settings: AppSettings,
        server: HermesServerClient,
        registry: ToolRegistry | None = None,
        on_status: StatusCallback | None = None,
        on_approval_required: Callable[[ApprovalRequest], Awaitable[str]] | None = None,
        mission_store: MissionStore | None = None,
    ) -> None:
        from hermes.mission.store import MissionStore as _MissionStore

        self._settings = settings
        self._server = server
        self._registry = registry or create_default_registry()
        self._local_context = LocalClientContext(self._registry, client_name=settings.client.name)
        self._on_status = on_status
        self._on_approval_required = on_approval_required
        self._policy = PolicyEngine(
            settings.security.require_approval_for, registry=self._registry
        )
        self._audit = AuditLogger(settings.security.audit_log_path, settings.security.redact_patterns)
        self._approval = ApprovalManager()
        self._executor = ToolExecutor(self._registry, self._policy, self._audit, self._approval)
        self._mission_store = mission_store or _MissionStore()
        self._intent_understanding: Any = None
        self._intent_router: Any = None
        self._skill_executor: Any = None
        self.state = AgentState()

    async def _set_status(self, phase: AgentPhase, message: str, extra: Any | None = None) -> None:
        if extra is None:
            extra = {}
        self.state.phase = phase
        self.state.status_message = message
        self.state.metadata.update(extra)
        logger.debug("agent_status", **{"phase": phase.value, "message": message, **extra})
        if self._on_status:
            result = self._on_status(phase, message, extra)
            if result is not None:
                await result

    def _complete_mission(self, mission_id: str | None, result: str, *, success: bool = True) -> None:
        if not mission_id:
            return
        from hermes.mission.models import MissionStatus

        loaded = self._mission_store.load(mission_id)
        if loaded and loaded.status in (
            MissionStatus.WAITING_FOR_USER,
            MissionStatus.PAUSED,
            MissionStatus.CANCELLED,
        ):
            return
        status = MissionStatus.COMPLETED if success else MissionStatus.FAILED
        self._mission_store.update_status(mission_id, status, summary=(result or "")[:500])

    async def _continue_mission(
        self,
        mission_id: str,
        message: str,
        conv_ctx: Any,
        *,
        preamble: str = "",
    ) -> str:
        from hermes.client.session_store import append_conversation_turn
        from hermes.mission.audit import MissionAuditor
        from hermes.mission.models import MissionStatus

        mission = self._mission_store.load(mission_id)
        prior_status = mission.status.value if mission else ""
        mission = self._mission_store.resume(mission_id)
        if mission is None:
            text = "Devam edecek gorev bulamadim."
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            conv_ctx.save()
            return text

        from hermes.agent.mission_flow import restore_context_snapshot

        snapshot = mission.context_snapshot or mission.working_context.get("context_snapshot") or {}
        restore_context_snapshot(conv_ctx, snapshot if isinstance(snapshot, dict) else {})
        conv_ctx.active_mission_id = mission_id
        conv_ctx.active_step_id = mission.current_step_id
        conv_ctx.save()
        self._mission_store.snapshot_context(mission_id, conv_ctx.snapshot_for_mission())
        MissionAuditor(mission_id).mission_resumed(from_status=prior_status)

        engine_result = await self._run_mission_engine(mission_id)
        text = await self._apply_mission_engine_result(
            mission_id,
            engine_result,
            conv_ctx,
            message,
            preamble=preamble,
        )
        return text

    async def _apply_mission_engine_result(
        self,
        mission_id: str,
        engine_result: Any,
        conv_ctx: Any,
        message: str,
        *,
        preamble: str = "",
    ) -> str:
        from hermes.agent.status_messages import format_completed_status
        from hermes.client.session_store import append_conversation_turn
        from hermes.mission.models import MissionStatus

        loaded = self._mission_store.load(mission_id)
        if loaded is not None:
            conv_ctx.sync_from_mission(loaded)
            self._mission_store.snapshot_context(mission_id, conv_ctx.snapshot_for_mission())

        if engine_result.waiting_for_user:
            conv_ctx.active_mission_id = mission_id
            conv_ctx.active_step_id = loaded.current_step_id if loaded else None
            conv_ctx.record_mission_summary(engine_result.summary)
            conv_ctx.save()
            text = engine_result.summary
            if preamble and preamble not in text:
                text = f"{preamble}\n\n{text}"
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            self.state.metadata["turn_outcome"] = "waiting"
            await self._set_status(
                AgentPhase.AWAITING_APPROVAL,
                "Devam etmek icin yanitini bekliyorum.",
                {"mission_id": mission_id, "mission_status": MissionStatus.WAITING_FOR_USER.value},
            )
            return text

        if loaded and loaded.status == MissionStatus.PAUSED:
            conv_ctx.active_mission_id = None
            if mission_id not in conv_ctx.suspended_mission_ids:
                conv_ctx.suspended_mission_ids = [mission_id, *conv_ctx.suspended_mission_ids[:9]]
            conv_ctx.save()
            text = preamble or "Gorevi beklemeye aldim."
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.PLANNING, "Gorev bekletiliyor.")
            return text

        terminal = loaded and loaded.status in (
            MissionStatus.COMPLETED,
            MissionStatus.FAILED,
            MissionStatus.CANCELLED,
        )
        if terminal or engine_result.handled:
            conv_ctx.active_mission_id = None
            conv_ctx.active_step_id = None
            conv_ctx.suspended_mission_ids = [
                item for item in conv_ctx.suspended_mission_ids if item != mission_id
            ]
            conv_ctx.record_mission_outcome(mission_id, success=engine_result.success)
            conv_ctx.record_mission_summary(engine_result.summary)
            if engine_result.success:
                conv_ctx.record_action_summary(engine_result.summary)
                from hermes.agent.mission_progress import build_natural_mission_summary

                if loaded:
                    natural = build_natural_mission_summary(loaded)
                    if natural:
                        conv_ctx.record_verified_action(natural)
            conv_ctx.save()
            text = engine_result.summary
            if preamble and preamble not in text:
                text = f"{preamble}\n\n{text}"
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            phase = AgentPhase.COMPLETED if engine_result.success else AgentPhase.FAILED
            self.state.metadata["turn_outcome"] = (
                "completed" if engine_result.success else "failed"
            )
            await self._set_status(
                phase,
                format_completed_status() if engine_result.success else "Gorev tamamlanamadi.",
                {"mission_id": mission_id, "mission_status": phase.value},
            )
            if terminal or engine_result.success or not engine_result.waiting_for_user:
                self._complete_mission(mission_id, text, success=engine_result.success)
            return text

        text = engine_result.summary or preamble or "Gorev devam ediyor."
        append_conversation_turn("user", message)
        append_conversation_turn("assistant", text)
        conv_ctx.save()
        return text

    def _suspend_active_mission_if_needed(
        self,
        message: str,
        conv_ctx: Any,
        *,
        reason: str = "",
    ) -> None:
        from hermes.agent.mission_flow import is_independent_interrupt
        from hermes.mission.audit import MissionAuditor
        from hermes.mission.models import MissionStatus

        if not is_independent_interrupt(message):
            return
        active = self._mission_store.load_active()
        if active is None:
            return
        if active.status not in (
            MissionStatus.RUNNING,
            MissionStatus.PLANNING,
            MissionStatus.RECOVERING,
            MissionStatus.WAITING_FOR_USER,
        ):
            return
        self._mission_store.snapshot_context(active.mission_id, conv_ctx.snapshot_for_mission())
        self._mission_store.suspend_mission(active.mission_id, reason=reason or message[:200])
        MissionAuditor(active.mission_id).mission_suspended(reason=reason or message[:200])
        MissionAuditor(active.mission_id).user_interrupted(new_goal=message)
        if conv_ctx.active_mission_id == active.mission_id:
            conv_ctx.active_mission_id = None
            conv_ctx.active_step_id = None
        if active.mission_id not in conv_ctx.suspended_mission_ids:
            conv_ctx.suspended_mission_ids = [active.mission_id, *conv_ctx.suspended_mission_ids[:9]]
        conv_ctx.save()

    def _release_waiting_mission(self, mission_id: str, conv_ctx: Any, message: str) -> None:
        """A new goal must not keep answering an old WAITING clarification."""
        from hermes.mission.audit import MissionAuditor

        self._mission_store.snapshot_context(mission_id, conv_ctx.snapshot_for_mission())
        self._mission_store.suspend_mission(mission_id, reason="Yeni gorev; bekleyen netlestirme birakildi")
        MissionAuditor(mission_id).mission_suspended(reason="new_task_replaces_waiting")
        MissionAuditor(mission_id).user_interrupted(new_goal=message)
        if conv_ctx.active_mission_id == mission_id:
            conv_ctx.active_mission_id = None
            conv_ctx.active_step_id = None
        if mission_id not in conv_ctx.suspended_mission_ids:
            conv_ctx.suspended_mission_ids = [mission_id, *conv_ctx.suspended_mission_ids[:9]]
        conv_ctx.save()

    async def _run_mission_engine(self, mission_id: str) -> Any:
        from hermes.mission.engine import EngineResult, MissionEngine
        from hermes.mission.planner import MissionPlanner
        from hermes.agent.status_messages import format_mission_planning_status

        self._intent_layer()
        planner = MissionPlanner(self._server, self._registry)
        engine = MissionEngine(
            self._mission_store,
            self._registry,
            self._executor,
            execute_local_tool=self._execute_mission_local_tool,
            skill_executor=self._skill_executor,
        )
        await self._set_status(
            AgentPhase.PLANNING,
            format_mission_planning_status(),
            {"mission_id": mission_id},
        )
        return await engine.run(mission_id, planner)

    def _intent_layer(self) -> tuple[Any, Any]:
        from hermes.intent.router import IntentRouter
        from hermes.intent.understanding import IntentUnderstanding
        from hermes.skills.executor import SkillExecutor

        if self._intent_understanding is None:
            self._skill_executor = SkillExecutor(self._registry, self._executor)
            self._intent_understanding = IntentUnderstanding(self._server, self._registry)
            self._intent_router = IntentRouter(self._registry, self._skill_executor)
        return self._intent_understanding, self._intent_router

    @staticmethod
    def _context_can_ground(conv_ctx: Any) -> bool:
        """Whether the session already holds a target the next sentence can use."""
        return bool(
            getattr(conv_ctx, "active_file", None)
            or getattr(conv_ctx, "last_created_file", None)
            or getattr(conv_ctx, "last_opened_file", None)
            or getattr(conv_ctx, "last_url", None)
            or getattr(conv_ctx, "last_browser_url", None)
            or getattr(conv_ctx, "last_application", None)
            or getattr(conv_ctx, "active_folder", None)
        )

    def _local_intent_is_complete(self, intent: Any) -> bool:
        """Fast paths may only run when every required argument is already filled.

        A match like "su siteye gir" with no URL is a GoalRouter hit, not a
        finished action — understanding plus session context has to take it.
        """
        request = getattr(intent, "request", None)
        if request is None or not getattr(request, "name", None):
            return False
        tool = self._registry.get(request.name)
        arguments = dict(getattr(request, "arguments", None) or {})
        if tool is None:
            # Some resolver intents (create_file) are handled by the executor
            # without a registry entry. Empty arguments still mean "incomplete".
            return any(value not in (None, "") for value in arguments.values())
        required = (tool.get_parameters_schema() or {}).get("required") or []
        return all(arguments.get(name) not in (None, "") for name in required)

    async def _try_complete_local_fast_path(
        self, message: str, conv_ctx: Any, resolved_refs: dict[str, str]
    ) -> str | None:
        """Run a fully bound single local command without consulting the model."""
        from hermes.agent.local_intent import (
            guess_file_action,
            guess_install_action,
            guess_local_action,
            summarize_local_result,
        )
        from hermes.agent.status_messages import format_completed_status
        from hermes.client.session_store import append_conversation_turn
        from hermes.mission.write_content import is_placeholder_write_content

        file_intent = guess_file_action(
            message, resolved_references=resolved_refs, conv_ctx=conv_ctx
        )
        if file_intent and self._local_intent_is_complete(file_intent):
            placeholder = file_intent.request.name == "write_file" and (
                is_placeholder_write_content(
                    str(file_intent.request.arguments.get("content") or "")
                )
            )
            if not placeholder:
                text = await self._execute_resolved_local_intent(
                    file_intent, message, conv_ctx, resolved_references=resolved_refs
                )
                if self.state.session_notice:
                    text = f"{self.state.session_notice}\n\n{text}"
                append_conversation_turn("user", message)
                append_conversation_turn("assistant", text)
                await self._set_status(AgentPhase.COMPLETED, "Dosya islemi tamamlandi.")
                return text

        install_intent = guess_install_action(message)
        if install_intent and self._local_intent_is_complete(install_intent):
            result = await self._execute_local_tool(
                install_intent.request,
                run_id=f"local-{uuid4().hex[:12]}",
                user_message=message,
            )
            self._remember_tool_result(install_intent.request.name, result, message)
            self._update_conversational_context(conv_ctx, install_intent.request.name, result)
            conv_ctx.save()
            text = summarize_local_result(install_intent, result)
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.COMPLETED, "Kurulum tamamlandi.")
            return text

        local = guess_local_action(
            message, resolved_references=resolved_refs, conv_ctx=conv_ctx
        )
        if local and self._local_intent_is_complete(local):
            tool_name = local.request.name
            if tool_name in (
                "write_file",
                "create_file",
                "open_path",
                "create_folder",
                "delete_path",
                "list_directory",
            ):
                text = await self._execute_resolved_local_intent(
                    local, message, conv_ctx, resolved_references=resolved_refs
                )
            else:
                result = await self._execute_local_tool(
                    local.request,
                    run_id=f"local-{uuid4().hex[:12]}",
                    user_message=message,
                )
                self._remember_tool_result(local.request.name, result, message)
                self._update_conversational_context(conv_ctx, local.request.name, result)
                conv_ctx.save()
                text = summarize_local_result(local, result)
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.COMPLETED, format_completed_status())
            return text
        return None

    async def _handle_with_intent(self, message: str, conv_ctx: Any) -> str | None:
        """Understand the message and carry it out through the usual chain.

        Returns None only when the model cannot be reached, so the
        deterministic handlers remain a fallback rather than a second router.
        A parsed intent is always routed, even if validation found a gap.
        """
        from hermes.client.session_store import load_conversation_history
        from hermes.intent.router import RouteKind

        understanding, router = self._intent_layer()
        history = [
            f"{turn['role']}: {turn['content']}"
            for turn in load_conversation_history()[-6:]
        ]

        from hermes.context.entity_decision import Confidence
        from hermes.screen.plan import build_catalog_search_intent, build_screen_perception_intent

        screen_intent = build_screen_perception_intent(message, conv_ctx)
        if screen_intent is None:
            screen_intent = build_catalog_search_intent(message, conv_ctx)
        if screen_intent is not None:
            plan = router.route(screen_intent, confidence=Confidence.HIGH, context=conv_ctx)
            self._store_routing_trace(message, screen_intent, plan)
            conv_ctx.record_intent(screen_intent.to_dict(), route_kind=str(plan.kind))
            if plan.is_executable:
                return await self._dispatch_routed_plan(
                    message, screen_intent, plan, conv_ctx
                )

        await self._set_status(AgentPhase.PLANNING, "Ne istedigini anlamaya calisiyorum...")
        result = await understanding.understand(
            message, context=conv_ctx, history=history or None
        )
        if result.unavailable:
            self.state.metadata["intent_unavailable"] = True
            logger.info(
                "intent_layer_unavailable",
                error=(result.error or "")[:200],
            )
            return None

        if result.intent is None:
            logger.info(
                "intent_layer_unparseable",
                error=(result.error or "")[:200],
            )
            return await self._finish_intent_turn(
                message,
                "Istedigini yapilandirilmis olarak anlayamadim. "
                "Ne yapmami istedigini biraz daha acik yazar misin?",
                conv_ctx,
                AgentPhase.WAITING_FOR_USER,
            )

        plan = router.route(result.intent, confidence=result.confidence, context=conv_ctx)
        self._store_routing_trace(message, result.intent, plan)
        conv_ctx.record_intent(
            result.intent.to_dict(),
            route_kind=str(plan.kind),
        )
        logger.info(
            "intent_layer_routed",
            route=str(plan.kind),
            confidence=str(result.confidence),
            capabilities=list(result.intent.required_capabilities),
            tools=[step.tool_name for step in plan.steps],
            tool_source="capability_resolver",
        )
        return await self._dispatch_routed_plan(message, result.intent, plan, conv_ctx)

    async def _dispatch_routed_plan(
        self, message: str, intent: Any, plan: Any, conv_ctx: Any
    ) -> str | None:
        from hermes.intent.router import RouteKind

        if plan.kind is RouteKind.CONVERSATION:
            reply = plan.question or intent.reply or "Nasil yardimci olabilirim?"
            return await self._finish_intent_turn(
                message, reply, conv_ctx, AgentPhase.COMPLETED, intent=intent, plan=plan
            )

        if plan.kind is RouteKind.QUESTION:
            return await self._finish_intent_turn(
                message,
                plan.question,
                conv_ctx,
                AgentPhase.WAITING_FOR_USER,
                intent=intent,
                plan=plan,
            )

        if plan.kind is RouteKind.UNSUPPORTED:
            return await self._finish_intent_turn(
                message,
                _describe_capability_gap(plan),
                conv_ctx,
                AgentPhase.FAILED,
                intent=intent,
                plan=plan,
            )

        if plan.kind is RouteKind.SKILL:
            outcome = await self._skill_executor.execute(
                plan.skill_id, inputs=plan.skill_inputs, context=conv_ctx
            )
            phase = AgentPhase.COMPLETED if outcome.success else AgentPhase.FAILED
            return await self._finish_intent_turn(
                message,
                outcome.summary,
                conv_ctx,
                phase,
                intent=intent,
                plan=plan,
            )

        return await self._run_intent_mission(message, plan, intent, conv_ctx)

    async def _run_intent_mission(
        self, message: str, plan: Any, intent: Any, conv_ctx: Any
    ) -> str | None:
        """Hand routed steps to the mission engine as a pre-validated plan."""
        from hermes.intent.models import canonical_required_capabilities

        working_context = {
            "agent_intent": intent.to_dict(),
            "resolved_references": conv_ctx.resolved_references(message),
            "required_capabilities": list(canonical_required_capabilities(intent)),
        }
        mission = self._mission_store.create_mission(
            message, working_context=working_context
        )
        mission.steps = list(plan.steps)
        mission.plan_validated = True
        self._mission_store.save(mission)

        conv_ctx.active_mission_id = mission.mission_id
        conv_ctx.set_current_objective(intent.goal or message)
        self._mission_store.snapshot_context(
            mission.mission_id, conv_ctx.snapshot_for_mission()
        )
        conv_ctx.save()
        self.state.metadata["mission_id"] = mission.mission_id

        engine_result = await self._run_mission_engine(mission.mission_id)
        if not engine_result.handled:
            return await self._finish_intent_turn(
                message,
                engine_result.summary
                or "Gorevi mevcut araclarla tamamlayamadim.",
                conv_ctx,
                AgentPhase.FAILED,
                intent=intent,
                plan=plan,
            )
        text = await self._apply_mission_engine_result(
            mission.mission_id, engine_result, conv_ctx, message
        )
        conv_ctx.record_intent(
            intent.to_dict(),
            route_kind=str(getattr(plan, "kind", "")),
            result=text,
        )
        conv_ctx.save()
        if self.state.session_notice and self.state.session_notice not in text:
            text = f"{self.state.session_notice}\n\n{text}"
        return text

    def _store_routing_trace(self, message: str, intent: Any, plan: Any) -> None:
        self.state.metadata["faz_f_trace"] = {
            "USER_INPUT": message,
            "INTENT": intent.goal if intent is not None else "",
            "REQUIRED_CAPABILITIES": list(getattr(intent, "required_capabilities", ()) or ()),
            "SELECTED_SKILL_OR_MISSION": getattr(plan, "skill_id", "")
            or ("mission" if getattr(plan, "steps", None) else str(getattr(plan, "kind", ""))),
            "SELECTED_LOCAL_TOOLS": [
                step.tool_name for step in (getattr(plan, "steps", None) or []) if step.tool_name
            ],
            "TOOL_SOURCE": "capability_resolver",
            "ROUTE": str(getattr(plan, "kind", "")),
            "POLICY_RISK": [
                step.risk_level for step in (getattr(plan, "steps", None) or []) if step.risk_level
            ],
            "APPROVAL": bool(getattr(plan, "needs_approval", False)),
        }

    def _legacy_reply(self, text: str) -> str:
        if not self.state.metadata.get("intent_unavailable"):
            return text
        notice = _LLM_UNAVAILABLE_NOTICE
        if not text:
            return notice
        if notice in text:
            return text
        return f"{notice}\n\n{text}"

    async def _finish_intent_turn(
        self,
        message: str,
        text: str,
        conv_ctx: Any,
        phase: AgentPhase,
        *,
        intent: Any = None,
        plan: Any = None,
    ) -> str:
        from hermes.client.session_store import append_conversation_turn

        if intent is not None:
            conv_ctx.record_intent(
                intent.to_dict(),
                route_kind=str(getattr(plan, "kind", "")),
                result=text,
            )
        append_conversation_turn("user", message)
        append_conversation_turn("assistant", text)
        conv_ctx.save()
        if plan is not None and getattr(plan, "kind", None) is not None:
            kind = str(getattr(plan, "kind", "") or "")
            if kind == "question":
                self.state.metadata["turn_outcome"] = "question"
            elif kind == "unsupported":
                self.state.metadata["turn_outcome"] = "unsupported"
            else:
                self.state.metadata["turn_outcome"] = {
                    AgentPhase.COMPLETED: "completed",
                    AgentPhase.FAILED: "failed",
                    AgentPhase.WAITING_FOR_USER: "waiting",
                }.get(phase, "failed")
        else:
            self.state.metadata["turn_outcome"] = {
                AgentPhase.COMPLETED: "completed",
                AgentPhase.FAILED: "failed",
                AgentPhase.WAITING_FOR_USER: "waiting",
            }.get(phase, "failed")
        await self._set_status(phase, text[:200])
        return text

    async def initialize_session(self) -> str:
        from hermes.client.session_store import (
            clear_session_id,
            get_client_id,
            load_session_id,
            new_conversation_id,
            save_session_id,
        )
        from hermes.server.client import HermesServerError

        get_client_id()
        self.state.session_renewed = False
        self.state.session_notice = None

        if not self._settings.sessions.record_sessions:
            self.state.current_session_id = None
            return ""

        stored = load_session_id()
        session_id = stored or new_conversation_id()
        if not stored:
            logger.info("conversation_id_issued", session_id=session_id)
        save_session_id(session_id)
        self.state.current_session_id = session_id
        setter = getattr(self._server, "set_active_session", None)
        if callable(setter):
            setter(session_id)

        try:
            metadata = self._local_context.build_session_metadata()
            session = await self._server.create_session(
                SessionCreate(id=session_id, session_id=session_id, metadata=metadata)
            )
            resolved = (session.id or session.session_id or session_id).strip()
            if resolved:
                session_id = resolved
            return await self._activate_session(session_id, reused=bool(stored))
        except HermesServerError as exc:
            if exc.status_code == 400 or "invalid_session" in str(exc).lower():
                previous = session_id
                clear_session_id()
                session_id = new_conversation_id()
                self.state.session_renewed = True
                self.state.session_notice = SESSION_RENEWED_NOTICE
                logger.info("session_renewed", previous_session_id=previous, reason="invalid_id")
                return await self._activate_session(session_id, reused=False)
            self.state.session_renewed = True
            self.state.session_notice = SESSION_RENEWED_NOTICE
            reason = "auth" if exc.is_auth_error() else "register_failed"
            logger.info(
                "session_renewed",
                previous_session_id=session_id,
                reason=reason,
                error=str(exc),
            )
            return await self._activate_session(session_id, reused=False)

    async def _activate_session(
        self,
        session_id: str,
        *,
        reused: bool,
        offline: bool = False,
    ) -> str:
        from hermes.client.session_store import save_session_id

        save_session_id(session_id)
        self.state.current_session_id = session_id
        setter = getattr(self._server, "set_active_session", None)
        if callable(setter):
            setter(session_id)

        from hermes.client.session_store import load_manifest_tool_count, save_manifest_tool_count

        current_tools = self._local_context.tool_count
        previous_tools = load_manifest_tool_count()
        if previous_tools is not None and previous_tools != current_tools:
            logger.info(
                "manifest_tool_count_changed",
                previous=previous_tools,
                current=current_tools,
            )
            self.state.metadata["manifest_refreshed"] = True
        save_manifest_tool_count(current_tools)

        self.state.metadata["local_tools_registered"] = True
        self.state.metadata["local_tool_count"] = self._local_context.tool_count
        logger.info(
            "local_tools_session_reused" if reused else "local_tools_registered",
            session_id=session_id,
            tool_count=self._local_context.tool_count,
            renewed=self.state.session_renewed,
            offline=offline,
        )
        if not offline:
            try:
                await self.register_local_capabilities(session_id)
            except HermesServerError:
                logger.debug("session_metadata_embedded_in_run", session_id=session_id)
        return session_id

    async def register_local_capabilities(self, session_id: str | None) -> None:
        """Ensure session metadata includes the current local tools manifest."""
        if not session_id:
            return
        metadata = self._local_context.build_session_metadata()
        updated = await self._server.update_session(session_id, metadata=metadata)
        if updated is None:
            logger.debug("session_metadata_embedded_in_run", session_id=session_id)
        self.state.metadata["local_tools_registered"] = True
        self.state.metadata["local_tool_count"] = self._local_context.tool_count

    async def process_message(self, message: str, session_id: str | None = None) -> str:
        from hermes.client.session_store import append_conversation_turn
        from hermes.context.conversational_context import ConversationalContext
        from hermes.context.reference_resolver import ReferenceResolver, ResolutionResult

        self.state.step_count = 0
        self.state.metadata["local_tool_executed_this_turn"] = False
        self.state.metadata.pop("routing_trace", None)
        self.state.metadata.pop("intent_unavailable", None)
        self.state.metadata.pop("faz_f_trace", None)
        self.state.metadata.pop("turn_outcome", None)

        conv_ctx = ConversationalContext.load()
        conv_ctx.reconcile_with_filesystem()
        conv_ctx.record_user_message(message)

        from hermes.agent.conversation_flow import handle_meta_conversation

        meta = handle_meta_conversation(message, conv_ctx, self._mission_store)
        if meta.handled:
            if meta.repeat_message:
                conv_ctx.save()
                return await self.process_message(meta.repeat_message, session_id=session_id)
            text = meta.response
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            conv_ctx.save()
            await self._set_status(AgentPhase.COMPLETED, "Tamam.")
            return text

        from hermes.context.context_correction import resolve_user_correction
        from hermes.mission.models import MissionStatus

        correction = resolve_user_correction(message, conv_ctx)
        if correction.handled:
            if correction.repeat_message:
                active_waiting = self._mission_store.load_active()
                if (
                    active_waiting is not None
                    and active_waiting.status == MissionStatus.WAITING_FOR_USER
                ):
                    self._release_waiting_mission(
                        active_waiting.mission_id, conv_ctx, message
                    )
                conv_ctx.save()
                return await self.process_message(correction.repeat_message, session_id=session_id)
            text = correction.response
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            conv_ctx.save()
            await self._set_status(AgentPhase.COMPLETED, "Tamam.")
            return text

        conv_ctx.set_current_objective(message)

        from hermes.agent.mission_flow import (
            handle_mission_commands,
            is_mission_resume_message,
        )
        from hermes.mission.models import MissionStatus

        mission_cmd = handle_mission_commands(message, conv_ctx, self._mission_store)
        if mission_cmd.handled:
            if mission_cmd.cancelled_mission_id:
                text = mission_cmd.response
                if self.state.session_notice:
                    text = f"{self.state.session_notice}\n\n{text}"
                append_conversation_turn("user", message)
                append_conversation_turn("assistant", text)
                await self._set_status(AgentPhase.CANCELLED, "Gorev iptal edildi.")
                return text
            if mission_cmd.resume_mission_id:
                text = await self._continue_mission(
                    mission_cmd.resume_mission_id,
                    message,
                    conv_ctx,
                    preamble=mission_cmd.response,
                )
                if self.state.session_notice and self.state.session_notice not in text:
                    text = f"{self.state.session_notice}\n\n{text}"
                return text

        from hermes.agent.mission_continuation import (
            PendingReplyKind,
            classify_pending_user_message,
            detect_mission_continuation,
        )

        continuation = detect_mission_continuation(message, conv_ctx, self._mission_store)
        if continuation.continue_mission_id and continuation.is_continuation:
            if continuation.reason == "waiting_for_user":
                self._mission_store.resume_from_user(continuation.continue_mission_id, message)
            else:
                mission = self._mission_store.load(continuation.continue_mission_id)
                if mission is not None:
                    mission.user_interventions.append(
                        {
                            "type": "continuation",
                            "message": message[:2000],
                            "reason": continuation.reason,
                        }
                    )
                    mission.working_context["continuation_message"] = message[:2000]
                    self._mission_store.save(mission)
                    self._mission_store.resume(continuation.continue_mission_id)
            text = await self._continue_mission(
                continuation.continue_mission_id,
                message,
                conv_ctx,
                preamble="Tamam, goreve devam ediyorum.",
            )
            if self.state.session_notice and self.state.session_notice not in text:
                text = f"{self.state.session_notice}\n\n{text}"
            return text

        active_mission = self._mission_store.load_active()
        if active_mission and active_mission.status == MissionStatus.WAITING_FOR_USER:
            pending_kind = classify_pending_user_message(message, active_mission)
            if pending_kind is PendingReplyKind.CONTINUE and not is_mission_resume_message(
                message
            ):
                self._mission_store.resume_from_user(active_mission.mission_id, message)
                text = await self._continue_mission(active_mission.mission_id, message, conv_ctx)
                if self.state.session_notice and self.state.session_notice not in text:
                    text = f"{self.state.session_notice}\n\n{text}"
                return text
            if pending_kind is PendingReplyKind.NEW_TASK:
                self._release_waiting_mission(active_mission.mission_id, conv_ctx, message)

        self._suspend_active_mission_if_needed(message, conv_ctx, reason="Kullanici yeni gorev istedi")

        resolution = ReferenceResolver().resolve(message, conv_ctx)
        from hermes.agent.application_catalog import is_web_or_app_open_message
        from hermes.agent.task_planner import is_multi_step_message

        bound_single = not is_multi_step_message(message)
        if is_web_or_app_open_message(message) and bound_single:
            resolution = ResolutionResult(is_new_task=True)

        if (
            resolution.ambiguous
            and bound_single
            and not self._context_can_ground(conv_ctx)
        ):
            text = resolution.clarification
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            conv_ctx.save()
            await self._set_status(AgentPhase.UNDERSTANDING, "Referans netlestirme gerekiyor.")
            return text

        from hermes.agent.status_messages import format_completed_status, format_understanding_status

        if resolution.intent and self._local_intent_is_complete(resolution.intent):
            await self._set_status(AgentPhase.UNDERSTANDING, format_understanding_status())
            text = await self._execute_resolved_local_intent(
                resolution.intent,
                message,
                conv_ctx,
                resolved_references=resolution.resolved_references,
            )
            for follow_up in resolution.follow_up_intents:
                extra = await self._execute_resolved_local_intent(
                    follow_up,
                    message,
                    conv_ctx,
                    resolved_references=resolution.resolved_references,
                )
                if extra:
                    text = f"{text}\n{extra}" if text else extra
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.COMPLETED, format_completed_status())
            return text

        resolved_refs = dict(resolution.resolved_references)

        from hermes.agent.goal_router import GoalRouter

        goal = GoalRouter(self._registry).route(
            message, conv_ctx, resolved_references=resolved_refs
        )
        if bound_single and goal.ambiguous and not self._context_can_ground(conv_ctx):
            text = goal.clarification
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            conv_ctx.save()
            await self._set_status(AgentPhase.UNDERSTANDING, "Netlestirmem gerekiyor.")
            return text
        if bound_single and goal.intent and self._local_intent_is_complete(goal.intent):
            await self._set_status(AgentPhase.UNDERSTANDING, format_understanding_status())
            text = await self._execute_resolved_local_intent(
                goal.intent,
                message,
                conv_ctx,
                resolved_references=goal.resolved_references,
            )
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.COMPLETED, format_completed_status())
            return text

        # Single, fully bound local commands (DNS, volume, open one app) stay
        # fast. Multi-step and incomplete matches fall through to understanding.
        if bound_single:
            fast = await self._try_complete_local_fast_path(
                message, conv_ctx, resolved_refs
            )
            if fast is not None:
                return fast

        # Structured understanding is the primary path for anything that is
        # not a fully bound single local action. Keyword allowlists no longer
        # sit in front of it. If the model is unreachable, we fall through.
        intent_reply = await self._handle_with_intent(message, conv_ctx)
        if intent_reply is not None:
            return intent_reply

        return self._legacy_reply(
            await self._run_legacy_fallback(
                message, session_id, conv_ctx, resolution, resolved_refs
            )
        )

    async def _run_legacy_fallback(
        self,
        message: str,
        session_id: str | None,
        conv_ctx: Any,
        resolution: Any,
        resolved_refs: dict[str, str],
    ) -> str:
        """Deterministic handlers used only when understanding is unreachable.

        Keyword allowlists, AgentPlanner, and free-text create_run live here
        as compatibility, not as the online router.
        """
        from hermes.agent.agent_planner import AgentPlanner
        from hermes.agent.goal_router import GoalRouter
        from hermes.agent.local_intent import (
            guess_file_action,
            guess_install_action,
            guess_local_action,
            summarize_local_result,
        )
        from hermes.agent.plan_models import PlanningRoute
        from hermes.agent.server_tasks import should_defer_to_server
        from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence
        from hermes.client.session_store import append_conversation_turn
        from hermes.context.conversational_context import ConversationalContext

        max_steps = self._settings.client.max_agent_steps
        agent_decision = AgentPlanner(self._registry).evaluate(
            message, conv_ctx, resolved_references=resolved_refs
        )

        if agent_decision.route == PlanningRoute.CLARIFY and agent_decision.clarification:
            if not self._context_can_ground(conv_ctx):
                text = agent_decision.clarification
                if self.state.session_notice:
                    text = f"{self.state.session_notice}\n\n{text}"
                append_conversation_turn("user", message)
                append_conversation_turn("assistant", text)
                conv_ctx.save()
                await self._set_status(AgentPhase.UNDERSTANDING, "Netlestirmem gerekiyor.")
                return text

        if agent_decision.route == PlanningRoute.LOCAL_SEQUENCE and agent_decision.local_sequence:
            from hermes.agent.status_messages import format_completed_status, format_understanding_status

            await self._set_status(AgentPhase.UNDERSTANDING, format_understanding_status())
            text = await self._execute_local_sequence(
                agent_decision.local_sequence,
                message,
                conv_ctx=conv_ctx,
            )
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.COMPLETED, format_completed_status())
            return text

        from hermes.mission.selection import should_route_to_mission

        route_mission = (
            agent_decision.route == PlanningRoute.MISSION
            or should_route_to_mission(message)
        )

        if route_mission and not (
            resolution.is_continuation and resolved_refs and not resolution.is_new_task
        ):
            from hermes.agent.goal_parser import parse_goal
            from hermes.agent.status_messages import format_mission_planning_status

            parsed = parse_goal(message, conv_ctx, resolved_references=resolved_refs)
            working_context = dict(agent_decision.working_context)
            if not working_context.get("parsed_goal"):
                working_context["parsed_goal"] = parsed.to_dict()
            ctx_refs = conv_ctx.resolved_references(message)
            merged_refs = {**ctx_refs, **resolved_refs}
            if merged_refs:
                working_context["resolved_references"] = merged_refs
            if conv_ctx.last_url:
                working_context.setdefault("last_url", conv_ctx.last_url)
                working_context.setdefault(
                    "last_browser_url",
                    conv_ctx.last_browser_url or conv_ctx.last_url,
                )
            mission = self._mission_store.create_mission(message, working_context=working_context)
            mission_id = mission.mission_id
            conv_ctx.active_mission_id = mission_id
            conv_ctx.set_current_objective(message)
            self._mission_store.snapshot_context(mission_id, conv_ctx.snapshot_for_mission())
            conv_ctx.save()
            self.state.metadata["mission_id"] = mission_id
            progress = agent_decision.progress_hint or format_mission_planning_status()
            await self._set_status(
                AgentPhase.PLANNING,
                progress,
                {"mission_id": mission_id, "planner_source": agent_decision.source},
            )
            engine_result = await self._run_mission_engine(mission_id)
            if engine_result.handled:
                text = await self._apply_mission_engine_result(
                    mission_id,
                    engine_result,
                    conv_ctx,
                    message,
                )
                if self.state.session_notice and self.state.session_notice not in text:
                    text = f"{self.state.session_notice}\n\n{text}"
                return text
            await self._set_status(
                AgentPhase.PLANNING,
                "Mission planner fallback — mevcut agent akisi devam ediyor.",
                {"mission_id": mission_id},
            )

        from hermes.agent.local_intent import guess_file_action

        from hermes.mission.write_content import (
            is_composite_file_mission,
            is_placeholder_write_content,
            plan_composite_file_sequence,
        )

        composite_steps = plan_composite_file_sequence(message)
        from hermes.agent.implicit_file_content import requires_implicit_content_generation

        if is_composite_file_mission(message) and len(composite_steps) < 2:
            if requires_implicit_content_generation(message):
                from hermes.agent.goal_parser import parse_goal
                from hermes.agent.status_messages import format_mission_planning_status

                parsed = parse_goal(message, conv_ctx, resolved_references=resolved_refs)
                working_context = {
                    "parsed_goal": parsed.to_dict(),
                    "resolved_references": resolved_refs,
                }
                mission = self._mission_store.create_mission(
                    message, working_context=working_context
                )
                mission_id = mission.mission_id
                conv_ctx.active_mission_id = mission_id
                conv_ctx.set_current_objective(message)
                self._mission_store.snapshot_context(
                    mission_id, conv_ctx.snapshot_for_mission()
                )
                conv_ctx.save()
                await self._set_status(
                    AgentPhase.PLANNING,
                    format_mission_planning_status(),
                    {"mission_id": mission_id},
                )
                engine_result = await self._run_mission_engine(mission_id)
                if engine_result.handled:
                    text = await self._apply_mission_engine_result(
                        mission_id, engine_result, conv_ctx, message
                    )
                    if self.state.session_notice and self.state.session_notice not in text:
                        text = f"{self.state.session_notice}\n\n{text}"
                    return text
            text = "Dosyaya yazilacak icerigi anlayamadim."
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            conv_ctx.save()
            await self._set_status(AgentPhase.FAILED, text)
            return text

        if len(composite_steps) >= 2:
            return await self._execute_local_sequence(composite_steps, message, conv_ctx=conv_ctx)

        file_intent = guess_file_action(message, resolved_references=resolved_refs, conv_ctx=conv_ctx)
        placeholder_write = False
        if file_intent:
            # A write whose content could not be read out of the sentence is
            # not a failure, it is a sentence the understanding layer should
            # look at; declining here lets it through.
            placeholder_write = file_intent.request.name == "write_file" and (
                is_placeholder_write_content(
                    str(file_intent.request.arguments.get("content") or "")
                )
            )
        if file_intent and not placeholder_write:
            text = await self._execute_resolved_local_intent(
                file_intent, message, conv_ctx, resolved_references=resolved_refs
            )
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.COMPLETED, "Dosya islemi tamamlandi.")
            return text

        from hermes.agent.local_intent import guess_install_action

        install_intent = guess_install_action(message)
        if install_intent:
            logger.info(
                "local_install_start",
                package=install_intent.request.arguments.get("package"),
                run_id_prefix="local",
            )
            result = await self._execute_local_tool(
                install_intent.request, run_id=f"local-{uuid4().hex[:12]}", user_message=message
            )
            self._remember_tool_result(install_intent.request.name, result, message)
            self._update_conversational_context(conv_ctx, install_intent.request.name, result)
            conv_ctx.save()
            text = summarize_local_result(install_intent, result)
            logger.info(
                "local_install_done",
                success=getattr(result, "success", False),
                error=(getattr(result, "error", None) or "")[:300],
            )
            if self.state.session_notice:
                text = f"{self.state.session_notice}\n\n{text}"
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", text)
            await self._set_status(AgentPhase.COMPLETED, "Kurulum tamamlandi.")
            return text

        use_server = should_defer_to_server(message)

        if not use_server and has_actionable_sequence(message):
            steps = plan_local_sequence(message)
            if len(steps) >= 2:
                return await self._execute_local_sequence(steps, message, conv_ctx=conv_ctx)

        if not use_server:
            intent = guess_local_action(message, resolved_references=resolved_refs, conv_ctx=conv_ctx)
            if intent:
                tool_name = intent.request.name
                if tool_name in ("write_file", "create_file", "open_path", "create_folder", "delete_path", "list_directory"):
                    text = await self._execute_resolved_local_intent(
                        intent, message, conv_ctx, resolved_references=resolved_refs
                    )
                else:
                    result = await self._execute_local_tool(
                        intent.request, run_id=f"local-{uuid4().hex[:12]}", user_message=message
                    )
                    self._remember_tool_result(intent.request.name, result, message)
                    self._update_conversational_context(conv_ctx, intent.request.name, result)
                    conv_ctx.save()
                    text = summarize_local_result(intent, result)
                if self.state.session_notice:
                    text = f"{self.state.session_notice}\n\n{text}"
                append_conversation_turn("user", message)
                append_conversation_turn("assistant", text)
                from hermes.agent.status_messages import format_completed_status

                await self._set_status(AgentPhase.COMPLETED, format_completed_status())
                return text
        else:
            await self._set_status(
                AgentPhase.PLANNING,
                "Hermes sunucusu adim adim planliyor...",
            )

        mission_id: str | None = None

        sid = (session_id or self.state.current_session_id or "").strip() or None
        if self._settings.sessions.record_sessions and session_id is None:
            try:
                sid = (await self.initialize_session()).strip() or sid
            except HermesServerError:
                from hermes.client.session_store import load_session_id, new_conversation_id

                sid = load_session_id() or new_conversation_id()

        if sid:
            self.state.current_session_id = sid
            try:
                await self.register_local_capabilities(sid)
            except HermesServerError:
                logger.debug("session_metadata_embedded_in_run", session_id=sid)

        from hermes.client.session_store import (
            append_conversation_turn,
            load_conversation_history,
        )

        instructions = self._local_context.build_run_instructions()
        current_input = self._with_tool_memory(message)
        original_message = message
        history = load_conversation_history()
        final_builder = ResponseBuilder()
        if self.state.session_notice:
            final_builder.add_extra(self.state.session_notice)

        from hermes.voice.spoken import looks_like_missing_tools

        retried_session = False
        for local_iteration in range(_MAX_LOCAL_TOOL_ITERATIONS):
            builder = ResponseBuilder()
            self.state.step_count = 0
            await self._set_status(AgentPhase.UNDERSTANDING, "İstek anlaşılıyor...")
            try:
                run = await self._server.create_run(
                    current_input,
                    session_id=sid,
                    instructions=instructions,
                    conversation_history=history or None,
                )
            except HermesServerError as exc:
                if (
                    session_id is None
                    and not retried_session
                    and local_iteration == 0
                    and (exc.is_not_found() or "session_not_found" in str(exc).lower())
                ):
                    from hermes.client.session_store import clear_session_id

                    clear_session_id()
                    retried_session = True
                    sid = (await self.initialize_session()).strip() or None
                    if self.state.session_notice:
                        final_builder.add_extra(self.state.session_notice)
                    continue
                if exc.is_auth_error():
                    auth_msg = (
                        "Sunucu API anahtarini reddetti. "
                        "Settings'ten ayni API_SERVER_KEY degerini kaydedin."
                    )
                    self._complete_mission(mission_id, auth_msg, success=False)
                    return auth_msg
                if local_iteration == 0:
                    fallback = await self._fallback_chat(message, exc)
                    self._complete_mission(mission_id, fallback, success=bool(fallback.strip()))
                    return fallback
                final_builder.add_extra(f"Runs API hatasi: {exc}")
                err_msg = final_builder.build()
                self._complete_mission(mission_id, err_msg, success=False)
                return err_msg

            if run.session_id:
                sid = run.session_id
                self.state.current_session_id = sid
                from hermes.client.session_store import save_session_id

                save_session_id(sid)

            self.state.current_run_id = run.id
            run_output = await self._consume_run_events(run, builder, max_steps)
            if not builder.streamed.strip() and not builder.extras and run_output:
                builder.add_final_output(run_output)

            combined = builder.build()
            if looks_like_missing_tools(combined):
                recovered = await self._recover_local_action(original_message)
                if recovered:
                    append_conversation_turn("user", original_message)
                    append_conversation_turn("assistant", recovered)
                    self._complete_mission(mission_id, recovered, success=True)
                    return recovered
            local_call = parse_local_tool_request(combined)
            if local_call and local_call.execution_target == "server":
                from hermes.server.models import ToolResultPayload

                blocked = ToolResultPayload(
                    tool_call_id=f"local-{uuid4().hex[:12]}",
                    success=False,
                    error=f"{local_call.name} is a server-only tool and cannot run on the Windows client.",
                )
                current_input = format_tool_result_message(
                    local_call.name,
                    blocked,
                    user_message=original_message,
                )
                continue
            if local_call and local_call.name in self._registry:
                result = await self._execute_local_tool(
                    local_call, run.id, user_message=original_message
                )
                self._remember_tool_result(local_call.name, result, original_message)
                current_input = format_tool_result_message(
                    local_call.name,
                    result,
                    user_message=original_message,
                )
                continue
            if local_call:
                recovered = await self._recover_local_action(original_message)
                if recovered:
                    append_conversation_turn("user", original_message)
                    append_conversation_turn("assistant", recovered)
                    self._complete_mission(mission_id, recovered, success=True)
                    return recovered
                from hermes.server.models import ToolResultPayload

                invalid = ToolResultPayload(
                    tool_call_id=f"local-{uuid4().hex[:12]}",
                    success=False,
                    error=f"Gecersiz veya desteklenmeyen arac: {local_call.name}",
                )
                current_input = format_tool_result_message(
                    local_call.name,
                    invalid,
                    user_message=original_message,
                )
                continue

            final_builder = builder
            if self.state.session_notice and self.state.session_notice not in final_builder.build():
                final_builder.add_extra(self.state.session_notice)
            break

        result = final_builder.build()
        if looks_like_missing_tools(result):
            recovered = await self._recover_local_action(original_message)
            result = recovered or (
                "Yerel araclar hazir. Ne yapmami istedigini biraz daha net yazar misin? "
                "Ornek: ekrani oku, dns degistir, chrome ac."
            )
        elif (
            not self.state.metadata.get("local_tool_executed_this_turn")
            and self._looks_like_fake_pc_success(result)
        ):
            conv_ctx = ConversationalContext.load()
            route = GoalRouter(self._registry).route(original_message, conv_ctx)
            if route.intent:
                recovered = await self._execute_resolved_local_intent(
                    route.intent,
                    original_message,
                    conv_ctx,
                    resolved_references=route.resolved_references,
                )
                if recovered:
                    result = recovered
        if result.strip():
            append_conversation_turn("user", original_message)
            append_conversation_turn("assistant", result)
        self._complete_mission(mission_id, result, success=bool(result.strip()))
        return result

    def _log_routing_trace(
        self,
        tool_name: str,
        arguments: dict[str, object],
        result: Any,
        *,
        source: str,
    ) -> None:
        from hermes.agent.routing_trace import RoutingTrace

        output = getattr(result, "output", None)
        verified = False
        if isinstance(output, dict):
            verified = bool(output.get("verified"))
        trace = RoutingTrace(
            user_goal=tool_name,
            resolved_target=str(arguments.get("path") or arguments.get("app") or ""),
            selected_tool=tool_name,
            arguments=dict(arguments),
            route_source=source,
            executed=True,
            verified=verified,
            final_result="success" if bool(getattr(result, "success", False)) else "failed",
        )
        trace.log()
        self.state.metadata["routing_trace"] = {
            "USER_GOAL": trace.user_goal,
            "RESOLVED_TARGET": trace.resolved_target,
            "SELECTED_TOOL": trace.selected_tool,
            "EXECUTION_TARGET": trace.execution_target,
            "ARGUMENTS": trace.arguments,
            "EXECUTED": trace.executed,
            "VERIFIED": trace.verified,
            "FINAL_RESULT": trace.final_result,
        }

    @staticmethod
    def _looks_like_fake_pc_success(text: str) -> bool:
        import re

        lower = (text or "").casefold()
        if not lower.strip():
            return False
        return bool(
            re.search(
                r"\b(actim|acildi|açıldı|olusturdum|oluşturdum|hazirladim|hazırladım|yaptim|yaptım)\b",
                lower,
            )
        )

    async def _recover_local_action(self, message: str) -> str:
        from hermes.agent.goal_router import GoalRouter
        from hermes.context.conversational_context import ConversationalContext

        conv_ctx = ConversationalContext.load()
        route = GoalRouter(self._registry).route(message, conv_ctx)
        if route.intent:
            return await self._execute_resolved_local_intent(
                route.intent,
                message,
                conv_ctx,
                resolved_references=route.resolved_references,
            )

        from hermes.agent.local_intent import guess_local_action, summarize_local_result
        from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence

        if has_actionable_sequence(message):
            steps = plan_local_sequence(message)
            if len(steps) >= 2:
                return await self._execute_local_sequence(steps, message)

        intent = guess_local_action(message)
        if intent is None:
            return ""
        result = await self._execute_local_tool(
            intent.request, run_id=f"local-{uuid4().hex[:12]}", user_message=message
        )
        self._remember_tool_result(intent.request.name, result, message)
        text = summarize_local_result(intent, result)
        await self._set_status(AgentPhase.COMPLETED, "Yerel islem tamamlandi.")
        return text

    async def _execute_local_sequence(
        self, steps: list[Any], message: str, *, conv_ctx: Any | None = None
    ) -> str:
        from hermes.agent.local_intent import LocalIntent, summarize_local_result
        from hermes.client.session_store import append_conversation_turn
        from hermes.context.conversational_context import ConversationalContext
        from hermes.mission.write_content import is_placeholder_write_content

        ctx = conv_ctx or ConversationalContext.load()
        summaries: list[str] = []
        await self._set_status(
            AgentPhase.PLANNING,
            f"{len(steps)} adim sirayla calistiriliyor...",
            {"steps": len(steps)},
        )
        for index, intent in enumerate(steps, start=1):
            if not isinstance(intent, LocalIntent):
                continue
            await self._set_status(
                AgentPhase.EXECUTING,
                f"Adim {index}/{len(steps)}: {intent.summary}",
                {"tool": intent.request.name, "step": index},
            )
            if intent.request.name in ("write_file", "create_file") and is_placeholder_write_content(
                str(intent.request.arguments.get("content") or "")
            ):
                if intent.request.name != "create_file":
                    summaries.append(f"{index}. Dosyaya yazilacak icerigi anlayamadim.")
                    break
            exec_req = intent.request
            if intent.request.name == "create_file":
                from hermes.tools.manifest import LocalToolRequest

                exec_req = LocalToolRequest(
                    "write_file",
                    {"path": str(intent.request.arguments.get("path") or ""), "content": ""},
                )
            result = await self._execute_local_tool(
                exec_req,
                run_id=f"local-{uuid4().hex[:12]}",
                user_message=message,
            )
            if intent.request.name in ("write_file", "create_file") and getattr(result, "success", False):
                from hermes.agent.local_verify import verify_write_file_result
                from hermes.server.models import ToolResultPayload

                if isinstance(result, ToolResultPayload):
                    result = await verify_write_file_result(
                        result, dict(exec_req.arguments)
                    )
            if intent.request.name == "open_path" and getattr(result, "success", False):
                result = await self._verify_open_path_result(
                    result, str(intent.request.arguments.get("path") or "")
                )
            if intent.request.name == "rename_path" and getattr(result, "success", False):
                from hermes.agent.local_verify import verify_rename_path_result
                from hermes.server.models import ToolResultPayload

                if isinstance(result, ToolResultPayload):
                    result = await verify_rename_path_result(
                        result, dict(intent.request.arguments)
                    )
            self._remember_tool_result(intent.request.name, result, message)
            if bool(getattr(result, "success", False)):
                self._update_conversational_context(ctx, intent.request.name, result, intent)
            summaries.append(f"{index}. {summarize_local_result(intent, result)}")

        ctx.save()
        text = "\n".join(summaries) if summaries else "Sirali islemler tamamlandi."
        if self.state.session_notice:
            text = f"{self.state.session_notice}\n\n{text}"
        append_conversation_turn("user", message)
        append_conversation_turn("assistant", text)
        await self._set_status(AgentPhase.COMPLETED, "Sirali islemler tamamlandi.")
        return text

    def _remember_tool_result(self, name: str, result: Any, user_message: str) -> None:
        output = getattr(result, "output", None)
        entry = {
            "name": name,
            "success": bool(getattr(result, "success", False)),
            "output": output,
            "user_message": user_message,
        }
        self.state.last_tool_results.append(entry)
        self.state.last_tool_results = self.state.last_tool_results[-8:]

    def _tool_result_verified(self, tool_name: str, result: Any) -> bool:
        output = getattr(result, "output", None)
        if isinstance(output, dict):
            if output.get("verified") is True:
                return True
            if output.get("verification_failed"):
                return False
        return bool(getattr(result, "verified", False))

    def _update_conversational_context(
        self, ctx: Any, tool_name: str, result: Any, intent: Any | None = None
    ) -> None:
        if not bool(getattr(result, "success", False)):
            return
        if not self._tool_result_verified(tool_name, result):
            return
        output = getattr(result, "output", None)
        ctx.update_from_tool(tool_name, output, success=True, verified=True)
        if intent is not None:
            from hermes.agent.conversation_flow import is_user_facing_summary
            from hermes.agent.local_intent import summarize_local_result

            summary = summarize_local_result(intent, result)
            if summary and is_user_facing_summary(summary):
                ctx.record_verified_action(summary)

    async def _execute_resolved_local_intent(
        self,
        intent: Any,
        message: str,
        conv_ctx: Any,
        *,
        resolved_references: dict[str, str] | None = None,
    ) -> str:
        from hermes.agent.local_intent import summarize_local_result
        from hermes.agent.local_verify import (
            validate_write_parent_directory,
            verify_create_folder_result,
            verify_write_file_result,
        )
        from hermes.agent.status_messages import format_verifying_status
        from hermes.mission.write_content import is_placeholder_write_content
        from hermes.server.models import ToolResultPayload

        req = intent.request
        refs = dict(resolved_references or {})
        if message:
            conv_ctx.set_current_objective(message)

        if req.name in ("create_file", "write_file"):
            from hermes.context.file_intent import FileIntentKind, classify_file_intent

            content = str(req.arguments.get("content") or "")
            kind = (
                FileIntentKind.CREATE_FILE
                if req.name == "create_file"
                else classify_file_intent(message, content=content)
            )
            if kind == FileIntentKind.MODIFY_CONTENT and not content.strip():
                conv_ctx.save()
                return "Dosyaya yazilacak icerigi anlayamadim."
            if kind == FileIntentKind.WRITE_FILE and (
                not content.strip() or is_placeholder_write_content(content)
            ):
                conv_ctx.save()
                return "Dosyaya yazilacak icerigi anlayamadim."

            exec_req = req
            if req.name == "create_file":
                from hermes.tools.manifest import LocalToolRequest

                exec_req = LocalToolRequest(
                    "write_file",
                    {"path": str(req.arguments.get("path") or ""), "content": ""},
                )

            target_folder = refs.get("target_folder")
            ok, reason = validate_write_parent_directory(
                str(exec_req.arguments.get("path") or ""),
                target_folder,
            )
            if not ok:
                conv_ctx.save()
                return f"Hedef klasor dogrulanamadi: {reason}"

            result = await self._execute_local_tool(
                exec_req, run_id=f"local-{uuid4().hex[:12]}", user_message=message
            )
            await self._set_status(AgentPhase.VERIFYING, format_verifying_status("write_file"))
            if isinstance(result, ToolResultPayload):
                result = await verify_write_file_result(result, dict(exec_req.arguments))
            else:
                payload = ToolResultPayload(
                    tool_call_id=f"local-{uuid4().hex[:12]}",
                    success=bool(getattr(result, "success", False)),
                    output=getattr(result, "output", None),
                    error=getattr(result, "error", None),
                )
                result = await verify_write_file_result(payload, dict(exec_req.arguments))

            self._remember_tool_result(exec_req.name, result, message)
            if bool(getattr(result, "success", False)):
                self._update_conversational_context(conv_ctx, req.name, result, intent)
            conv_ctx.save()
            from hermes.agent.user_messages import (
                format_file_create_message,
                format_modify_content_message,
            )

            if kind == FileIntentKind.MODIFY_CONTENT:
                return format_modify_content_message(intent, result)
            return format_file_create_message(intent, result, kind=kind.value)

        if req.name == "create_folder":
            result = await self._execute_local_tool(
                req, run_id=f"local-{uuid4().hex[:12]}", user_message=message
            )
            await self._set_status(AgentPhase.VERIFYING, format_verifying_status(req.name))
            if isinstance(result, ToolResultPayload):
                result = await verify_create_folder_result(result, dict(req.arguments))
            self._remember_tool_result(req.name, result, message)
            if bool(getattr(result, "success", False)):
                self._update_conversational_context(conv_ctx, req.name, result, intent)
            conv_ctx.save()
            self._log_routing_trace(req.name, req.arguments, result, source="resolved_intent")
            return summarize_local_result(intent, result)

        if req.name == "rename_path":
            result = await self._execute_local_tool(
                req, run_id=f"local-{uuid4().hex[:12]}", user_message=message
            )
            await self._set_status(AgentPhase.VERIFYING, format_verifying_status(req.name))
            if isinstance(result, ToolResultPayload):
                from hermes.agent.local_verify import verify_rename_path_result

                result = await verify_rename_path_result(result, dict(req.arguments))
            self._remember_tool_result(req.name, result, message)
            if bool(getattr(result, "success", False)):
                self._update_conversational_context(conv_ctx, req.name, result, intent)
            conv_ctx.save()
            return summarize_local_result(intent, result)

        if req.name == "read_file":
            result = await self._execute_local_tool(
                req, run_id=f"local-{uuid4().hex[:12]}", user_message=message
            )
            self._remember_tool_result(req.name, result, message)
            if bool(getattr(result, "success", False)) and self._tool_result_verified(req.name, result):
                self._update_conversational_context(conv_ctx, req.name, result, intent)
            conv_ctx.save()
            return summarize_local_result(intent, result)

        result = await self._execute_local_tool(
            req, run_id=f"local-{uuid4().hex[:12]}", user_message=message
        )
        if req.name == "open_path" and bool(getattr(result, "success", False)):
            await self._set_status(AgentPhase.VERIFYING, format_verifying_status(req.name))
            result = await self._verify_open_path_result(
                result, str(req.arguments.get("path") or "")
            )
        self._remember_tool_result(req.name, result, message)
        if req.name == "open_path":
            if self._tool_result_verified(req.name, result):
                self._update_conversational_context(conv_ctx, req.name, result, intent)
        elif bool(getattr(result, "success", False)):
            self._update_conversational_context(conv_ctx, req.name, result, intent)
        conv_ctx.save()
        return summarize_local_result(intent, result)

    async def _verify_open_path_result(self, result: Any, path: str) -> Any:
        from pathlib import Path

        from hermes.server.models import ToolResultPayload

        output = dict(getattr(result, "output", None) or {})
        tool_call_id = getattr(result, "tool_call_id", f"local-{uuid4().hex[:12]}")
        success = bool(getattr(result, "success", False))
        if output.get("reused") or output.get("verified") is True:
            return ToolResultPayload(
                tool_call_id=tool_call_id,
                success=success,
                output=output,
                error=getattr(result, "error", None),
            )

        path_obj = Path(path).expanduser()
        if not path_obj.is_absolute():
            path_obj = Path.home() / "Desktop" / path_obj
        try:
            path_obj = path_obj.resolve()
        except OSError:
            path_obj = Path(path)

        if path_obj.exists():
            output["verified"] = True
            output["path"] = str(path_obj)
            output["is_directory"] = path_obj.is_dir()
            return ToolResultPayload(
                tool_call_id=tool_call_id,
                success=success,
                output=output,
                error=getattr(result, "error", None),
            )

        stem = path_obj.stem
        folder_name = path_obj.name
        verified = False
        try:
            from hermes.tools.manifest import LocalToolRequest

            observe = await self._execute_local_tool(
                LocalToolRequest("list_windows", {}),
                run_id=f"local-{uuid4().hex[:12]}",
            )
            if bool(getattr(observe, "success", False)) and isinstance(
                getattr(observe, "output", None), dict
            ):
                titles = [
                    str(item)
                    for item in (observe.output.get("windows") or [])
                ]
                verified = any(
                    stem.casefold() in title.casefold()
                    or folder_name.casefold() in title.casefold()
                    for title in titles
                )
        except Exception:
            verified = False

        output["verified"] = verified
        if not verified:
            output["verification_note"] = (
                "Acma komutu gonderildi ancak pencerenin acildigini dogrulayamadim: "
                f"{path}"
            )
        return ToolResultPayload(
            tool_call_id=tool_call_id,
            success=success,
            output=output,
            error=getattr(result, "error", None),
        )

    def _with_tool_memory(self, message: str) -> str:
        if not self.state.last_tool_results:
            return message
        import json

        lines: list[str] = []
        for item in self.state.last_tool_results[-4:]:
            preview = json.dumps(item.get("output"), ensure_ascii=False, default=str)[:400]
            lines.append(f"- {item.get('name')}: {preview}")
        return (
            "SON_YEREL_ISLEMLER (Windows client, onceki adimlar — ozet/devam icin kullan):\n"
            + "\n".join(lines)
            + "\n\nKullanici: "
            + message
        )

    async def _execute_mission_local_tool(
        self,
        local_call: LocalToolRequest,
        run_id: str,
    ) -> Any:
        """Mission steps verify once, in MissionEngine._observe_and_verify."""
        return await self._execute_local_tool(local_call, run_id, verify=False)

    async def _execute_local_tool(
        self,
        local_call: LocalToolRequest,
        run_id: str,
        *,
        user_message: str = "",
        verify: bool = True,
    ) -> Any:
        from hermes.tools.execution_target import ExecutionTarget, validate_runtime_execution
        from hermes.tools.pc_manager import PCManager

        allowed, reason = validate_runtime_execution(
            local_call.name,
            self._registry,
            runtime=ExecutionTarget.CLIENT,
            user_message=user_message,
            envelope_target=local_call.execution_target,
        )
        if not allowed:
            from hermes.server.models import ToolResultPayload

            return ToolResultPayload(
                tool_call_id=f"local-{uuid4().hex[:12]}",
                success=False,
                error=reason,
            )

        from hermes.security.bulk_risk import assess_bulk_risk

        bulk = assess_bulk_risk(
            local_call.name,
            user_message,
            dict(local_call.arguments),
        )
        if bulk.requires_confirmation:
            confirmed = any(
                token in (user_message or "").casefold()
                for token in ("evet", "onay", "onayla", "devam", "tamam")
            )
            if not confirmed:
                from hermes.server.models import ToolResultPayload

                return ToolResultPayload(
                    tool_call_id=f"local-{uuid4().hex[:12]}",
                    success=False,
                    error=bulk.reason,
                )

        from hermes.agent.status_messages import format_executing_status

        await self._set_status(
            AgentPhase.EXECUTING,
            format_executing_status(local_call.name, dict(local_call.arguments)),
            {"tool": local_call.name, "execution_target": "client"},
        )
        self.state.metadata["local_tool_executed_this_turn"] = True
        pc = PCManager(self._registry, self._executor)
        command = {
            "name": local_call.name,
            "arguments": local_call.arguments,
            "execution_target": local_call.execution_target or "client",
            "mission_id": local_call.mission_id,
            "step_id": local_call.step_id,
        }
        tool = self._registry.get(local_call.name)
        skip = bool(self._settings.ui.auto_approve_local_tools)
        if (
            not skip
            and tool is not None
            and tool.risk_level in (RiskLevel.NORMAL_MODIFICATION, RiskLevel.HIGH_RISK)
        ):
            skip = False
        try:
            return await pc.process(
                command,
                run_id=run_id,
                skip_approval=skip,
                user_message=user_message,
                verify=verify,
            )
        except ToolApprovalRequiredError as exc:
            if skip:
                return await pc.process(
                    command,
                    run_id=run_id,
                    skip_approval=True,
                    verify=verify,
                )
            await self._set_status(AgentPhase.AWAITING_APPROVAL, "Yerel tool onayi bekleniyor...")
            approval = ApprovalRequest(
                id=run_id,
                run_id=run_id,
                title=f"Yerel tool onayi: {local_call.name}",
                description=exc.reason,
                tool_name=local_call.name,
            )
            self._approval.register(approval)
            user_input = ""
            if self._on_approval_required:
                user_input = await self._on_approval_required(approval)
            parsed = await self._approval.resolve(run_id, user_input=user_input)
            from hermes.server.models import ApprovalDecision, ToolResultPayload

            if parsed.decision in (ApprovalDecision.REJECT, ApprovalDecision.CANCEL):
                return ToolResultPayload(
                    tool_call_id=f"local-{uuid4().hex[:12]}",
                    success=False,
                    error="Kullanici yerel tool calistirmayi reddetti.",
                )
            self._approval.mark_run_bulk_approved(run_id)
            return await pc.process(
                command, run_id=run_id, skip_approval=True, verify=verify
            )

    async def _consume_run_events(
        self, run: Run, builder: ResponseBuilder, max_steps: int
    ) -> str | None:
        run_output = None
        stream = self._server.stream_events(run.id)
        try:
            async for event in stream:
                if event.type not in (
                    RunEventType.MESSAGE,
                    RunEventType.MESSAGE_DELTA,
                    RunEventType.ASSISTANT_DELTA,
                ):
                    self.state.step_count += 1
                if self.state.step_count > max_steps:
                    try:
                        await self._server.stop_run(run.id)
                    except HermesServerError:
                        logger.debug("stop_run_ignored", run_id=run.id)
                    await self._set_status(
                        AgentPhase.FAILED, "Maksimum adım sınırına ulaşıldı."
                    )
                    break
                terminal, output = await self._handle_event(event, builder, run.id)
                if output:
                    run_output = output
                if terminal:
                    break
        finally:
            await stream.aclose()
        return run_output

    async def _fallback_chat(self, message: str, original_error: HermesServerError) -> str:
        await self._set_status(AgentPhase.EXECUTING, "Runs API kullanılamıyor, chat fallback...")
        builder = ResponseBuilder()
        builder.add_extra(f"[Runs API: {original_error}]")
        from hermes.server.models import ChatRequest

        request = ChatRequest(message=message, stream=True)
        stream = self._server.chat_stream(request)
        try:
            async for event in stream:
                if event.type in (
                    RunEventType.MESSAGE,
                    RunEventType.MESSAGE_DELTA,
                    RunEventType.ASSISTANT_DELTA,
                ):
                    content = (
                        event.data.get("content")
                        or event.data.get("delta")
                        or event.data.get("text", "")
                    )
                    if content:
                        builder.add_delta(str(content))
                if event.type == RunEventType.RUN_COMPLETED:
                    output = event.data.get("output") or event.data.get("summary")
                    if output:
                        builder.add_final_output(str(output))
                if event.type == RunEventType.DONE:
                    break
        finally:
            await stream.aclose()
        return builder.build()

    async def _handle_event(
        self, event: RunEvent, builder: ResponseBuilder, run_id: str
    ) -> tuple[bool, str | None]:
        if event.type in (
            RunEventType.MESSAGE,
            RunEventType.MESSAGE_DELTA,
            RunEventType.ASSISTANT_DELTA,
        ):
            content = (
                event.data.get("content") or event.data.get("delta") or event.data.get("text") or ""
            )
            if content:
                builder.add_delta(str(content))
            return (False, None)

        if event.type == RunEventType.TOOL_STARTED:
            tool = event.data.get("tool") or event.data.get("name", "tool")
            await self._set_status(AgentPhase.EXECUTING, f"Tool çalışıyor: {tool}...")
            return (False, None)

        if event.type == RunEventType.TOOL_COMPLETED:
            await self._set_status(AgentPhase.VERIFYING, "Tool sonucu alındı.")
            return (False, None)

        if event.type == RunEventType.APPROVAL_REQUIRED:
            await self._handle_approval(event, builder, run_id)
            return (False, None)

        if event.type == RunEventType.RUN_COMPLETED:
            output = (
                event.data.get("output") or event.data.get("summary") or event.data.get("message")
            )
            if output:
                builder.add_final_output(str(output))
            await self._set_status(AgentPhase.COMPLETED, "Görev tamamlandı.")
            return (True, str(output) if output else None)

        if event.type == RunEventType.RUN_FAILED:
            err = event.data.get("error") or event.data.get("message", "Bilinmeyen hata")
            builder.add_extra(f"Hata: {err}")
            await self._set_status(AgentPhase.FAILED, str(err))
            return (True, None)

        if event.type == RunEventType.RUN_CANCELLED:
            await self._set_status(AgentPhase.CANCELLED, "İşlem iptal edildi.")
            return (True, None)

        if event.type == RunEventType.ERROR:
            builder.add_extra(f"Hata: {event.data.get('message', 'Bilinmeyen hata')}")
            return (False, None)

        if event.type == RunEventType.DONE:
            return (True, None)

        return (False, None)

    async def _handle_approval(self, event: RunEvent, builder: ResponseBuilder, run_id: str) -> None:
        await self._set_status(AgentPhase.AWAITING_APPROVAL, "Onay bekleniyor...")
        tool_name = event.data.get("tool") or event.data.get("tool_name", "")
        description = event.data.get("description") or event.data.get("message", "")
        approval = ApprovalRequest(
            id=run_id,
            run_id=run_id,
            title=f"Onay gerekli: {tool_name or 'islem'}",
            description=str(description),
            tool_name=str(tool_name),
            raw=event.data,
        )
        self._approval.register(approval)

        prompt = approval.title
        if approval.description:
            prompt += f"\n{approval.description}"
        prompt += "\n\nOnayliyor musunuz? (evet / iptal / hepsini onayliyorum)"
        builder.add_extra(prompt)

        user_input = ""
        if self._on_approval_required:
            user_input = await self._on_approval_required(approval)
        elif self._settings.ui.auto_approve_local_tools:
            user_input = "evet"

        parsed = await self._approval.resolve(run_id, user_input=user_input)
        choice = map_decision_to_hermes_choice(parsed.decision)
        await self._server.submit_approval(HermesApprovalSubmit(run_id=run_id, choice=choice))

        if choice.value == "deny":
            await self._set_status(AgentPhase.CANCELLED, "İşlem reddedildi.")
            await self._server.stop_run(run_id)
            return
        await self._set_status(AgentPhase.EXECUTING, "Onaylandı, devam ediliyor...")

    def get_local_capabilities(self) -> dict[str, Any]:
        return self._registry.to_capabilities_dict()

    def get_local_context(self) -> LocalClientContext:
        return self._local_context
