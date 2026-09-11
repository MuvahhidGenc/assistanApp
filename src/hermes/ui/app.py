from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes.config.paths import user_config_path
from hermes.config.settings import AppSettings
from hermes.ui.chat_window import ChatWindow
from hermes.ui.logs_window import LogsWindow
from hermes.ui.notifications import notify, notify_error
from hermes.ui.settings_window import SettingsWindow
from hermes.ui.state import UIState
from hermes.ui.tray import TrayController
from hermes.ui.worker import BackgroundWorker


class HermesTrayApplication:
    """Windows tray assistant with optional chat window."""

    def __init__(
        self,
        settings: AppSettings,
        config_path: str | None = None,
        debug: bool = False,
    ) -> None:
        self._settings = settings
        self._config_path = config_path
        self._debug = debug
        self._state = UIState(wake_word_enabled=settings.voice.wake_word_enabled)
        self._worker = BackgroundWorker(
            config_path=config_path,
            debug=debug,
            state=self._state,
            on_event=self._on_worker_event,
        )
        self._chat = None
        self._tray = None
        self._closing = False

    def run(self) -> None:
        self._worker.start()
        self._chat = ChatWindow(
            self._state,
            on_send=self._worker.send_message,
            on_open_settings=self._open_settings,
            on_approve=self._worker.resolve_approval,
            on_toggle_voice=self._toggle_voice,
            width=self._settings.ui.window_width,
            height=self._settings.ui.window_height,
        )
        self._tray = TrayController(
            self._state,
            on_open_chat=self._open_chat,
            on_open_settings=self._open_settings,
            on_open_logs=self._open_logs,
            on_toggle_voice=self._toggle_voice,
            on_toggle_wake_word=self._toggle_wake_word,
            on_show_status=self._show_status,
            on_exit=self._exit,
        )
        self._tray.start()
        self._chat.show()
        self._chat.mainloop()
        self._shutdown()

    def _on_worker_event(self, event: str, payload: dict[str, Any]) -> None:
        if not self._chat or self._closing:
            return

        def _apply() -> None:
            if event == "message":
                role = payload.get("role", "assistant")
                text = payload.get("text", "")
                source = str(payload.get("source") or "text")
                if role == "user" and source == "voice":
                    self._chat.append_message("user", text, via="voice")
                elif role != "user":
                    self._chat.append_message(role, text)
            elif event == "status":
                # Status updates HUD only — never the conversation transcript.
                self._chat.refresh_state()
            elif event == "approval_required":
                self._chat.show()
                self._chat.show_approval(
                    str(payload.get("title") or "Onay gerekli"),
                    str(payload.get("description") or ""),
                )
            elif event in ("approval_timeout", "approval_resolved"):
                self._chat.hide_approval()
            elif event in (
                "state",
                "task_completed",
                "error",
                "stopped",
                "config_reloaded",
            ):
                self._chat.refresh_state()
                if self._tray:
                    self._tray.update_menu()
            if event == "config_reloaded" and payload.get("ok"):
                self._reload_local_settings()
            if event == "error":
                notify_error(
                    str(payload.get("message") or "Hata"),
                    enabled=self._settings.ui.notifications_enabled,
                )

        self._chat.schedule(_apply)

    def _reload_local_settings(self) -> None:
        path = user_config_path()
        self._settings = AppSettings.load(path if path.exists() else None)
        if path.exists():
            self._config_path = str(path)

    def _open_chat(self) -> None:
        if self._chat is not None:
            self._chat.schedule(self._chat.show)

    def _open_settings(self) -> None:
        def _show() -> None:
            SettingsWindow(
                self._chat.root,
                on_saved=self._on_settings_saved,
                on_open_logs=self._open_logs,
            ).show()

        if self._chat is not None:
            self._chat.schedule(_show)

    def _open_logs(self) -> None:
        def _show() -> None:
            LogsWindow(self._chat.root).show()

        if self._chat is not None:
            self._chat.schedule(_show)

    def _on_settings_saved(self) -> None:
        self._reload_local_settings()
        self._worker.reload_config()

    def _toggle_voice(self) -> None:
        target = not self._state.voice_enabled
        self._worker.set_voice_enabled(target)

    def _toggle_wake_word(self) -> None:
        target = not self._state.wake_word_enabled
        self._worker.set_wake_word_enabled(target)

    def _show_status(self) -> None:
        snap = self._state.snapshot()
        lines = [
            f"Baglanti: {snap['connection']}",
            f"Ses: {'acik' if snap['voice_enabled'] else 'kapali'}",
            f"Wake word: {'acik' if snap['wake_word_enabled'] else 'kapali'}",
            f"Mikrofon: {'var' if snap['microphone_available'] else 'yok'}",
            f"Durum: {snap['status_text']}",
        ]
        notify("HERMES Durum", "\n".join(lines), enabled=self._settings.ui.notifications_enabled)
        self._worker.refresh_connection()
        self._open_chat()

    def _exit(self) -> None:
        if self._chat is not None:
            self._chat.schedule(self._shutdown_and_quit)

    def _shutdown_and_quit(self) -> None:
        self._shutdown()
        if self._chat is not None:
            self._chat.root.quit()

    def _shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._tray is not None:
            self._tray.stop()
        self._worker.stop()
        if self._chat is not None:
            try:
                self._chat.destroy()
            except Exception:
                return


def run_tray_app(config_path: str | None = None, debug: bool = False) -> None:
    from pathlib import Path

    from hermes.config.paths import resolve_config_path
    from hermes.config.settings_store import bootstrap_user_config
    from hermes.utils.logging import get_logger, setup_app_logging

    setup_app_logging()
    logger = get_logger(__name__)
    bootstrap_user_config()
    resolved = resolve_config_path(Path(config_path) if config_path else None)
    logger.info("tray_app_start", config=str(resolved), debug=debug)
    settings = AppSettings.load(Path(config_path) if config_path else None)
    app = HermesTrayApplication(settings, config_path=config_path, debug=debug)
    logger.info("tray_app_running")
    app.run()
    logger.info("tray_app_stopped")
