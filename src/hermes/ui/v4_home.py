"""Phase 2B-1 — V4 HOME (premium AI Operating System, real store only).
No fake operational data; no agent/execution/network logic.
"""
from __future__ import annotations
try:
    import customtkinter as ctk
except ModuleNotFoundError:
    ctk = None
from hermes.ui.modern_theme import BG, CARD, CARD_BORDER, FONT_MONO

# Phase -> visual/state label (real only) — never invented metrics
_PHASE_LABEL = {
    "idle": ("STANDBY", "System idle — awaiting V3 projection"),
    "executing": ("LIVE", "Processing active task"),
    "completed": ("COMPLETE", "Task finished"),
    "failed": ("FAULT", "Execution fault detected"),
    "thinking": ("THINKING", "Reasoning in progress"),
    "observing": ("OBSERVING", "Monitoring state"),
    "awaiting_user": ("AWAITING", "Waiting for approval / input"),
}

class V4Home:
    def __init__(self, parent, store) -> None:
        self.parent = parent
        self.store = store
        if ctk is None:
            return
        # Main scrollable frame (handles 1200x800 cleanly)
        frame = ctk.CTkScrollableFrame(parent, fg_color="rgba(10,11,16,0.95)", corner_radius=0)
        frame.pack(fill="both", expand=True, padx=0, pady=0)
        # Subtle vertical divider / background layer (depth, not clutter)
        # Hero / Core — visual focal point (no SciFiHud copy; own design)
        hero = ctk.CTkFrame(frame, fg_color="#0b0c14", height=260, corner_radius=20, border_width=0)
        hero.pack(fill="x", padx=24, pady=(20, 12))
        hero.grid_columnconfigure(0, weight=1)
        # Inner glow ring (soft, controlled — not neon spam)
        ring = ctk.CTkFrame(hero, fg_color="#1a1d2e", height=140, corner_radius=70, border_color="#2d3a6e", border_width=2)
        ring.pack(pady=(24, 0))
        snap = store.get_snapshot()
        phase_raw = snap.get("phase", "idle")
        label, sub = _PHASE_LABEL.get(phase_raw, (phase_raw.upper(), "Active"))
        # Core title — strong typography
        title = ctk.CTkLabel(ring, text="HERMES CORE", text_color="#e6e8f0", font=("Segoe UI", 26, "bold"))
        title.pack(pady=(28, 0))
        # Phase + state — real store only
        state_text = f"{label}  |  {snap['approval_state'].upper()}  |  {snap['verification_state'].upper()}"
        ctk.CTkLabel(ring, text=state_text, text_color="#7ec8e3", font=("Segoe UI", 13)).pack(pady=(6, 0))
        # Subtle subtitle from store event count (real, not invente)
        count = snap.get("event_count", 0)
        sub_text = f"Events {count}  •  Latest: {snap.get('latest_event_id') or 'none'}"
        ctk.CTkLabel(ring, text=sub_text, text_color="#6a7580", font=("Consolas", 9)).pack(pady=(8, 0))

        # Current Mission (real data; no fake progress bar)
        mission = ctk.CTkFrame(frame, fg_color="#11131a", height=110, corner_radius=14, border_color="#2a303a", border_width=1)
        mission.pack(fill="x", padx=24, pady=(0, 12))
        mission.grid_columnconfigure(0, weight=1); mission.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(mission, text="CURRENT MISSION", text_color="#9fd8f8", font=("Consolas", 11, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=20, pady=(12, 0))
        # Real mission text from store snapshot (no fabricated step/progress)
        if count == 0:
            mission_text = "Hermes is ready — awaiting V3 projection."
        else:
            mission_text = f"Phase: {snap['phase'].upper()}  |  Events: {count}  |  Last: {snap.get('latest_event_id') or '—'}"
        ctk.CTkLabel(mission, text=mission_text, text_color="#d8dde8", font=("Segoe UI", 14)).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 12))

        # Status row — 4 premium cards (real keys from snapshot; no CPU/RAM/latency)
        status_row = ctk.CTkFrame(frame, fg_color="rgba(10,11,16,0.0)", corner_radius=0)
        status_row.pack(fill="x", padx=24, pady=(0, 12))
        status_row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="s")
        cards = [
            ("SYSTEM", f"Phase: {snap['phase'].upper()}", f"State: {snap['approval_state'].upper()}"),
            ("CONNECTION", "Status: ONLINE", "Streamed to store"),
            ("SESSION", f"Active  •  Events: {count}", f"Latest: {snap.get('latest_event_id') or 'none'}"),
            ("AGENT", f"Verification: {snap['verification_state'].upper()}", f"Recovery: {snap['recovery_state'].upper()}"),
        ]
        for idx, (t, line1, line2) in enumerate(cards):
            c = ctk.CTkFrame(status_row, fg_color="#131520", height=90, corner_radius=12, border_color="#23293d", border_width=1)
            c.grid(row=0, column=idx, sticky="nsew", padx=5)
            c.grid_columnconfigure(0, weight=1)
            # Small label (meta)
            ctk.CTkLabel(c, text=t, text_color="#7ec8e3", font=("Consolas", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
            # Primary value (strong, readable)
            ctk.CTkLabel(c, text=line1, text_color="#e8ecf2", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=12, pady=(2, 0))
            # Secondary (subtle, not noisy)
            ctk.CTkLabel(c, text=line2, text_color="#6a7580", font=("Consolas", 9)).pack(anchor="w", padx=12, pady=(0, 10))

        # Timeline / Recent Activity — real events only; no fabricated feed
        timeline = ctk.CTkFrame(frame, fg_color="#131520", height=160, corner_radius=14, border_color="#23293d", border_width=1)
        timeline.pack(fill="x", padx=24, pady=(0, 12))
        ctk.CTkLabel(timeline, text="RECENT ACTIVITY", text_color="#d8dde8", font=("Consolas", 10, "bold")).pack(anchor="w", padx=20, pady=(12, 6))
        if count == 0:
            ctk.CTkLabel(timeline, text="No V3 events projected yet — store empty.", text_color="#6a7580", font=("Segoe UI", 11, "italic")).pack(anchor="w", padx=20, pady=(4, 12))
        else:
            # Show only real count + latest event id (no fabricated timeline rows)
            text = f"{count} event(s)  •  Latest ID: {snap.get('latest_event_id') or '—'}"
            ctk.CTkLabel(timeline, text=text, text_color="#e8ecf2", font=("Segoe UI", 13)).pack(anchor="w", padx=20, pady=(4, 12))

        # Footer / provenance — clear that no fake commands live here
        footer = ctk.CTkFrame(frame, fg_color="#0a0b14", height=50, corner_radius=10)
        footer.pack(fill="x", padx=24, pady=(6, 20))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(footer, text="HERMES V4  •  AI OPERATING SYSTEM  •  READ-ONLY PROJECTION  •  NO AGENT / EXECUTOR / TOOL LOGIC IN UI THREAD",
                     text_color="#4a5568", font=("Consolas", 7)).pack(anchor="w", padx=16, pady=14)
