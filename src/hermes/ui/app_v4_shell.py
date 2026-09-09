"""Phase 2A — V4 App Shell and Navigation (CustomTkinter, minimal).
Only shell; no full screen content yet.
"""
from __future__ import annotations
try:
    import customtkinter as ctk
except ModuleNotFoundError:  # Linux / no GUI env — shell import deferred to runtime
    ctk = None  # type: ignore
from hermes.ui.modern_theme import apply_appearance, CARD, BG, NEON_CYAN, NEON_BLUE, CARD_BORDER, FONT_MONO
from hermes.ui.v4_store import V4UIStore

NAV_ITEMS = ["HOME","CHAT","AGENT","MEMORY","SKILLS","COMPUTER","BROWSER","TASKS","ACTIVITY","SETTINGS"]

class V4AppShell(ctk.CTk):
    def __init__(self, store: V4UIStore | None = None, **kwargs):
        super().__init__(**kwargs)
        apply_appearance()
        self.store = store or V4UIStore()
        self.title("Hermes V4")
        self.geometry("1200x800")
        self.configure(fg_color=BG)
        self._nav_active = "HOME"
        self._build_ui()

    def _build_ui(self):
        # Header
        self.header = ctk.CTkFrame(self, fg_color=CARD, height=60, corner_radius=0, border_width=0, border_color=CARD_BORDER)
        self.header.pack(fill="x", side="top")
        status = ctk.CTkLabel(self.header, text=f"◈ HERMES  |  Phase {self.store.phase.upper()}  |  Events: {self.store.get_snapshot()['event_count']}", text_color=NEON_CYAN, font=FONT_MONO)
        status.pack(side="left", padx=20, pady=14)
        # Main container: nav + content
        main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        main.pack(fill="both", expand=True)
        # Nav sidebar
        nav = ctk.CTkFrame(main, fg_color=CARD, width=200, border_color=CARD_BORDER, border_width=1)
        nav.pack(side="left", fill="y", padx=0, pady=0)
        nav.pack_propagate(False)
        ctk.CTkLabel(nav, text="NAVIGATION", text_color=NEON_BLUE, font=("Consolas", 10, "bold")).pack(anchor="w", padx=16, pady=(16, 6))
        for item in NAV_ITEMS:
            btn = ctk.CTkButton(nav, text=item, fg_color="transparent", hover_color=NEON_BLUE, text_color=NEON_CYAN,
                                font=FONT_MONO, height=30, anchor="w",
                                command=lambda i=item: self._set_nav(i))
            btn.pack(fill="x", padx=12, pady=2)
        # Content stub
        self.content = ctk.CTkFrame(main, fg_color=BG)
        self.content.pack(side="left", fill="both", expand=True)
        self.content_label = ctk.CTkLabel(self.content, text="V4 SHELL  •  Select a screen", text_color=NEON_CYAN, font=("Segoe UI", 24, "bold"))
        self.content_label.pack(expand=True)

    def _set_nav(self, item: str):
        self._nav_active = item
        self.content_label.configure(text=f"V4 SHELL  •  {item}")
        # Minimal: reflect in store (read-only observation only)
        snap = self.store.get_snapshot()

if __name__ == "__main__":
    from hermes.ui.v4_store import V4UIStore
    store = V4UIStore()
    app = V4AppShell(store=store)
    app.mainloop()
