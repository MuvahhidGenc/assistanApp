from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from hermes.agent.local_intent import (
    LocalIntent,
    guess_file_action,
    guess_install_action,
    match_local_intent,
    summarize_local_result,
)
from hermes.agent.response_builder import ResponseBuilder
from hermes.agent.server_tasks import should_defer_to_server
from hermes.agent.task_planner import has_actionable_sequence, plan_local_sequence
from hermes.client.local_context import LocalClientContext
from hermes.config.settings import AppSettings
from hermes.security import ApprovalManager, AuditLogger, PolicyEngine, map_decision_to_hermes_choice
from hermes.server.client import HermesServerClient, HermesServerError
from hermes.server.models import (
    ApprovalRequest,
    HermesApprovalSubmit,
    Run,
    RunEvent,
    RunEventType,
    SessionCreate,
    ToolCallRequest,
    ToolResultPayload,
)
from hermes.tools.base import ToolExecutionResult
from hermes.tools.executor import ToolApprovalRequiredError, ToolExecutor
from hermes.tools.manifest import LocalToolRequest, format_tool_result_message, parse_local_tool_request
from hermes.tools.registry import ToolRegistry, create_default_registry
from hermes.utils.logging import get_logger

logger = get_logger(__name__)

_MAX_LOCAL_TOOL_ITERATIONS = 10


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


StatusCallback = Callable[[AgentPhase, str, dict[str, Any]], Awaitable[None] | None]


@dataclass
class AgentState:
    phase: AgentPhase = AgentPhase.IDLE
    status_message: str = ""
    current_run_id: str | None = None
    current_session_id: str | None = None
    step_count: int = 0
    plan_steps: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    session_renewed: bool = False
    session_notice: str | None = None
    recent_local_ops: list[str] = field(default_factory=list)


class AgentOrchestrator:
    """Client-side agent loop using Hermes Agent Runs API."""

    def __init__(
        self,
        settings: AppSettings,
        server: HermesServerClient,
        *,
        registry: ToolRegistry | None = None,
        on_status: StatusCallback | None = None,
        on_approval_required: Callable[[ApprovalRequest], Awaitable[str]] | None = None,
    ) -> None:
        self._settings = settings
        self._server = server
        self._registry = registry or create_default_registry()
        self._local_context = LocalClientContext(
            self._registry,
            client_name=settings.client.name,
        )
        self._on_status = on_status
        self._on_approval_required = on_approval_required

        self._policy = PolicyEngine(settings.security.require_approval_for)
        self._audit = AuditLogger(
            settings.security.audit_log_path,
            settings.security.redact_patterns,
        )
        self._approval = ApprovalManager()
        self._executor = ToolExecutor(
            self._registry,
            self._policy,
            self._audit,
            self._approval,
        )
        self.state = AgentState()

    async def _set_status(self, phase: AgentPhase, message: str, **extra: Any) -> None:
        self.state.phase = phase
        self.state.status_message = message
        self.state.metadata.update(extra)
        logger.debug("agent_status", phase=phase.value, message=message, **extra)
        if self._on_status:
            result = self._on_status(phase, message, extra)
            if result is not None:
                await result

    async def initialize_session(self) -> str:
        from hermes.client.session_store import load_session_id, new_conversation_id, save_session_id

        metadata = self._local_context.build_session_metadata()
        stored = load_session_id()

        if stored:
            try:
                session = await self._server.create_session(
                    SessionCreate(id=stored, session_id=stored, metadata=metadata),
                )
                session_id = session.id or stored
                self.state.current_session_id = session_id
                self.state.session_renewed = False
                save_session_id(session_id)
                await self.register_local_capabilities(session_id)
                return session_id
            except HermesServerError as exc:
                if exc.status_code == 400:
                    session_id = new_conversation_id()
                    self.state.current_session_id = session_id
                    self.state.session_renewed = True
                    await self.register_local_capabilities(session_id)
                    return session_id
                self.state.current_session_id = stored
                self.state.session_renewed = True
                return stored

        try:
            session = await self._server.create_session(SessionCreate(metadata=metadata))
            session_id = session.id or new_conversation_id()
        except HermesServerError:
            session_id = new_conversation_id()
            self.state.session_renewed = True

        self.state.current_session_id = session_id
        save_session_id(session_id)
        await self.register_local_capabilities(session_id)
        self.state.metadata["local_tools_registered"] = True
        self.state.metadata["local_tool_count"] = self._local_context.tool_count
        return session_id

    async def register_local_capabilities(self, session_id: str | None) -> None:
        if not session_id:
            return
        metadata = self._local_context.build_session_metadata()
        updated = await self._server.update_session(session_id, metadata=metadata)
        if updated is None:
            logger.debug("session_metadata_embedded_in_run", session_id=session_id)
        self.state.metadata["local_tools_registered"] = True
        self.state.metadata["local_tool_count"] = self._local_context.tool_count

    async def _ensure_session_id(self, session_id: str | None) -> str | None:
        sid = session_id or self.state.current_session_id
        if sid:
            return sid
        try:
            return await self.initialize_session()
        except HermesServerError:
            from hermes.client.session_store import new_conversation_id, save_session_id

            sid = new_conversation_id()
            self.state.current_session_id = sid
            save_session_id(sid)
            return sid

    async def _run_local_tool(
        self,
        request: LocalToolRequest,
        *,
        user_message: str = "",
    ) -> ToolExecutionResult:
        from hermes.tools.pc_manager import PCManager

        await self._set_status(AgentPhase.EXECUTING, f"Yerel tool calistiriliyor: {request.name}...")
        pc = PCManager(self._registry, self._executor)
        command = {"name": request.name, "arguments": request.arguments}
        skip = self._settings.ui.auto_approve_local_tools
        run_id = self.state.current_run_id or f"local-{uuid4().hex[:12]}"
        try:
            payload = await pc.process(command, run_id=run_id, skip_approval=skip)
            success = payload.success if hasattr(payload, "success") else True
            output = payload.output if hasattr(payload, "output") else payload
            error = payload.error if hasattr(payload, "error") else None
            return ToolExecutionResult(success=success, output=output, error=error)
        except ToolApprovalRequiredError:
            if skip:
                payload = await pc.process(command, run_id=run_id, skip_approval=True)
                return ToolExecutionResult(
                    success=payload.success,
                    output=payload.output,
                    error=payload.error,
                )
            raise

    async def _execute_local_intent(self, intent: LocalIntent) -> ToolExecutionResult:
        run_id = self.state.current_run_id or f"local-{uuid4().hex[:12]}"
        payload = await self._execute_local_tool(intent.request, run_id)
        result = ToolExecutionResult(
            success=payload.success,
            output=payload.output,
            error=payload.error,
        )
        summary = summarize_local_result(intent, result)
        self.state.recent_local_ops.append(summary)
        return result

    async def _execute_local_sequence(self, intents: list[LocalIntent]) -> str:
        parts: list[str] = []
        for intent in intents:
            result = await self._execute_local_intent(intent)
            parts.append(summarize_local_result(intent, result))
        return "\n".join(parts)

    async def _try_local_fast_path(self, message: str) -> str | None:
        if should_defer_to_server(message):
            return None

        for guesser in (guess_file_action, guess_install_action):
            intent = guesser(message)
            if intent:
                result = await self._execute_local_intent(intent)
                return summarize_local_result(intent, result)

        if has_actionable_sequence(message):
            steps = plan_local_sequence(message)
            if len(steps) >= 2:
                return await self._execute_local_sequence(steps)

        intent = match_local_intent(message)
        if intent:
            result = await self._execute_local_intent(intent)
            return summarize_local_result(intent, result)

        return None

    async def process_message(self, message: str, *, session_id: str | None = None) -> str:
        from hermes.client.session_store import append_conversation_turn, load_conversation_history

        local_result = await self._try_local_fast_path(message)
        if local_result is not None:
            append_conversation_turn("user", message)
            append_conversation_turn("assistant", local_result)
            await self._set_status(AgentPhase.COMPLETED, "Yerel islem tamamlandi.")
            return local_result

        max_steps = self._settings.client.max_agent_steps
        self.state.step_count = 0

        sid = await self._ensure_session_id(session_id)
        if sid:
            await self.register_local_capabilities(sid)

        instructions = self._local_context.build_run_instructions()
        current_input = message
        original_message = message
        history = load_conversation_history()
        if self.state.recent_local_ops:
            ops_text = "\n".join(self.state.recent_local_ops[-5:])
            current_input = f"SON_YEREL_ISLEMLER:\n{ops_text}\n\nKullanici: {message}"

        append_conversation_turn("user", message)
        final_builder = ResponseBuilder()

        for local_iteration in range(_MAX_LOCAL_TOOL_ITERATIONS):
            builder = ResponseBuilder()
            self.state.step_count = 0
            await self._set_status(AgentPhase.UNDERSTANDING, "İstek anlaşılıyor...")

            try:
                run = await self._server.create_run(
                    current_input,
                    session_id=sid or "",
                    instructions=instructions,
                    conversation_history=history,
                )
            except HermesServerError as exc:
                if local_iteration == 0:
                    return await self._fallback_chat(message, exc)
                final_builder.add_extra(f"Runs API hatasi: {exc}")
                break

            if not sid and run.session_id:
                sid = run.session_id
                self.state.current_session_id = sid
                from hermes.client.session_store import save_session_id

                save_session_id(sid)

            self.state.current_run_id = run.id
            run_output = await self._consume_run_events(run, builder, max_steps)
            if not builder.streamed.strip() and not builder.extras and run_output:
                builder.add_final_output(run_output)

            combined = builder.build()

            from hermes.voice.spoken import looks_like_missing_tools

            if looks_like_missing_tools(combined):
                intent = match_local_intent(message)
                if not intent and "ekran" in message.lower():
                    intent = LocalIntent(
                        LocalToolRequest(name="read_screen_text", arguments={}),
                    )
                if intent and intent.request.name in self._registry:
                    result = await self._execute_local_intent(intent)
                    replacement = summarize_local_result(intent, result)
                    append_conversation_turn("assistant", replacement)
                    return replacement

            local_call = parse_local_tool_request(combined)
            if local_call and local_call.name in self._registry:
                result = await self._execute_local_tool(local_call, run.id)
                current_input = format_tool_result_message(
                    local_call.name,
                    result,
                    user_message=original_message,
                )
                continue

            final_builder = builder
            break

        response = final_builder.build()
        append_conversation_turn("assistant", response)
        return response

    async def _execute_local_tool(
        self,
        local_call: LocalToolRequest,
        run_id: str,
    ) -> ToolResultPayload:
        from hermes.tools.pc_manager import PCManager

        await self._set_status(
            AgentPhase.EXECUTING,
            f"Yerel tool calistiriliyor: {local_call.name}...",
            tool=local_call.name,
        )
        pc = PCManager(self._registry, self._executor)
        command = {"name": local_call.name, "arguments": local_call.arguments}
        skip = self._settings.ui.auto_approve_local_tools
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
            user_input = ""
            if self._on_approval_required:
                user_input = await self._on_approval_required(approval)
            parsed = await self._approval.resolve(run_id, user_input=user_input)
            if parsed.decision.value in ("reject", "cancel"):
                return ToolResultPayload(
                    tool_call_id=f"local-{uuid4().hex[:12]}",
                    success=False,
                    error="Kullanici yerel tool calistirmayi reddetti.",
                )
            return await pc.process(command, run_id=run_id, skip_approval=True)

    async def _consume_run_events(
        self,
        run: Run,
        builder: ResponseBuilder,
        max_steps: int,
    ) -> str | None:
        run_output: str | None = None
        stream: AsyncIterator[RunEvent] = self._server.stream_events(run.id)
        try:
            async for event in stream:
                self.state.step_count += 1
                if self.state.step_count > max_steps:
                    await self._server.stop_run(run.id)
                    await self._set_status(AgentPhase.FAILED, "Maksimum adım sınırına ulaşıldı.")
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
        self,
        event: RunEvent,
        builder: ResponseBuilder,
        run_id: str,
    ) -> tuple[bool, str | None]:
        if event.type in (RunEventType.MESSAGE, RunEventType.MESSAGE_DELTA, RunEventType.ASSISTANT_DELTA):
            content = (
                event.data.get("content")
                or event.data.get("delta")
                or event.data.get("text")
                or ""
            )
            if content:
                builder.add_delta(str(content))
            return False, None

        if event.type == RunEventType.TOOL_STARTED:
            tool = event.data.get("tool") or event.data.get("name", "tool")
            await self._set_status(AgentPhase.EXECUTING, f"Tool çalışıyor: {tool}...")
            return False, None

        if event.type == RunEventType.TOOL_COMPLETED:
            await self._set_status(AgentPhase.VERIFYING, "Tool sonucu alındı.")
            return False, None

        if event.type == RunEventType.APPROVAL_REQUIRED:
            await self._handle_approval(event, builder, run_id)
            return False, None

        if event.type == RunEventType.RUN_COMPLETED:
            output = event.data.get("output") or event.data.get("summary") or event.data.get("message")
            if output:
                builder.add_final_output(str(output))
            await self._set_status(AgentPhase.COMPLETED, "Görev tamamlandı.")
            return True, str(output) if output else None

        if event.type == RunEventType.RUN_FAILED:
            err = event.data.get("error") or event.data.get("message", "Bilinmeyen hata")
            builder.add_extra(f"Hata: {err}")
            await self._set_status(AgentPhase.FAILED, str(err))
            return True, None

        if event.type == RunEventType.RUN_CANCELLED:
            await self._set_status(AgentPhase.CANCELLED, "İşlem iptal edildi.")
            return True, None

        if event.type == RunEventType.ERROR:
            builder.add_extra(f"Hata: {event.data.get('message', 'Bilinmeyen hata')}")
            return False, None

        if event.type == RunEventType.DONE:
            return True, None

        return False, None

    async def _handle_approval(
        self,
        event: RunEvent,
        builder: ResponseBuilder,
        run_id: str,
    ) -> None:
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
        prompt += "\n\nOnay gerekli — Onayliyor musunuz? (evet / iptal / hepsini onayliyorum)"
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
        else:
            await self._set_status(AgentPhase.EXECUTING, "Onaylandı, devam ediliyor...")

    def get_local_capabilities(self) -> dict[str, Any]:
        return self._registry.to_capabilities_dict()

    def get_local_context(self) -> LocalClientContext:
        return self._local_context
