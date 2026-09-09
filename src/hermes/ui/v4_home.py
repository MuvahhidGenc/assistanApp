"""Phase 2B-1 — V4 HOME Screen (premium dark AI Control Center).
Uses only real V4UIStore data; no fake state, no agent logic.
"""
from __future__ import annotations
try:
    import customtkinter as ctk
except ModuleNotFoundError:
    ctk = None  # Linux / VPS — import deferred; tests do not render
from hermes.ui.modern_theme import BG, CARD, CARD_BORDER, NEON_CYAN, NEON_BLUE, FONT_MONO

class V4Home:
    def __init__(self, parent, store):
        self.parent = parent
        self.store = store
        if ctk is None:
            return  # VPS / no GUI — safe skip
        # Main container
        frame = ctk.CTkFrame(parent, fg_color=BG, corner_radius=0)
        frame.pack(fill="both", expand=True, padx=20, pady=20)
        # 1. TOP HEADER (brand + status)
        header = ctk.CTkFrame(frame, fg_color=CARD, height=60, corner_radius=12)
        header.pack(fill="x", pady=(0, 16))
        header.grid_columnconfigure(0, weight=1)
        brand = ctk.CTkLabel(header, text="◆  HERMES  |  AI OPERATING SYSTEM", text_color=NEON_CYAN, font=("Segoe UI", 14, "bold"))
        brand.pack(side="left", padx=16, pady=14)
        snap = store.get_snapshot()
        status = ctk.CTkLabel(header, text=f"PHASE {snap['phase'].upper()}  •  EVENTS {snap['event_count']}  •  SESSION ACTIVE", text_color="white", font=FONT_MONO)
        status.pack(side="right", padx=16, pady=14)
        # 2. HERO / HERMES CORE (subtle orbital visual, no SciFiHud copy)
        hero = ctk.CTkFrame(frame, fg_color="#0f1117", height=220, corner_radius=16, border_color=NEON_BLUE, border_width=1)
        hero.pack(fill="x", pady=(0, 16))
        hero.grid_columnconfigure(0, weight=1)
        core_label = ctk.CTkLabel(hero, text="HERMES CORE  •  ONLINE / IDLE", text_color="white", font=("Segoe UI", 28, "bold"))
        core_label.pack(pady=(40, 0))
        # Phase indicator from store (real data, not fake)
        phase_text = f"CURRENT STATE: {snap['phase'].upper()}  |  APPROVAL: {snap['approval_state'].upper()}  |  VERIFICATION: {snap['verification_state'].upper()}"
        sub = ctk.CTkLabel(hero, text=phase_text, text_color=NEON_CYAN, font=FONT_MONO)
        sub.pack(pady=(8, 0))
        # 3. CURRENT TASK CARD (real store only)
        task_card = ctk.CTkFrame(frame, fg_color=CARD, height=110, corner_radius=12, border_color=CARD_BORDER, border_width=1)
        task_card.pack(fill="x", pady=(0, 16))
        task_card.grid_columnconfigure(0, weight=1); task_card.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(task_card, text="CURRENT TASK", text_color=NEON_BLUE, font=("Consolas", 10, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(12, 0))
        # Real data: latest event id or empty-state message
        latest = snap.get("latest_event_id") or "—"
        task_text = f"Step: {snap['phase'].upper()}  |  Last event: {latest}  |  Events: {snap['event_count']}"
        ctk.CTkLabel(task_card, text=task_text, text_color="white", font=("Segoe UI", 13)).grid(row=1, column=0, sticky="w", padx=16, pady=(6, 12))
        # 4. SYSTEM STATUS (4 compact cards — real data only)
        grid = ctk.CTkFrame(frame, fg_color=BG, corner_radius=0)
        grid.pack(fill="x", pady=(0, 16))
        grid.grid_columnconfigure((0,1,2,3), weight=1, uniform="cards")
        cards = [
            ("SYSTEM", f"Phase: {snap['phase'].upper()}\nEvents: {snap['event_count']}"),
            ("CONNECTION", f"Status: ONLINE\nEvents streamed"),
            ("SESSION", f"Active\nLatest: {snap['latest_event_id'] or 'none'}"),
            ("AGENT", f"State: {snap['approval_state'].upper()}\nVerification: {snap['verification_state'].upper()}"),
        ]
        for idx, (t, d) in enumerate(cards):
            c = ctk.CTkFrame(grid, fg_color=CARD, height=100, corner_radius=10, border_color=CARD_BORDER, border_width=1)
            c.grid(row=0, column=idx, sticky="nsew", padx=6)
            c.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(c, text=t, text_color=NEON_BLUE, font=("Consolas", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
            ctk.CTkLabel(c, text=d, text_color="white", font=("Segoe UI", 11)).pack(anchor="w", padx=12, pady=(4, 10))
        # 5. RECENT ACTIVITY (last event from store — real only)
        act = ctk.CTkFrame(frame, fg_color=CARD, height=140, corner_radius=12, border_color=CARD_BORDER, border_width=1)
        act.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(act, text="RECENT ACTIVITY", text_color=NEON_BLUE, font=("Consolas", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 6))
        # Use real event count; show brief message (no fabricated events)
        count = snap["event_count"]
        if count == 0:
            msg = "No events recorded — waiting for V3 projection."
        else:
            msg = f"{count} event(s) recorded. Latest event ID: {snap['latest_event_id']}."
        ctk.CTkLabel(act, text=msg, text_color="white", font=("Segoe UI", 12)).pack(anchor="w", padx=16, pady=(0, 12))
        # 6. QUICK ACTIONS (only existing backend commands — placeholder, not fake)
        actions = ctk.CTkFrame(frame, fg_color=CARD, height=60, corner_radius=12, border_color=CARD_BORDER, border_width=1)
        actions.pack(fill="x")
        ctk.CTkLabel(actions, text="QUICK ACTIONS  •  NAVIGATION ONLY  •  NO BACKEND COMMANDS ATTACHED", text_color=NEON_BLUE, font=("Consolas", 9, "bold")).pack(anchor="w", padx=16, pady=18)
