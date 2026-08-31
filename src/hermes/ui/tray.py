from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from hermes.ui.icon import create_tray_icon
from hermes.ui.state import UIState


class TrayController:
    """System tray icon and context menu via pystray."""

    def __init__(
        self,
        state: UIState,
        on_open_chat: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_open_logs: Callable[[], None],
        on_toggle_voice: Callable[[], None],
        on_toggle_wake_word: Callable[[], None],
        on_show_status: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._state = state
        self._on_open_chat = on_open_chat
        self._on_open_settings = on_open_settings
        self._on_open_logs = on_open_logs
        self._on_toggle_voice = on_toggle_voice
        self._on_toggle_wake_word = on_toggle_wake_word
        self._on_show_status = on_show_status
        self._on_exit = on_exit
        self._icon = None
        self._thread = None

    def start(self) -> None:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Open Chat", self._handle_open_chat, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Voice On/Off",
                self._handle_toggle_voice,
                checked=self._voice_checked,
            ),
            pystray.MenuItem(
                "Wake Word On/Off",
                self._handle_toggle_wake,
                checked=self._wake_checked,
                enabled=self._wake_enabled,
            ),
            pystray.MenuItem("Settings", self._handle_settings),
            pystray.MenuItem("View Logs", self._handle_logs),
            pystray.MenuItem("Status", self._handle_status),
            pystray.MenuItem("Exit", self._handle_exit),
        )
        self._icon = pystray.Icon("HERMES", create_tray_icon(), "HERMES Assistant", menu)
        self._thread = threading.Thread(target=self._icon.run, name="hermes-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()

    def update_menu(self) -> None:
        if self._icon is not None:
            self._icon.update_menu()

    def _voice_checked(self, _item: Any) -> bool:
        return self._state.voice_enabled

    def _wake_checked(self, _item: Any) -> bool:
        return self._state.wake_word_enabled

    def _wake_enabled(self, _item: Any) -> bool:
        return self._state.microphone_available and self._state.voice_enabled

    def _handle_open_chat(self, _icon: Any, _item: Any) -> None:
        self._on_open_chat()

    def _handle_toggle_voice(self, _icon: Any, _item: Any) -> None:
        self._on_toggle_voice()

    def _handle_toggle_wake(self, _icon: Any, _item: Any) -> None:
        self._on_toggle_wake_word()

    def _handle_settings(self, _icon: Any, _item: Any) -> None:
        self._on_open_settings()

    def _handle_logs(self, _icon: Any, _item: Any) -> None:
        self._on_open_logs()

    def _handle_status(self, _icon: Any, _item: Any) -> None:
        self._on_show_status()

    def _handle_exit(self, _icon: Any, _item: Any) -> None:
        self._on_exit()
