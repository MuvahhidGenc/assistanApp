from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hermes.config.settings_store import SettingsFormData, load_settings_form, save_settings_form


class SettingsWindow:
    """User settings editor (API, voice, notifications)."""

    def __init__(
        self,
        master: Any,
        *,
        on_saved: Callable[[], None] | None = None,
        on_open_logs: Callable[[], None] | None = None,
    ) -> None:
        import customtkinter as ctk

        self._ctk = ctk
        self._on_saved = on_saved
        self._on_open_logs = on_open_logs
        self._token_visible = False

        self._window = ctk.CTkToplevel(master)
        self._window.title("HERMES Settings")
        self._window.geometry("460x620")
        self._window.minsize(420, 580)
        self._window.transient(master)

        self._error_label = ctk.CTkLabel(self._window, text="", text_color="#ef4444", wraplength=400)
        self._error_label.pack(fill="x", padx=16, pady=(12, 0))

        form = ctk.CTkFrame(self._window)
        form.pack(fill="both", expand=True, padx=16, pady=12)

        ctk.CTkLabel(form, text="Hermes API URL", anchor="w").pack(fill="x", pady=(4, 2))
        self._api_url = ctk.CTkEntry(form, placeholder_text="http://host:8642")
        self._api_url.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(form, text="API Token / Key", anchor="w").pack(fill="x", pady=(4, 2))
        token_row = ctk.CTkFrame(form, fg_color="transparent")
        token_row.pack(fill="x", pady=(0, 8))
        self._api_key = ctk.CTkEntry(token_row, placeholder_text="API key", show="*")
        self._api_key.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._toggle_key_btn = ctk.CTkButton(
            token_row,
            text="Show",
            width=70,
            command=self._toggle_api_key_visibility,
        )
        self._toggle_key_btn.pack(side="right")

        ctk.CTkLabel(form, text="Model", anchor="w").pack(fill="x", pady=(4, 2))
        self._model = ctk.CTkEntry(form, placeholder_text="hermes-agent")
        self._model.pack(fill="x", pady=(0, 8))

        self._short_responses = ctk.CTkSwitch(form, text="Short responses")
        self._short_responses.pack(anchor="w", pady=6)
        self._voice_enabled = ctk.CTkSwitch(form, text="Voice enabled")
        self._voice_enabled.pack(anchor="w", pady=6)
        self._wake_word = ctk.CTkSwitch(form, text="Wake word enabled")
        self._wake_word.pack(anchor="w", pady=6)
        self._notifications = ctk.CTkSwitch(form, text="Notifications enabled")
        self._notifications.pack(anchor="w", pady=6)

        buttons = ctk.CTkFrame(self._window, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkButton(buttons, text="Save", command=self._save).pack(side="left")
        ctk.CTkButton(buttons, text="View Logs", command=self._open_logs).pack(side="left", padx=(8, 0))
        ctk.CTkButton(buttons, text="Close", command=self._window.destroy).pack(side="right")

        self._load_form()

    @property
    def window(self) -> Any:
        return self._window

    def _load_form(self) -> None:
        data = load_settings_form()
        self._api_url.delete(0, "end")
        self._api_url.insert(0, data.api_url)
        self._api_key.delete(0, "end")
        self._api_key.insert(0, data.api_key)
        self._model.delete(0, "end")
        self._model.insert(0, data.model)
        self._set_switch(self._short_responses, data.prefer_short_responses)
        self._set_switch(self._voice_enabled, data.voice_enabled)
        self._set_switch(self._wake_word, data.wake_word_enabled)
        self._set_switch(self._notifications, data.notifications_enabled)
        self._error_label.configure(text="")

    @staticmethod
    def _set_switch(switch: Any, value: bool) -> None:
        if value:
            switch.select()
        else:
            switch.deselect()

    @staticmethod
    def _switch_value(switch: Any) -> bool:
        return bool(switch.get())

    def _toggle_api_key_visibility(self) -> None:
        self._token_visible = not self._token_visible
        self._api_key.configure(show="" if self._token_visible else "*")
        self._toggle_key_btn.configure(text="Hide" if self._token_visible else "Show")

    def _collect_form(self) -> SettingsFormData:
        return SettingsFormData(
            api_url=self._api_url.get().strip(),
            api_key=self._api_key.get().strip(),
            model=self._model.get().strip(),
            prefer_short_responses=self._switch_value(self._short_responses),
            voice_enabled=self._switch_value(self._voice_enabled),
            wake_word_enabled=self._switch_value(self._wake_word),
            notifications_enabled=self._switch_value(self._notifications),
        )

    def _save(self) -> None:
        try:
            save_settings_form(self._collect_form())
        except ValueError as exc:
            self._error_label.configure(text=str(exc))
            return

        self._error_label.configure(text="")
        if self._on_saved:
            self._on_saved()
        self._window.destroy()

    def _open_logs(self) -> None:
        if self._on_open_logs:
            self._on_open_logs()

    def show(self) -> None:
        self._window.lift()
        self._window.focus_force()
