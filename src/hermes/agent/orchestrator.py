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

logger = get_logger(__name__)

_MAX_LOCAL_TOOL_ITERATIONS = 25


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
    ) -> None:
        self._settings = settings
        self._server = server
        self._registry = registry or create_default_registry()
        self._local_context = LocalClientContext(self._registry, client_name=settings.client.name)
        self._on_status = on_status
        self._on_approval_required = on_approval_required
        self._policy = PolicyEngine(settings.security.require_approval_for)
        self._audit = AuditLogger(settings.security.audit_log_path, settings.security.redact_patterns)
        self._approval = ApprovalManager()
        self._executor = ToolExecutor(self._registry, self._policy, self._audit, self._approval)
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
        from hermes.agent.local_intent import guess_local_action, summarize_local_result
        from hermes.agent.server_tasks import should_defer_to_server
        from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence
        from hermes.client.session_store import append_conversation_turn

        max_steps = self._settings.client.max_agent_steps
        self.state.step_count = 0

        from hermes.agent.local_intent import guess_file_action

        file_intent = guess_file_action(message)
        if file_intent:
            result = await self._execute_local_tool(
                file_intent.request, run_id=f"local-{uuid4().hex[:12]}"
            )
            self._remember_tool_result(file_intent.request.name, result, message)
            text = summarize_local_result(file_intent, result)
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
                install_intent.request, run_id=f"local-{uuid4().hex[:12]}"
            )
            self._remember_tool_result(install_intent.request.name, result, message)
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
                return await self._execute_local_sequence(steps, message)

        if not use_server:
            intent = guess_local_action(message)
            if intent:
                result = await self._execute_local_tool(
                    intent.request, run_id=f"local-{uuid4().hex[:12]}"
                )
                self._remember_tool_result(intent.request.name, result, message)
                text = summarize_local_result(intent, result)
                if self.state.session_notice:
                    text = f"{self.state.session_notice}\n\n{text}"
                append_conversation_turn("user", message)
                append_conversation_turn("assistant", text)
                await self._set_status(AgentPhase.COMPLETED, "Yerel islem tamamlandi.")
                return text
        else:
            await self._set_status(
                AgentPhase.PLANNING,
                "Hermes sunucusu adim adim planliyor...",
            )

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
                    return (
                        "Sunucu API anahtarini reddetti. "
                        "Settings'ten ayni API_SERVER_KEY degerini kaydedin."
                    )
                if local_iteration == 0:
                    return await self._fallback_chat(message, exc)
                final_builder.add_extra(f"Runs API hatasi: {exc}")
                return final_builder.build()

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
                    return recovered
            local_call = parse_local_tool_request(combined)
            if local_call and local_call.name in self._registry:
                result = await self._execute_local_tool(local_call, run.id)
                self._remember_tool_result(local_call.name, result, original_message)
                current_input = format_tool_result_message(
                    local_call.name,
                    result,
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
        if result.strip():
            append_conversation_turn("user", original_message)
            append_conversation_turn("assistant", result)
        return result

    async def _recover_local_action(self, message: str) -> str:
        from hermes.agent.local_intent import guess_local_action, summarize_local_result
        from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence

        if has_actionable_sequence(message):
            steps = plan_local_sequence(message)
            if len(steps) >= 2:
                return await self._execute_local_sequence(steps, message)

        intent = guess_local_action(message)
        if intent is None:
            return ""
        result = await self._execute_local_tool(intent.request, run_id=f"local-{uuid4().hex[:12]}")
        self._remember_tool_result(intent.request.name, result, message)
        text = summarize_local_result(intent, result)
        await self._set_status(AgentPhase.COMPLETED, "Yerel islem tamamlandi.")
        return text

    async def _execute_local_sequence(self, steps: list[Any], message: str) -> str:
        from hermes.agent.local_intent import LocalIntent, summarize_local_result
        from hermes.client.session_store import append_conversation_turn

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
            result = await self._execute_local_tool(
                intent.request,
                run_id=f"local-{uuid4().hex[:12]}",
            )
            self._remember_tool_result(intent.request.name, result, message)
            summaries.append(f"{index}. {summarize_local_result(intent, result)}")

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

    async def _execute_local_tool(self, local_call: LocalToolRequest, run_id: str) -> Any:
        from hermes.tools.pc_manager import PCManager

        await self._set_status(
            AgentPhase.EXECUTING,
            f"Yerel tool calistiriliyor: {local_call.name}...",
            {"tool": local_call.name},
        )
        pc = PCManager(self._registry, self._executor)
        command = {"name": local_call.name, "arguments": local_call.arguments}
        tool = self._registry.get(local_call.name)
        skip = bool(self._settings.ui.auto_approve_local_tools)
        if (
            not skip
            and tool is not None
            and tool.risk_level in (RiskLevel.NORMAL_MODIFICATION, RiskLevel.HIGH_RISK)
        ):
            skip = False
        try:
            return await pc.process(command, run_id=run_id, skip_approval=skip)
        except ToolApprovalRequiredError as exc:
            if skip:
                return await pc.process(command, run_id=run_id, skip_approval=True)
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
            return await pc.process(command, run_id=run_id, skip_approval=True)

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
