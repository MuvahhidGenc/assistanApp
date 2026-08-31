from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hermes.config.settings_store import SettingsFormData, load_settings_form, save_settings_form
from hermes.ui.modern_theme import (
    BG,
    CARD_BORDER,
    INPUT_BG,
    TEXT,
    ghost_button,
    holo_card,
    primary_button,
    section_label,
)


class SettingsWindow:
    """User settings editor (API, voice, notifications)."""

    def __init__(
        self,
        master: Any,
        on_saved: Callable[[], None] | None = None,
        on_open_logs: Callable[[], None] | None = None,
    ) -> None:
        import customtkinter as ctk

        self._ctk = ctk
        self._on_saved = on_saved
        self._on_open_logs = on_open_logs
        self._token_visible = False
        self._window = ctk.CTkToplevel(master)
        self._window.title("HERMES // SYSTEM CONFIG")
        self._window.geometry("640x760")
        self._window.minsize(560, 680)
        self._window.transient(master)
        self._window.configure(fg_color=BG)

        self._error_label = ctk.CTkLabel(
            self._window,
            text="",
            text_color="#ef4444",
            wraplength=580,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self._error_label.pack(fill="x", padx=16, pady=(12, 0))

        form = holo_card(ctk, self._window)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        section_label(ctk, form, "SERVER LINK").pack(anchor="w", padx=14, pady=(14, 8))
        ctk.CTkLabel(
            form, text="Hermes API URL", anchor="w", text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(fill="x", padx=14, pady=(4, 2))
        self._api_url = ctk.CTkEntry(
            form, placeholder_text="http://host:8642", fg_color=INPUT_BG, border_color=CARD_BORDER
        )
        self._api_url.pack(fill="x", padx=14, pady=(0, 8))

        ctk.CTkLabel(
            form, text="API Token / Key", anchor="w", text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(fill="x", padx=14, pady=(4, 2))
        token_row = ctk.CTkFrame(form, fg_color="transparent")
        token_row.pack(fill="x", padx=14, pady=(0, 8))
        self._api_key = ctk.CTkEntry(
            token_row, placeholder_text="API key", show="*", fg_color=INPUT_BG, border_color=CARD_BORDER
        )
        self._api_key.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._toggle_key_btn = ghost_button(
            ctk, token_row, "SHOW", self._toggle_api_key_visibility, width=70
        )
        self._toggle_key_btn.pack(side="right")

        ctk.CTkLabel(
            form, text="Model", anchor="w", text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(fill="x", padx=14, pady=(4, 2))
        self._model = ctk.CTkEntry(
            form, placeholder_text="hermes-agent", fg_color=INPUT_BG, border_color=CARD_BORDER
        )
        self._model.pack(fill="x", padx=14, pady=(0, 8))

        section_label(ctk, form, "VOICE & ALERTS").pack(anchor="w", padx=14, pady=(12, 6))
        switch_font = ctk.CTkFont(family="Consolas", size=11)
        self._short_responses = ctk.CTkSwitch(form, text="Short responses", font=switch_font)
        self._short_responses.pack(anchor="w", padx=14, pady=6)
        self._voice_enabled = ctk.CTkSwitch(form, text="Voice enabled", font=switch_font)
        self._voice_enabled.pack(anchor="w", padx=14, pady=6)
        self._wake_word = ctk.CTkSwitch(form, text="Wake word enabled", font=switch_font)
        self._wake_word.pack(anchor="w", padx=14, pady=6)
        self._notifications = ctk.CTkSwitch(form, text="Notifications enabled", font=switch_font)
        self._notifications.pack(anchor="w", padx=14, pady=(6, 14))

        buttons = ctk.CTkFrame(self._window, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(8, 12))
        primary_button(ctk, buttons, "SAVE", self._save).pack(side="left")
        ghost_button(ctk, buttons, "LOGS", self._open_logs).pack(side="left", padx=(8, 0))
        ghost_button(ctk, buttons, "CLOSE", self._window.destroy).pack(side="right")
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
        self._toggle_key_btn.configure(text="HIDE" if self._token_visible else "SHOW")

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
        if self._on_saved is not None:
            self._on_saved()
        self._window.destroy()

    def _open_logs(self) -> None:
        if self._on_open_logs is not None:
            self._on_open_logs()

    def show(self) -> None:
        self._window.lift()
        self._window.focus_force()
