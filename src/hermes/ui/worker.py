from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.app.bootstrap import HermesApplication, create_application
from hermes.ui.notifications import notify_error, notify_task_completed
from hermes.ui.state import ActivityMode, ConnectionStatus, UIState
from hermes.utils.logging import get_logger
from hermes.voice.assistant import VoiceAssistant, build_voice_assistant

logger = get_logger(__name__)


class WorkerCommand(StrEnum):
    SEND_MESSAGE = "send_message"
    SET_VOICE = "set_voice"
    SET_WAKE_WORD = "set_wake_word"
    REFRESH_CONNECTION = "refresh_connection"
    RELOAD_CONFIG = "reload_config"
    RESOLVE_APPROVAL = "resolve_approval"
    SHUTDOWN = "shutdown"


@dataclass
class WorkerCommandPayload:
    kind: WorkerCommand
    data: dict[str, Any] = field(default_factory=dict)


UIEventHandler = Callable[[str, dict[str, Any]], None]


@dataclass
class BackgroundWorker:
    """Async Hermes + voice loop running on a dedicated thread."""

    config_path: str | None = None
    debug: bool = False
    state: UIState = field(default_factory=UIState)
    on_event: UIEventHandler | None = None
    _thread: threading.Thread | None = field(default=None, init=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False)
    _command_queue: asyncio.Queue[WorkerCommandPayload] | None = field(default=None, init=False)
    _running: bool = field(default=False, init=False)
    _app: HermesApplication | None = field(default=None, init=False)
    _voice: VoiceAssistant | None = field(default=None, init=False)
    _connection_task: asyncio.Task[None] | None = field(default=None, init=False)
    _notifications_enabled: bool = field(default=True, init=False)
    _approval_future: asyncio.Future[str] | None = field(default=None, init=False)
    _rpc_server: Any | None = field(default=None, init=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._thread_main, name="hermes-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        if self._loop is not None and self._command_queue is not None:
            self._running = False
            future = asyncio.run_coroutine_threadsafe(
                self._command_queue.put(WorkerCommandPayload(WorkerCommand.SHUTDOWN)),
                self._loop,
            )
            try:
                future.result(timeout=2)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def reload_config(self) -> None:
        self._enqueue(WorkerCommand.RELOAD_CONFIG)

    def send_message(self, text: str) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        if self._approval_is_pending():
            if self._resolve_approval_from_text(cleaned):
                return
            self._emit(
                "error",
                {"message": "Onay bekleniyor. Once Onayla/evet veya Iptal yazin."},
            )
            return
        self._enqueue(WorkerCommand.SEND_MESSAGE, {"text": cleaned})

    def set_voice_enabled(self, enabled: bool) -> None:
        self._enqueue(WorkerCommand.SET_VOICE, {"enabled": enabled})

    def set_wake_word_enabled(self, enabled: bool) -> None:
        self._enqueue(WorkerCommand.SET_WAKE_WORD, {"enabled": enabled})

    def refresh_connection(self) -> None:
        self._enqueue(WorkerCommand.REFRESH_CONNECTION)

    def resolve_approval(self, decision: str) -> None:
        """Resolve pending approval immediately (must not go through command queue)."""
        self._apply_approval_decision(decision, source="ui")

    def _approval_is_pending(self) -> bool:
        return self._approval_future is not None and not self._approval_future.done()

    def _resolve_approval_from_text(self, text: str) -> bool:
        lowered = text.casefold()
        if any(token in lowered for token in ("evet", "onay", "onayla", "olur", "tamam", "yes", "ok")):
            self._apply_approval_decision("evet", source="text")
            self.state.append_message("user", text)
            self._emit("message", {"role": "user", "text": text})
            return True
        if any(token in lowered for token in ("hayir", "hayır", "iptal", "no", "red", "vazgec")):
            self._apply_approval_decision("iptal", source="text")
            self.state.append_message("user", text)
            self._emit("message", {"role": "user", "text": text})
            return True
        return False

    def _apply_approval_decision(self, decision: str, *, source: str = "ui") -> None:
        if self._loop is None:
            return
        normalized = str(decision or "iptal").strip().casefold() or "iptal"
        if normalized in {"onayla", "approve", "yes", "ok"}:
            normalized = "evet"
        elif normalized not in {"evet", "iptal"}:
            normalized = "iptal"

        def _set_future() -> None:
            future = self._approval_future
            if future is not None and not future.done():
                future.set_result(normalized)
                logger.info("approval_resolved", decision=normalized, source=source)
            else:
                logger.warning("approval_resolve_ignored", decision=normalized, source=source)
            self._emit("approval_resolved", {"decision": normalized})

        self._loop.call_soon_threadsafe(_set_future)

    def _enqueue(self, kind: WorkerCommand, data: dict[str, Any] | None = None) -> None:
        if self._loop is None or self._command_queue is None:
            return
        payload = WorkerCommandPayload(kind=kind, data=data or {})
        asyncio.run_coroutine_threadsafe(self._command_queue.put(payload), self._loop)

    def _emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        if self.on_event:
            self.on_event(event, payload or {})

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()

    async def _async_main(self) -> None:
        from hermes.utils.logging import get_logger

        logger = get_logger(__name__)
        self._command_queue = asyncio.Queue()
        self.state.set_connection(ConnectionStatus.CONNECTING, detail="Baslatiliyor...")
        self._emit("state")
        logger.info("worker_starting")

        try:
            started = await self._start_services()
            if started:
                logger.info(
                    "worker_started",
                    microphone=bool(self._voice and self._voice.microphone_available),
                )
        except Exception as exc:
            logger.error("worker_startup_failed", error=str(exc), exc_info=True)
            self.state.set_connection(ConnectionStatus.ERROR, detail=str(exc))
            self._emit("state")
            notify_error(str(exc), enabled=self._notifications_enabled)
            self._emit("error", {"message": str(exc)})

        while self._running:
            try:
                payload = await asyncio.wait_for(self._command_queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            if payload.kind == WorkerCommand.SHUTDOWN:
                break
            await self._handle_command(payload)

        await self._shutdown_services()
        logger.info("worker_loop_stopped")

    async def _start_services(self) -> bool:
        from pathlib import Path

        from hermes.config.paths import resolve_config_path

        resolved = resolve_config_path(Path(self.config_path) if self.config_path else None)
        self.config_path = str(resolved)
        self._app = create_application(
            self.config_path,
            cli_mode=False,
            debug=self.debug,
            configure_logging=False,
        )
        self._notifications_enabled = self._app.settings.ui.notifications_enabled
        self._voice = build_voice_assistant(
            self._app.agent,
            self._app.settings.voice,
            elevenlabs_api_key=self._app.settings.elevenlabs_api_key.get_secret_value(),
        )
        from hermes.voice.tts import _audit

        _audit(
            "VOICE_ASSISTANT_ACTIVE",
            phase="worker_startup",
            voice_enabled=self._app.settings.voice.enabled,
            tts_class=type(self._voice.tts).__name__ if self._voice.tts else "none",
            tts_available=bool(self._voice.tts and self._voice.tts.is_available()),
            thread=__import__("threading").current_thread().name,
        )

        voice_enabled = self._app.settings.voice.enabled
        wake_enabled = (
            self._app.settings.voice.wake_word_enabled
            and voice_enabled
            and self._voice.microphone_available
        )
        await self._voice.configure_voice(
            voice_enabled=voice_enabled,
            wake_word_enabled=wake_enabled,
        )
        self._wire_voice_callbacks()
        self._wire_agent_callbacks()
        self._restore_mission_ui_state()
        await self._voice.start()

        await self._ensure_session()
        self._start_rpc_server()

        self.state.set_microphone_available(self._voice.microphone_available)
        self.state.set_voice(
            voice_enabled=self._voice.voice_enabled,
            wake_word_enabled=self._voice.wake_word_enabled,
        )
        await self._check_connection()
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
        self._connection_task = asyncio.create_task(self._connection_monitor())
        return True

    def _start_rpc_server(self) -> None:
        if not self._app or not self._loop:
            return
        from hermes.client.rpc_server import LocalToolRpcServer
        from hermes.client.session_store import get_or_create_rpc_secret, get_rpc_bind

        host, port = get_rpc_bind()
        secret = get_or_create_rpc_secret()
        try:
            self._rpc_server = LocalToolRpcServer(
                self._app.agent._executor,
                self._loop,
                host=host,
                port=port,
                secret=secret,
            )
            self._rpc_server.start()
            logger.info(
                "rpc_server_ready",
                url=f"http://{host}:{port}",
                secret_path_hint="%LOCALAPPDATA%\\HermesClient\\state\\client.json",
            )
        except OSError as exc:
            logger.error("rpc_server_start_failed", host=host, port=port, error=str(exc))
            self._rpc_server = None

    def _wire_voice_callbacks(self) -> None:
        async def on_status(message: str) -> None:
            activity = ActivityMode.THINKING
            lower = message.casefold()
            if "dinliyorum" in lower or "dinle" in lower or "duydum" in lower:
                activity = ActivityMode.LISTENING
            elif "calistir" in lower or "adim" in lower:
                activity = ActivityMode.EXECUTING
            self.state.set_activity(activity, status=message)
            self.state.append_message("status", message)
            self._emit("status", {"message": message})
            self._emit("state")

        async def on_response(response: str, source: str) -> None:
            self.state.append_message("assistant", response)
            self.state.set_activity(ActivityMode.IDLE, status="Hazir")
            self._emit("message", {"role": "assistant", "text": response, "source": source})
            self._emit("task_completed", {"text": response})
            self._emit("state")
            notify_task_completed(response, enabled=self._notifications_enabled)

        async def on_user_input(text: str, source: str) -> None:
            self.state.append_message("user", text)
            self.state.set_activity(ActivityMode.THINKING, status="Anladım, işliyorum...")
            self._emit("message", {"role": "user", "text": text, "source": source})
            self._emit("state")

        async def on_activity(activity: str) -> None:
            try:
                mode = ActivityMode(activity)
            except ValueError:
                mode = ActivityMode.THINKING
            self.state.set_activity(mode)
            self._emit("state")

        async def on_command(text: str) -> bool:
            if self._approval_is_pending():
                return self._resolve_approval_from_text(text)
            return False

        self._voice.on_status = on_status
        self._voice.on_response = on_response
        self._voice.on_user_input = on_user_input
        self._voice.on_activity = on_activity
        self._voice.on_command = on_command

    def _wire_agent_callbacks(self) -> None:
        from hermes.server.models import ApprovalRequest
        from hermes.ui.notifications import notify

        async def on_approval(approval: ApprovalRequest) -> str:
            notify(
                "HERMES Onay",
                f"{approval.title}\n{approval.description}\nUI veya ses: evet / iptal",
                enabled=self._notifications_enabled,
            )
            loop = asyncio.get_running_loop()
            if self._approval_future and not self._approval_future.done():
                self._approval_future.cancel()
            self._approval_future = loop.create_future()
            self.state.set_activity(
                ActivityMode.AWAITING_APPROVAL,
                status="Onay bekleniyor",
            )
            self._emit("state")
            self._emit(
                "approval_required",
                {
                    "run_id": approval.run_id,
                    "title": approval.title,
                    "description": approval.description,
                },
            )
            try:
                decision = await asyncio.wait_for(self._approval_future, timeout=300)
                self._emit("approval_resolved", {"decision": decision})
                return decision
            except TimeoutError:
                self._emit("approval_timeout", {})
                self.state.append_message("system", "Onay suresi doldu, islem iptal edildi.")
                self._emit("message", {"role": "system", "text": "Onay suresi doldu, islem iptal edildi."})
                return "iptal"
            finally:
                self._approval_future = None
                if not self.state.busy:
                    self.state.set_activity(ActivityMode.IDLE, status="Hazir")
                    self._emit("state")

        self._app.agent._on_approval_required = on_approval

        async def on_agent_status(phase, message: str, extra: dict | None = None) -> None:
            from hermes.agent.orchestrator import AgentPhase

            activity = ActivityMode.THINKING
            if phase in (AgentPhase.EXECUTING, AgentPhase.VERIFYING):
                activity = ActivityMode.EXECUTING
            elif phase == AgentPhase.AWAITING_APPROVAL:
                activity = ActivityMode.AWAITING_APPROVAL
            elif phase == AgentPhase.COMPLETED:
                activity = ActivityMode.IDLE
            text = (message or "").strip()
            if text:
                self.state.set_activity(activity, status=text)
                self.state.append_message("status", text)
                self._emit("status", {"message": text})
            mission_id = str((extra or {}).get("mission_id") or "").strip() or None
            if mission_id:
                self._sync_mission_ui_from_store(mission_id)
            self._emit("state")

        self._app.agent._on_status = on_agent_status

    def _restore_mission_ui_state(self) -> None:
        from hermes.mission.store import MissionStore

        mission = MissionStore().load_active()
        if mission is None:
            self.state.clear_mission_snapshot()
            return
        self.state.set_mission_snapshot(
            mission_id=mission.mission_id,
            mission_status=mission.status.value,
            mission_goal=mission.user_goal,
            mission_progress=mission.progress_ratio,
        )

    def _sync_mission_ui_from_store(self, mission_id: str | None = None) -> None:
        from hermes.mission.store import MissionStore

        store = MissionStore()
        mission = store.load(mission_id) if mission_id else store.load_active()
        if mission is None:
            self.state.clear_mission_snapshot()
            return
        self.state.set_mission_snapshot(
            mission_id=mission.mission_id,
            mission_status=mission.status.value,
            mission_goal=mission.user_goal,
            mission_progress=mission.progress_ratio,
        )

    async def _reload_config(self) -> None:
        logger.info("worker_reload_config")
        await self._shutdown_services(keep_running=True)
        try:
            await self._start_services()
            self._emit("config_reloaded", {"ok": True})
        except Exception as exc:
            logger.error("worker_reload_failed", error=str(exc), exc_info=True)
            self.state.set_connection(ConnectionStatus.ERROR, detail=str(exc))
            self._emit("config_reloaded", {"ok": False, "message": str(exc)})
            self._emit("error", {"message": str(exc)})

    async def _handle_command(self, payload: WorkerCommandPayload) -> None:
        if payload.kind == WorkerCommand.RELOAD_CONFIG:
            await self._reload_config()
            return

        if payload.kind == WorkerCommand.RESOLVE_APPROVAL:
            decision = str(payload.data.get("decision", "iptal")).strip() or "iptal"
            self._apply_approval_decision(decision, source="queue")
            return

        if not self._voice:
            self._emit("error", {"message": "Servis hazir degil. Settings kaydedin."})
            return

        if payload.kind == WorkerCommand.SEND_MESSAGE:
            text = str(payload.data.get("text", "")).strip()
            if not text:
                return
            self.state.append_message("user", text)
            self.state.set_activity(ActivityMode.THINKING, status="Gonderiliyor...")
            self._emit("message", {"role": "user", "text": text})
            self._emit("state")
            try:
                await self._voice.handle_text_input(text)
            except Exception as exc:
                logger.exception("send_message_failed")
                self.state.set_activity(ActivityMode.IDLE, status="Hata")
                self.state.append_message("system", str(exc))
                self._emit("error", {"message": str(exc)})
                self._emit("state")
                notify_error(str(exc), enabled=self._notifications_enabled)
            return

        if payload.kind == WorkerCommand.SET_VOICE:
            enabled = bool(payload.data.get("enabled", True))
            await self._voice.configure_voice(voice_enabled=enabled)
            if enabled and not self._voice.microphone_available:
                await self._voice.configure_voice(wake_word_enabled=False)
            self.state.set_voice(voice_enabled=self._voice.voice_enabled)
            self._emit("state")
            return

        if payload.kind == WorkerCommand.SET_WAKE_WORD:
            enabled = bool(payload.data.get("enabled", True))
            if enabled:
                if not self._voice.microphone_available or not self._voice.voice_enabled:
                    self._emit(
                        "error",
                        {"message": "Wake word icin mikrofon ve ses acik olmali."},
                    )
                    return
                await self._voice.configure_voice(wake_word_enabled=True)
            else:
                await self._voice.configure_voice(wake_word_enabled=False)
            self.state.set_voice(wake_word_enabled=self._voice.wake_word_enabled)
            self._emit("state")
            return

        if payload.kind == WorkerCommand.REFRESH_CONNECTION:
            await self._check_connection()

    async def _ensure_session(self, *, retries: int = 3) -> str:
        if not self._app:
            return ""
        from hermes.server.client import HermesServerError

        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                session_id = await self._app.agent.initialize_session()
                notice = self._app.agent.state.session_notice
                if notice:
                    self.state.append_message("system", notice)
                    self._emit("message", {"role": "system", "text": notice})
                return session_id
            except Exception as exc:
                last_error = exc
                waiting = isinstance(exc, HermesServerError) and exc.is_connection_error()
                logger.warning(
                    "worker_session_waiting" if waiting else "worker_session_init_failed",
                    attempt=attempt + 1,
                    error=str(exc),
                    model=self._app.settings.server.model,
                )
                if not waiting:
                    break
                await asyncio.sleep(0.8 * (attempt + 1))
        if last_error:
            if isinstance(last_error, HermesServerError) and last_error.is_connection_error():
                self.state.append_message("system", "Sunucu bekleniyor...")
                self._emit("message", {"role": "system", "text": "Sunucu bekleniyor..."})
            else:
                self.state.append_message(
                    "system",
                    f"Oturum baslatilamadi ({self._app.settings.server.model}): {last_error}",
                )
                self._emit("message", {"role": "system", "text": str(last_error)})
        return (getattr(self._app.agent.state, "current_session_id", None) or "") if self._app else ""

    async def _check_connection(self) -> None:
        if not self._app:
            self.state.set_connection(ConnectionStatus.ERROR, detail="Uygulama hazir degil")
            self._emit("state")
            return
        previous = self.state.connection
        try:
            health = await self._app.server.health()
            if health.status in ("ok", "healthy"):
                self.state.set_connection(ConnectionStatus.CONNECTED, detail="Sunucuya bagli")
                if previous in (
                    ConnectionStatus.DISCONNECTED,
                    ConnectionStatus.ERROR,
                    ConnectionStatus.UNKNOWN,
                ):
                    await self._ensure_session()
            else:
                self.state.set_connection(
                    ConnectionStatus.DISCONNECTED, detail=f"Health: {health.status}"
                )
        except Exception as exc:
            from hermes.server.client import HermesServerError

            detail = "Sunucu bekleniyor..."
            if isinstance(exc, HermesServerError) and exc.is_auth_error():
                detail = "API anahtari reddedildi"
            elif isinstance(exc, HermesServerError) and (
                "10061" in str(exc) or "reddetti" in str(exc).lower()
            ):
                detail = "Sunucu 8642 portunu reddetti (VPS bind 0.0.0.0 + API_SERVER_KEY)"
            elif not (isinstance(exc, HermesServerError) and exc.is_connection_error()):
                text = str(exc)
                if "All connection attempts failed" not in text:
                    detail = text
            self.state.set_connection(ConnectionStatus.DISCONNECTED, detail=detail)
        self._emit("state")

    async def _connection_monitor(self) -> None:
        interval = 60
        if self._app is not None:
            interval = self._app.settings.ui.connection_check_interval_seconds
        while self._running:
            await asyncio.sleep(max(interval, 15))
            await self._check_connection()

    async def _shutdown_services(self, *, keep_running: bool = False) -> None:
        if self._rpc_server is not None:
            self._rpc_server.stop()
            self._rpc_server = None
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
            self._connection_task = None
        if self._voice:
            await self._voice.stop()
            self._voice = None
        if self._app:
            await self._app.shutdown()
            self._app = None
        if keep_running:
            self.state.set_connection(ConnectionStatus.CONNECTING, detail="Yeniden yukleniyor...")
            self._emit("state")
            return
        self.state.set_connection(ConnectionStatus.DISCONNECTED, detail="Kapatildi")
        self._emit("state")
        self._emit("stopped")
