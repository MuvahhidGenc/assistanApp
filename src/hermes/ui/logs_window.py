from __future__ import annotations

from typing import Any

from hermes.ui.logs_viewer import open_log_file, read_log_tail


class LogsWindow:
    """Simple viewer for the last N log lines."""

    def __init__(self, master: Any) -> None:
        import customtkinter as ctk

        self._window = ctk.CTkToplevel(master)
        self._window.title("HERMES Logs")
        self._window.geometry("760x520")
        self._window.minsize(640, 420)
        self._window.transient(master)

        self._info = ctk.CTkLabel(self._window, text="", anchor="w", wraplength=700)
        self._info.pack(fill="x", padx=12, pady=(12, 6))

        self._text = ctk.CTkTextbox(self._window, wrap="none", activate_scrollbars=True)
        self._text.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        buttons = ctk.CTkFrame(self._window, fg_color="transparent")
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(buttons, text="Refresh", command=self.refresh).pack(side="left")
        ctk.CTkButton(buttons, text="Open log file", command=self._open_file).pack(side="left", padx=(8, 0))
        ctk.CTkButton(buttons, text="Close", command=self._window.destroy).pack(side="right")

        self.refresh()

    def refresh(self) -> None:
        lines, message = read_log_tail(max_lines=200)
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        if message:
            self._info.configure(text=message)
            self._text.insert("1.0", message)
        else:
            self._info.configure(text=f"Son {len(lines)} satir gosteriliyor.")
            self._text.insert("1.0", "\n".join(lines))
            self._text.see("end")
        self._text.configure(state="disabled")

    def _open_file(self) -> None:
        ok, detail = open_log_file()
        if not ok:
            self._info.configure(text=detail)
            return
        self._info.configure(text=f"Log dosyasi acildi: {detail}")

    def show(self) -> None:
        self._window.lift()
        self._window.focus_force()
        self.refresh()
