from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hermes.ui.modern_theme import (
    BG,
    CARD,
    CARD_BORDER,
    HOLO_GREEN,
    INPUT_BG,
    NEON_GOLD,
    TEXT,
    ghost_button,
    neon_button,
    section_label,
)


class ApprovalDialog:
    """Modal, always-on-top approval window for risky local/server actions."""

    def __init__(self, parent: Any, on_decision: Callable[[str], None]) -> None:
        import customtkinter as ctk

        self._ctk = ctk
        self._parent = parent
        self._on_decision = on_decision
        self._win: Any | None = None
        self._title_label: Any | None = None
        self._desc_box: Any | None = None

    def show(self, title: str, description: str) -> None:
        if self._win is None or not self._win.winfo_exists():
            self._build()
        assert self._win is not None
        assert self._title_label is not None
        assert self._desc_box is not None

        self._title_label.configure(text=title or "Onay gerekli")
        self._desc_box.configure(state="normal")
        self._desc_box.delete("1.0", "end")
        self._desc_box.insert("1.0", description or "Bu islem icin onay gerekiyor.")
        self._desc_box.configure(state="disabled")

        self._win.deiconify()
        self._win.lift()
        self._win.attributes("-topmost", True)
        self._win.after(200, self._focus)
        self._parent.deiconify()
        self._parent.lift()

    def hide(self) -> None:
        if self._win is not None and self._win.winfo_exists():
            try:
                self._win.grab_release()
            except Exception:
                pass
            self._win.withdraw()

    def destroy(self) -> None:
        if self._win is not None and self._win.winfo_exists():
            try:
                self._win.grab_release()
            except Exception:
                pass
            self._win.destroy()
        self._win = None

    def _focus(self) -> None:
        if self._win is None or not self._win.winfo_exists():
            return
        self._win.focus_force()
        try:
            self._win.grab_set()
        except Exception:
            pass

    def _build(self) -> None:
        ctk = self._ctk
        win = ctk.CTkToplevel(self._parent)
        win.title("HERMES // AUTHORIZATION")
        win.geometry("720x380")
        win.minsize(620, 320)
        win.configure(fg_color=BG)
        win.protocol("WM_DELETE_WINDOW", self._cancel)
        win.withdraw()
        try:
            win.update_idletasks()
            px = self._parent.winfo_rootx() + max(0, (self._parent.winfo_width() - 720) // 2)
            py = self._parent.winfo_rooty() + max(0, (self._parent.winfo_height() - 380) // 2)
            win.geometry(f"720x380+{px}+{py}")
        except Exception:
            pass

        frame = ctk.CTkFrame(
            win,
            fg_color=CARD,
            corner_radius=6,
            border_width=1,
            border_color=NEON_GOLD,
        )
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        section_label(ctk, frame, "AUTHORIZATION REQUIRED").pack(anchor="w", padx=16, pady=(14, 4))

        self._title_label = ctk.CTkLabel(
            frame,
            text="Onay gerekli",
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
            text_color=TEXT,
            wraplength=500,
            justify="left",
        )
        self._title_label.pack(anchor="w", padx=16, pady=(0, 8))

        self._desc_box = ctk.CTkTextbox(
            frame,
            height=120,
            fg_color=INPUT_BG,
            text_color=TEXT,
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=4,
            wrap="word",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._desc_box.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))
        neon_button(
            ctk,
            row,
            "CONFIRM",
            lambda: self._resolve("evet"),
            accent=HOLO_GREEN,
            width=120,
            height=36,
        ).pack(side="left", padx=(0, 10))
        ghost_button(
            ctk,
            row,
            "ABORT",
            self._cancel,
            width=120,
            height=36,
        ).pack(side="left")
        ctk.CTkLabel(
            row,
            text="voice: evet · iptal",
            text_color=NEON_GOLD,
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(side="left", padx=12)

        self._win = win

    def _resolve(self, decision: str) -> None:
        self.hide()
        self._on_decision(decision)

    def _cancel(self) -> None:
        self._resolve("iptal")
