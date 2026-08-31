from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hermes.ui.state import UIState


class ChatWindow:
    """Compact chat window built with customtkinter."""

    def __init__(
        self,
        state: UIState,
        *,
        on_send: Callable[[str], None],
        on_open_settings: Callable[[], None] | None = None,
        width: int = 420,
        height: int = 560,
    ) -> None:
        import customtkinter as ctk

        self._ctk = ctk
        self._state = state
        self._on_send = on_send
        self._on_open_settings = on_open_settings
        self._root = ctk.CTk()
        self._root.title("HERMES")
        self._root.geometry(f"{width}x{height}")
        self._root.minsize(360, 420)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._build_ui()
        self._root.protocol("WM_DELETE_WINDOW", self.hide)

    @property
    def root(self) -> Any:
        return self._root

    def _build_ui(self) -> None:
        ctk = self._ctk
        header = ctk.CTkFrame(self._root, corner_radius=0)
        header.pack(fill="x", padx=0, pady=0)

        self._conn_dot = ctk.CTkLabel(header, text="●", text_color=self._state.connection_color(), width=20)
        self._conn_dot.pack(side="left", padx=(12, 4), pady=10)

        self._conn_label = ctk.CTkLabel(header, text=self._state.connection_label(), anchor="w")
        self._conn_label.pack(side="left", padx=(0, 8), pady=10)

        self._status_label = ctk.CTkLabel(
            header,
            text=self._state.status_text,
            text_color="#94a3b8",
            anchor="e",
        )
        self._status_label.pack(side="right", padx=(0, 4), pady=10)

        if self._on_open_settings:
            settings_btn = ctk.CTkButton(
                header,
                text="Settings",
                width=72,
                height=28,
                command=self._on_open_settings,
            )
            settings_btn.pack(side="right", padx=(0, 8), pady=8)

        self._chat = ctk.CTkTextbox(self._root, wrap="word", activate_scrollbars=True)
        self._chat.pack(fill="both", expand=True, padx=12, pady=(8, 8))
        self._chat.configure(state="disabled")

        footer = ctk.CTkFrame(self._root)
        footer.pack(fill="x", padx=12, pady=(0, 12))

        self._input = ctk.CTkEntry(footer, placeholder_text="Mesajinizi yazin...")
        self._input.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._input.bind("<Return>", self._submit)

        send_btn = ctk.CTkButton(footer, text="Gonder", width=80, command=self._submit)
        send_btn.pack(side="right")

    def _submit(self, _event: object = None) -> None:
        text = self._input.get().strip()
        if not text:
            return
        self._input.delete(0, "end")
        self.append_message("user", text)
        self._on_send(text)

    def append_message(self, role: str, text: str) -> None:
        prefix = {"user": "You", "assistant": "HERMES", "status": "...", "system": "!"}.get(role, role)
        self._chat.configure(state="normal")
        self._chat.insert("end", f"{prefix}: {text}\n\n")
        self._chat.see("end")
        self._chat.configure(state="disabled")

    def refresh_state(self) -> None:
        self._conn_dot.configure(text_color=self._state.connection_color())
        self._conn_label.configure(text=self._state.connection_label())
        self._status_label.configure(text=self._state.status_text)

    def show(self) -> None:
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()
        self._input.focus_set()

    def hide(self) -> None:
        self._root.withdraw()

    def destroy(self) -> None:
        self._root.destroy()

    def schedule(self, callback: Callable[[], None]) -> None:
        self._root.after(0, callback)

    def mainloop(self) -> None:
        self._root.mainloop()
