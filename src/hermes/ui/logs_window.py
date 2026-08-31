from __future__ import annotations

from typing import Any

from hermes.ui.logs_viewer import open_log_file, read_log_tail
from hermes.ui.modern_theme import (
    BG,
    CARD,
    CARD_BORDER,
    INPUT_BG,
    TEXT,
    ghost_button,
    primary_button,
    section_label,
    selectable_log,
)


class LogsWindow:
    """Simple viewer for the last N log lines."""

    def __init__(self, master: Any) -> None:
        import customtkinter as ctk

        self._window = ctk.CTkToplevel(master)
        self._window.title("HERMES // SYSTEM LOGS")
        self._window.geometry("1080x720")
        self._window.minsize(900, 560)
        self._window.transient(master)
        self._window.configure(fg_color=BG)

        header = ctk.CTkFrame(
            self._window,
            fg_color=CARD,
            corner_radius=6,
            border_width=1,
            border_color=CARD_BORDER,
        )
        header.pack(fill="x", padx=12, pady=(12, 6))
        section_label(ctk, header, "DIAGNOSTIC LOG").pack(side="left", padx=12, pady=8)

        self._info = ctk.CTkLabel(
            self._window,
            text="",
            anchor="w",
            wraplength=1020,
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self._info.pack(fill="x", padx=14, pady=(0, 6))

        self._text = selectable_log(ctk, self._window, wrap="none")
        self._text.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        buttons = ctk.CTkFrame(self._window, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        ghost_button(ctk, buttons, "REFRESH", self.refresh).pack(side="left")
        ghost_button(ctk, buttons, "OPEN FILE", self._open_file).pack(side="left", padx=(8, 0))
        primary_button(ctk, buttons, "CLOSE", self._window.destroy).pack(side="right")
        self.refresh()

    def refresh(self) -> None:
        lines, message = read_log_tail(max_lines=200)
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        if message:
            self._info.configure(text=message)
        else:
            self._info.configure(text=f"Last {len(lines)} lines displayed.")
        self._text.insert("end", "\n".join(lines))
        self._text.see("end")
        self._text.configure(state="disabled")

    def _open_file(self) -> None:
        ok, detail = open_log_file()
        self._info.configure(text=detail if not ok else f"Log dosyasi acildi: {detail}")

    def show(self) -> None:
        self._window.lift()
        self._window.focus_force()
        self.refresh()
