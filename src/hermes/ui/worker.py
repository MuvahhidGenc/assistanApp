from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hermes.app.bootstrap import HermesApplication, create_application
from hermes.ui.notifications import notify_error, notify_task_completed
from hermes.ui.state import ConnectionStatus, UIState
from hermes.utils.logging import get_logger
from hermes.voice.assistant import VoiceAssistant, build_voice_assistant

logger = get_logger(__name__)


class WorkerCommand(StrEnum):
    SEND_MESSAGE = "send_message"
    SET_VOICE = "set_voice"
    SET_WAKE_WORD = "set_wake_word"
    REFRESH_CONNECTION = "refresh_connection"
    RELOAD_CONFIG = "reload_config"
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

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._thread_main, name="hermes-worker", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        if not self._loop or not self._running:
            return
        self._running = False
        future = asyncio.run_coroutine_threadsafe(
            self._command_queue.put(WorkerCommandPayload(WorkerCommand.SHUTDOWN)),  # type: ignore[union-attr]
            self._loop,
        )
        try:
            future.result(timeout=2)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=timeout)

    def reload_config(self) -> None:
        self._enqueue(WorkerCommand.RELOAD_CONFIG)

    def send_message(self, text: str) -> None:
        if self._approval_is_pending():
            self._emit("error", {"message": "Onay bekleniyor — once onaylayin veya iptal edin."})
            return
        self._enqueue(WorkerCommand.SEND_MESSAGE, {"text": text})

    def set_voice_enabled(self, enabled: bool) -> None:
        self._enqueue(WorkerCommand.SET_VOICE, {"enabled": enabled})

    def set_wake_word_enabled(self, enabled: bool) -> None:
        self._enqueue(WorkerCommand.SET_WAKE_WORD, {"enabled": enabled})

    def refresh_connection(self) -> None:
        self._enqueue(WorkerCommand.REFRESH_CONNECTION)

    def resolve_approval(self, choice: str) -> None:
        if self._approval_future and not self._approval_future.done():
            self._approval_future.set_result(choice)

    def _approval_is_pending(self) -> bool:
        return self._approval_future is not None and not self._approval_future.done()

    def _enqueue(self, kind: WorkerCommand, data: dict[str, Any] | None = None) -> None:
        if not self._loop or not self._command_queue:
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
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()
            self._loop = None

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
                logger.info("worker_started", microphone=self._voice.microphone_available if self._voice else False)
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
        self._voice = build_voice_assistant(self._app.agent, self._app.settings.voice)

        voice_enabled = self._app.settings.voice.enabled
        if not self._voice.microphone_available:
            voice_enabled = False

        await self._voice.configure_voice(
            voice_enabled=voice_enabled,
            wake_word_enabled=self._app.settings.voice.wake_word_enabled and voice_enabled,
        )

        self._wire_voice_callbacks()
        self._wire_agent_callbacks()
        await self._voice.start()
        try:
            await self._app.agent.initialize_session()
        except Exception as exc:
            logger.warning(
                "worker_session_init_failed",
                error=str(exc),
                model=self._app.settings.server.model,
            )
            self.state.append_message(
                "system",
                f"Oturum baslatilamadi ({self._app.settings.server.model}): {exc}",
            )
            self._emit("message", {"role": "system", "text": str(exc)})
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

    def _wire_voice_callbacks(self) -> None:
        assert self._voice is not None

        async def on_status(message: str) -> None:
            self.state.set_busy(True, status=message)
            self.state.append_message("status", message)
            self._emit("status", {"message": message})
            self._emit("state")

        async def on_response(response: str, source: str) -> None:
            self.state.append_message("assistant", response)
            self.state.set_busy(False, status="Hazir")
            self._emit("message", {"role": "assistant", "text": response, "source": source})
            self._emit("task_completed", {"text": response})
            self._emit("state")
            notify_task_completed(response, enabled=self._notifications_enabled)

        self._voice.on_status = on_status
        self._voice.on_response = on_response

    def _wire_agent_callbacks(self) -> None:
        assert self._app is not None
        from hermes.server.models import ApprovalRequest
        from hermes.ui.notifications import notify

        async def on_approval(approval: ApprovalRequest) -> str:
            if self._app.settings.ui.auto_approve_local_tools:
                notify(
                    "HERMES PC",
                    f"{approval.title} — otomatik onaylandi.",
                    enabled=self._notifications_enabled,
                )
                return "evet"

            if self._loop:
                self._approval_future = self._loop.create_future()
            notify(
                "HERMES Onay",
                f"{approval.title}\n{approval.description}",
                enabled=self._notifications_enabled,
            )
            self._emit(
                "approval_required",
                {
                    "run_id": approval.run_id,
                    "title": approval.title,
                    "description": approval.description,
                },
            )
            if self._approval_future:
                try:
                    return await asyncio.wait_for(self._approval_future, timeout=300)
                finally:
                    self._approval_future = None
            return "evet"

        self._app.agent._on_approval_required = on_approval  # noqa: SLF001

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

        if not self._voice:
            self._emit("error", {"message": "Servis hazir degil. Settings kaydedin."})
            return

        if payload.kind == WorkerCommand.SEND_MESSAGE:
            if self._approval_is_pending():
                self._emit("error", {"message": "Onay bekleniyor — once onaylayin veya iptal edin."})
                return
            text = str(payload.data.get("text", "")).strip()
            if not text:
                return
            self.state.append_message("user", text)
            self.state.set_busy(True, status="Gonderiliyor...")
            self._emit("message", {"role": "user", "text": text})
            self._emit("state")
            try:
                await self._voice.handle_text_input(text)
            except Exception as exc:
                logger.exception("send_message_failed")
                self.state.set_busy(False, status="Hata")
                self.state.append_message("system", str(exc))
                self._emit("error", {"message": str(exc)})
                self._emit("state")
                notify_error(str(exc), enabled=self._notifications_enabled)

        elif payload.kind == WorkerCommand.SET_VOICE:
            enabled = bool(payload.data.get("enabled", True))
            await self._voice.configure_voice(voice_enabled=enabled)
            self.state.set_voice(voice_enabled=self._voice.voice_enabled)
            self._emit("state")

        elif payload.kind == WorkerCommand.SET_WAKE_WORD:
            enabled = bool(payload.data.get("enabled", True))
            if enabled:
                if not self._voice.microphone_available or not self._voice.voice_enabled:
                    self._emit("error", {"message": "Wake word icin mikrofon ve ses acik olmali."})
                    return
                await self._voice.configure_voice(wake_word_enabled=True)
            else:
                await self._voice.configure_voice(wake_word_enabled=False)
            self.state.set_voice(wake_word_enabled=self._voice.wake_word_enabled)
            self._emit("state")

        elif payload.kind == WorkerCommand.REFRESH_CONNECTION:
            await self._check_connection()

    async def _check_connection(self) -> None:
        if not self._app:
            self.state.set_connection(ConnectionStatus.ERROR, detail="Uygulama hazir degil")
            self._emit("state")
            return
        was_disconnected = self.state.connection == ConnectionStatus.DISCONNECTED
        try:
            health = await self._app.server.health()
            if health.status in ("ok", "healthy"):
                self.state.set_connection(ConnectionStatus.CONNECTED, detail="Sunucuya bagli")
                await self._app.agent.initialize_session()
                notice = self._app.agent.state.session_notice
                if notice:
                    self.state.status_text = notice
            else:
                self.state.set_connection(ConnectionStatus.DISCONNECTED, detail=f"Health: {health.status}")
        except Exception as exc:
            from hermes.server.client import HermesServerError

            detail = str(exc)
            if isinstance(exc, HermesServerError) and "connection attempts failed" in detail.lower():
                detail = "Sunucu bekleniyor..."
            self.state.set_connection(ConnectionStatus.DISCONNECTED, detail=detail)
        self._emit("state")

    async def _connection_monitor(self) -> None:
        interval = 60
        if self._app:
            interval = self._app.settings.ui.connection_check_interval_seconds
        while self._running:
            await asyncio.sleep(max(interval, 15))
            await self._check_connection()

    async def _shutdown_services(self, *, keep_running: bool = False) -> None:
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
