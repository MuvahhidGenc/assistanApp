"""Phase 2B-1 — V4 HOME (Premium AI Operating System, real store only).

Visual anchors:
  - Hermes Core: nucleus + 3 orbital rings + depth layering on Canvas
  - State-based animation velocity + palette shifts (no fake metrics)
  - Current Mission empty-state / real-state section
  - Status Cluster + Recent Activity balanced lower section

No backend mutation. No fabricated data. UI thread only.
"""
from __future__ import annotations
import math

try:
    import customtkinter as ctk
except ModuleNotFoundError:
    ctk = None

from hermes.ui.modern_theme import BG, BG_DEEP, CARD, CARD_BORDER, NEON_CYAN, NEON_BLUE, HOLO_GREEN, NEON_GOLD, NEON_MAGENTA, TEXT, TEXT_BRIGHT, MUTED


_PHASE_META = {
    "idle":          ("ONLINE / IDLE",       "Ready for command",                    "idle"),
    "executing":     ("EXECUTING",           "Active task in progress",              "executing"),
    "completed":     ("COMPLETED",           "Task finished successfully",           "completed"),
    "failed":        ("FAULT",               "Execution fault — see activity",       "error"),
    "thinking":      ("REASONING",           "Processing intent and planning",       "thinking"),
    "observing":     ("OBSERVING",           "Monitoring environment state",         "observing"),
    "awaiting_user": ("AWAITING APPROVAL",   "User action required — awaiting you",  "awaiting"),
}


_STATE_PALETTES = {
    "idle": {
        "nucleus": "#4db8d6", "nucleus_core": "#e8fbff",
        "ring1": "#2a5b74", "ring2": "#1a3f52", "ring3": "#102835",
        "glow": "#0b2430", "scan": "",
        "tick_ms": 120,
    },
    "thinking": {
        "nucleus": "#5bc0de", "nucleus_core": "#f2fdff",
        "ring1": "#367aa3", "ring2": "#22557a", "ring3": "#14334a",
        "glow": "#0d2a3c", "scan": "",
        "tick_ms": 70,
    },
    "executing": {
        "nucleus": "#7ad6f5", "nucleus_core": "#ffffff",
        "ring1": "#4aa5d1", "ring2": "#2d6e95", "ring3": "#194360",
        "glow": "#0f3145", "scan": "",
        "tick_ms": 45,
    },
    "observing": {
        "nucleus": "#8fe0c7", "nucleus_core": "#eafff8",
        "ring1": "#3f9e86", "ring2": "#27725e", "ring3": "#154538",
        "glow": "#0b2e25", "scan": HOLO_GREEN,
        "tick_ms": 90,
    },
    "awaiting": {
        "nucleus": "#e6c76a", "nucleus_core": "#fff9e6",
        "ring1": "#c99c3a", "ring2": "#8c6a21", "ring3": "#513d12",
        "glow": "#30250a", "scan": "",
        "tick_ms": 60,
    },
    "completed": {
        "nucleus": "#8fdfb0", "nucleus_core": "#f3fff7",
        "ring1": "#49a775", "ring2": "#2e7850", "ring3": "#173f29",
        "glow": "#0c2c1b", "scan": "",
        "tick_ms": 180,
    },
    "error": {
        "nucleus": "#d4717a", "nucleus_core": "#fff3f3",
        "ring1": "#aa3d4a", "ring2": "#74252f", "ring3": "#3f1218",
        "glow": "#2c0b10", "scan": "",
        "tick_ms": 55,
    },
}


_EVENT_LABEL = {
    "action_started":       "Action Started",
    "action_finished":      "Action Finished",
    "observation_recorded": "Observation Recorded",
    "verification_recorded":"Verification Recorded",
    "recovery_started":     "Recovery Started",
    "recovery_finished":    "Recovery Finished",
    "task_cancelled":       "Task Cancelled",
}


class V4Home:
    """Premium AI Operating System HOME projection — read-only store consumer."""

    CORE_SIZE = 300
    CANVAS_PAD = 30
    CANVAS_TOTAL = CORE_SIZE + CANVAS_PAD * 2

    def __init__(self, parent, store) -> None:
        self.parent = parent
        self.store = store
        self._snap = store.get_snapshot() if store is not None else {}

        if ctk is None:
            return

        self._timer: str | None = None
        self._tick: int = 0
        self._current_state_key: str = self._derive_state_key()

        self._build_layout()
        self._bind_store_subscriber()
        self._schedule_tick()

    # ── state derivation from real snapshot ──────────────────────────

    def _derive_state_key(self) -> str:
        snap = self._snap
        if snap.get("error_state") or snap.get("phase") == "failed":
            return "error"
        approval = snap.get("approval_state", "none")
        if approval == "required":
            return "awaiting"
        return _PHASE_META.get(snap.get("phase", "idle"), ("", "", "idle"))[2]

    # ── layout build ─────────────────────────────────────────────────

    def _build_layout(self) -> None:
        root = ctk.CTkFrame(self.parent, fg_color=BG_DEEP, corner_radius=0)
        root.pack(fill="both", expand=True, padx=0, pady=0)

        inner = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
        inner.pack(fill="both", expand=True, padx=28, pady=20)

        self._build_core_section(inner)
        self._build_mission_section(inner)
        self._build_lower_section(inner)

    def _build_core_section(self, root) -> None:
        section = ctk.CTkFrame(root, fg_color=CARD, corner_radius=18,
                                border_width=1, border_color=CARD_BORDER)
        section.pack(fill="x", padx=0, pady=(0, 18))
        section.grid_columnconfigure(0, weight=0)
        section.grid_columnconfigure(1, weight=1)
        section.grid_rowconfigure(0, weight=1)

        # LEFT: graphical core (isolated canvas, no text overlay)
        canvas_frame = ctk.CTkFrame(section, fg_color=CARD, corner_radius=0, width=self.CANVAS_TOTAL + 20)
        canvas_frame.grid(row=0, column=0, sticky="nsw", padx=(30, 20), pady=26)
        canvas_frame.pack_propagate(False)
        canvas_frame.configure(width=self.CANVAS_TOTAL + 20, height=self.CANVAS_TOTAL)

        self.canvas = ctk.CTkCanvas(
            canvas_frame, width=self.CANVAS_TOTAL, height=self.CANVAS_TOTAL,
            bg=CARD, highlightthickness=0, bd=0,
        )
        self.canvas.pack(anchor="center", pady=0)

        # RIGHT: status text stack (separate layer from graphics)
        text_layer = ctk.CTkFrame(section, fg_color=CARD, corner_radius=0)
        text_layer.grid(row=0, column=1, sticky="nsew", padx=(0, 36), pady=44)
        text_layer.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(text_layer, text="HERMES",
                     text_color=TEXT_BRIGHT,
                     font=("Segoe UI", 42, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 2))

        self.label_phase = ctk.CTkLabel(text_layer, text="",
                                        text_color=NEON_CYAN,
                                        font=("Segoe UI Semibold", 15),
                                        anchor="w")
        self.label_phase.grid(row=1, column=0, sticky="w", pady=(0, 6))

        self.label_sub = ctk.CTkLabel(text_layer, text="",
                                      text_color=MUTED,
                                      font=("Segoe UI", 12),
                                      anchor="w")
        self.label_sub.grid(row=2, column=0, sticky="w", pady=(0, 22))

        # Small metadata row: approval / verification / recovery
        meta_row = ctk.CTkFrame(text_layer, fg_color=CARD, corner_radius=0)
        meta_row.grid(row=3, column=0, sticky="w", pady=(0, 0))
        meta_row.grid_columnconfigure((0, 1, 2), weight=0)

        self.meta_appr = self._meta_pill(meta_row, "APPROVAL", 0)
        self.meta_ver  = self._meta_pill(meta_row, "VERIFY",   1)
        self.meta_rec  = self._meta_pill(meta_row, "RECOVERY", 2)

        # Secondary info: events / latest id
        info_row = ctk.CTkFrame(text_layer, fg_color=CARD, corner_radius=0)
        info_row.grid(row=4, column=0, sticky="w", pady=(20, 0))
        self.label_events = ctk.CTkLabel(info_row, text="",
                                          text_color="#7f8ea5",
                                          font=("Segoe UI", 10),
                                          anchor="w")
        self.label_events.pack(anchor="w")

        self._refresh_static_text()
        self._draw_core_frame()

    def _meta_pill(self, parent, title: str, col: int):
        frame = ctk.CTkFrame(parent, fg_color="#0a1522", corner_radius=10,
                             border_width=1, border_color="#14283a")
        frame.grid(row=0, column=col, padx=(0, 12 if col != 2 else 0), pady=0, ipadx=12, ipady=6)
        title_lbl = ctk.CTkLabel(frame, text=title,
                                 text_color="#7f98b4",
                                 font=("Consolas", 9, "bold"),
                                 anchor="w")
        title_lbl.pack(anchor="w", padx=12, pady=(8, 0))
        value_lbl = ctk.CTkLabel(frame, text="—",
                                 text_color=TEXT,
                                 font=("Segoe UI Semibold", 11),
                                 anchor="w")
        value_lbl.pack(anchor="w", padx=12, pady=(1, 8))
        return value_lbl

    def _build_mission_section(self, root) -> None:
        section = ctk.CTkFrame(root, fg_color=CARD, corner_radius=14,
                                border_width=1, border_color=CARD_BORDER)
        section.pack(fill="x", padx=0, pady=(0, 18))
        section.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(section, fg_color=CARD, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(16, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="CURRENT MISSION",
                     text_color=NEON_CYAN,
                     font=("Consolas", 11, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w")

        self.mission_body_container = ctk.CTkFrame(section, fg_color=CARD, corner_radius=0)
        self.mission_body_container.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 18))
        self.mission_body_container.grid_columnconfigure(0, weight=1)

        self._render_mission_body()

    def _render_mission_body(self) -> None:
        for w in self.mission_body_container.winfo_children():
            w.destroy()

        snap = self._snap
        count = snap.get("event_count", 0)
        phase = snap.get("phase", "idle")

        if count == 0:
            ctk.CTkLabel(self.mission_body_container, text="Hermes is ready",
                         text_color=TEXT_BRIGHT,
                         font=("Segoe UI", 18, "bold"),
                         anchor="w").grid(row=0, column=0, sticky="w", pady=(10, 2))
            ctk.CTkLabel(self.mission_body_container, text="Awaiting your command",
                         text_color=MUTED,
                         font=("Segoe UI", 12),
                         anchor="w").grid(row=1, column=0, sticky="w", pady=(0, 10))
            return

        phase_title, phase_sub, _ = _PHASE_META.get(phase, (phase.upper(), "Active", "idle"))
        latest = snap.get("latest_event_id") or "—"
        first_line = f"Phase  ·  {phase_title}"
        second_line = f"Events  ·  {count}   ·   Last event  ·  {latest}"

        ctk.CTkLabel(self.mission_body_container, text=first_line,
                     text_color=TEXT_BRIGHT,
                     font=("Segoe UI Semibold", 14),
                     anchor="w").grid(row=0, column=0, sticky="w", pady=(8, 2))
        ctk.CTkLabel(self.mission_body_container, text=second_line,
                     text_color=MUTED,
                     font=("Segoe UI", 11),
                     anchor="w").grid(row=1, column=0, sticky="w", pady=(0, 8))

    def _build_lower_section(self, root) -> None:
        grid = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
        grid.pack(fill="both", expand=True, padx=0, pady=(0, 0))
        grid.grid_columnconfigure(0, weight=5)
        grid.grid_columnconfigure(1, weight=6)
        grid.grid_rowconfigure(0, weight=1)

        self._build_status_cluster(grid, 0)
        self._build_recent_activity(grid, 1)

    def _build_status_cluster(self, parent, col: int) -> None:
        wrap = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color=CARD_BORDER)
        wrap.grid(row=0, column=col, sticky="nsew", padx=(0, 10), pady=0)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(wrap, text="SYSTEM STATUS",
                     text_color=NEON_CYAN,
                     font=("Consolas", 11, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w",
                                       padx=20, pady=(16, 10))

        body = ctk.CTkFrame(wrap, fg_color=CARD, corner_radius=0)
        body.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 16))
        body.grid_columnconfigure(0, weight=0, uniform="l")
        body.grid_columnconfigure(1, weight=1, uniform="l")

        self.status_dot: dict[str, ctk.CTkLabel] = {}
        self.status_val: dict[str, ctk.CTkLabel] = {}

        rows = [
            ("SYSTEM",     ("SYSTEM",    "#0a1522")),
            ("CONNECTION", ("CONNECTION","#0a1522")),
            ("SESSION",    ("SESSION",   "#0a1522")),
            ("AGENT",      ("AGENT",     "#0a1522")),
        ]

        for i, (key, (title, _)) in enumerate(rows):
            item = ctk.CTkFrame(body, fg_color="#0a1522", corner_radius=12,
                                border_width=1, border_color="#12253a")
            item.grid(row=i, column=0, columnspan=2, sticky="ew",
                      pady=(0 if i == 0 else 8), ipady=10, ipadx=14)
            item.grid_columnconfigure(0, weight=0)
            item.grid_columnconfigure(1, weight=0)
            item.grid_columnconfigure(2, weight=1)
            item.grid_columnconfigure(3, weight=0)

            dot = ctk.CTkLabel(item, text="●", text_color=NEON_CYAN,
                               font=("Segoe UI", 14))
            dot.grid(row=0, column=0, padx=(14, 10), pady=10, sticky="w")

            ctk.CTkLabel(item, text=title,
                         text_color="#8ca1bd",
                         font=("Consolas", 10, "bold")).grid(
                row=0, column=1, padx=(0, 14), pady=10, sticky="w")

            val = ctk.CTkLabel(item, text="",
                               text_color=TEXT_BRIGHT,
                               font=("Segoe UI Semibold", 12))
            val.grid(row=0, column=2, padx=0, pady=10, sticky="w")

            self.status_dot[key] = dot
            self.status_val[key] = val

        self._refresh_status_cluster()

    def _build_recent_activity(self, parent, col: int) -> None:
        wrap = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14,
                            border_width=1, border_color=CARD_BORDER)
        wrap.grid(row=0, column=col, sticky="nsew", padx=(10, 0), pady=0)
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(wrap, text="RECENT ACTIVITY",
                     text_color=NEON_CYAN,
                     font=("Consolas", 11, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w",
                                       padx=20, pady=(16, 10))

        self.activity_container = ctk.CTkFrame(wrap, fg_color=CARD, corner_radius=0)
        self.activity_container.grid(row=1, column=0, sticky="nsew",
                                     padx=20, pady=(0, 16))
        self.activity_container.grid_columnconfigure(0, weight=1)

        self._render_recent_activity()

    def _render_recent_activity(self) -> None:
        for w in self.activity_container.winfo_children():
            w.destroy()

        snap = self._snap
        count = snap.get("event_count", 0)
        events = getattr(self.store, "_events", []) if self.store is not None else []

        if count == 0 or not events:
            ctk.CTkLabel(self.activity_container, text="No recent activity",
                         text_color=TEXT_BRIGHT,
                         font=("Segoe UI Semibold", 13),
                         anchor="w").grid(row=0, column=0, sticky="w",
                                           padx=2, pady=(12, 2))
            ctk.CTkLabel(self.activity_container, text="Events will appear here as V3 projects to store.",
                         text_color=MUTED,
                         font=("Segoe UI", 11),
                         anchor="w",
                         justify="left",
                         wraplength=460).grid(row=1, column=0, sticky="w",
                                               padx=2, pady=(0, 12))
            return

        # Render last 6 events (newest first), from real store._events
        recent = list(reversed(list(events)[-6:]))
        for i, ev in enumerate(recent):
            label = _EVENT_LABEL.get(ev.kind, ev.kind.replace("_", " ").title())
            ev_id_short = (ev.event_id[:10] + "…") if len(ev.event_id) > 10 else ev.event_id
            line = f"{label}   ·   {ev_id_short}"

            accent = NEON_CYAN
            if "finished" in ev.kind:
                accent = HOLO_GREEN
            elif "failed" in ev.kind:
                accent = NEON_MAGENTA
            elif "recovery" in ev.kind:
                accent = NEON_GOLD
            elif "verification" in ev.kind:
                accent = NEON_BLUE

            row = ctk.CTkFrame(self.activity_container, fg_color="transparent",
                               corner_radius=0)
            row.grid(row=i, column=0, sticky="ew", pady=(0 if i == 0 else 8), ipadx=0)
            row.grid_columnconfigure(0, weight=0)
            row.grid_columnconfigure(1, weight=1)

            dot = ctk.CTkLabel(row, text="●", text_color=accent,
                               font=("Segoe UI", 10))
            dot.grid(row=0, column=0, padx=(2, 10), sticky="w")

            ctk.CTkLabel(row, text=line, text_color=TEXT,
                         font=("Segoe UI", 11), anchor="w").grid(
                row=0, column=1, sticky="w")

    # ── store subscriber ─────────────────────────────────────────────

    def _bind_store_subscriber(self) -> None:
        def _on_store_event(_evt: str, _payload) -> None:
            try:
                self._snap = self.store.get_snapshot() if self.store is not None else {}
                new_key = self._derive_state_key()
                state_changed = (new_key != self._current_state_key)
                self._current_state_key = new_key
                self._refresh_static_text()
                try:
                    self._render_mission_body()
                    self._render_recent_activity()
                    self._refresh_status_cluster()
                except Exception:
                    pass
                if state_changed:
                    # restart tick loop to pick up new palette speed
                    self._cancel_tick()
                    self._tick = 0
                    self._schedule_tick()
            except Exception:
                pass

        try:
            self.store.subscribe(_on_store_event)
        except Exception:
            pass

    # ── text / label refresh from snapshot ───────────────────────────

    def _refresh_static_text(self) -> None:
        snap = self._snap
        phase = snap.get("phase", "idle")
        phase_title, phase_sub, _ = _PHASE_META.get(phase, (phase.upper(), "Active", "idle"))

        try:
            self.label_phase.configure(text=phase_title)
            self.label_sub.configure(text=phase_sub)
        except Exception:
            pass

        approval = snap.get("approval_state", "none")
        verify   = snap.get("verification_state", "pending")
        recovery = snap.get("recovery_state", "none")

        appr_txt = {"none": "IDLE", "required": "REQUIRED",
                    "resolved": "RESOLVED", "timeout": "TIMEOUT"}.get(approval, approval.upper())
        ver_txt  = {"pending": "PENDING", "unknown": "UNKNOWN",
                    "verified": "VERIFIED"}.get(verify, verify.upper())
        rec_txt  = {"none": "IDLE", "recovery": "ACTIVE"}.get(recovery, recovery.upper())

        try:
            self.meta_appr.configure(text=appr_txt)
            self.meta_ver.configure(text=ver_txt)
            self.meta_rec.configure(text=rec_txt)
        except Exception:
            pass

        count = snap.get("event_count", 0)
        latest = snap.get("latest_event_id") or "none"
        try:
            self.label_events.configure(text=f"Event count  ·  {count}    Latest ID  ·  {latest}")
        except Exception:
            pass

    def _refresh_status_cluster(self) -> None:
        snap = self._snap
        phase = snap.get("phase", "idle")
        approval = snap.get("approval_state", "none")
        count = snap.get("event_count", 0)
        verify = snap.get("verification_state", "pending")

        # SYSTEM
        system_val = {"idle": "ONLINE / IDLE",
                      "thinking": "REASONING",
                      "executing": "EXECUTING",
                      "completed": "COMPLETED",
                      "failed": "FAULT",
                      "observing": "OBSERVING",
                      "awaiting_user": "AWAITING USER"}.get(phase, phase.upper())
        system_dot = (NEON_MAGENTA if phase == "failed" else
                      NEON_GOLD    if phase in ("awaiting_user",) or approval == "required" else
                      HOLO_GREEN   if phase == "completed" else
                      NEON_CYAN)

        # CONNECTION (store is projection consumer -> STREAMING when any event)
        conn_val = "STREAMING" if count > 0 else "ONLINE"
        conn_dot = NEON_CYAN if count > 0 else "#7f8ea5"

        # SESSION
        sess_val = f"ACTIVE  ·  {count}"
        sess_dot = HOLO_GREEN if count > 0 else "#7f8ea5"

        # AGENT
        agent_val = {"pending": "STANDBY",
                     "verified": "VERIFIED",
                     "unknown": "WAITING DATA"}.get(verify, verify.upper())
        agent_dot = HOLO_GREEN if verify == "verified" else NEON_CYAN if phase != "idle" else "#7f8ea5"

        mapping = [
            ("SYSTEM",     system_val, system_dot),
            ("CONNECTION", conn_val,   conn_dot),
            ("SESSION",    sess_val,   sess_dot),
            ("AGENT",      agent_val,  agent_dot),
        ]

        try:
            for key, value, dot_color in mapping:
                self.status_val[key].configure(text=value)
                self.status_dot[key].configure(text_color=dot_color)
        except Exception:
            pass

    # ── canvas drawing ───────────────────────────────────────────────

    def _draw_core_frame(self) -> None:
        try:
            c = self.canvas
        except AttributeError:
            return

        c.delete("all")
        pal = _STATE_PALETTES.get(self._current_state_key, _STATE_PALETTES["idle"])

        cx = self.CANVAS_TOTAL // 2
        cy = self.CANVAS_TOTAL // 2

        # glow layers (outer -> inner) — for depth without transparency
        for i, radius in enumerate((155, 140, 125, 112)):
            shrink = i * 2
            col = pal["glow"] if i < 3 else pal["ring3"]
            c.create_oval(cx - radius, cy - radius,
                          cx + radius, cy + radius,
                          outline=col, width=max(1, 4 - shrink // 2))

        t = self._tick
        speed = 1.0

        r1, r2, r3 = 108, 82, 58
        w1, w2, w3 = 2, 2, 2

        # Ring 1 — outermost, slowest
        self._draw_ring(cx, cy, r1,
                        angle=(t * 0.4 * speed) % 360.0,
                        coverage_deg=180.0,
                        color=pal["ring1"], width=w1)
        # Ring 2 — middle, reverse
        self._draw_ring(cx, cy, r2,
                        angle=(360.0 - t * 0.75 * speed) % 360.0,
                        coverage_deg=220.0,
                        color=pal["ring2"], width=w2)
        # Ring 3 — innermost, fastest
        self._draw_ring(cx, cy, r3,
                        angle=(t * 1.35 * speed) % 360.0,
                        coverage_deg=260.0,
                        color=pal["ring3"], width=w3)

        # 4 orbital nodes along ring 1/2/3
        self._draw_node(cx, cy, r1, (t * 0.4) % 360.0, pal["ring1"], 6)
        self._draw_node(cx, cy, r2, (360.0 - t * 0.75) % 360.0, pal["ring2"], 5)
        self._draw_node(cx, cy, r3, (t * 1.35) % 360.0, pal["ring3"], 4)
        self._draw_node(cx, cy, r1, ((t * 0.4) + 180.0) % 360.0, pal["ring2"], 5)

        # Nucleus — layered rings for depth
        r_core = 30
        c.create_oval(cx - r_core - 6, cy - r_core - 6,
                      cx + r_core + 6, cy + r_core + 6,
                      outline=pal["ring2"], width=1)
        c.create_oval(cx - r_core, cy - r_core,
                      cx + r_core, cy + r_core,
                      outline=pal["nucleus"], width=2)
        c.create_oval(cx - r_core + 8, cy - r_core + 8,
                      cx + r_core - 8, cy + r_core - 8,
                      fill=pal["nucleus"], outline="")
        # hot core (smallest dot)
        cc = 10
        c.create_oval(cx - cc, cy - cc, cx + cc, cy + cc,
                      fill=pal["nucleus_core"], outline="")

        # observing scan pulse: expanding thin arc
        if pal.get("scan"):
            pulse = (t % 40) / 40.0
            pr = int(r1 + 6 + pulse * 36)
            alpha_col = pal["scan"]
            c.create_oval(cx - pr, cy - pr, cx + pr, cy + pr,
                          outline=alpha_col, width=1)

        # approval / awaiting state: subtle pulse border around canvas
        if self._current_state_key == "awaiting":
            pad = 4 + (int(3 * math.sin(t * 0.4)) % 4)
            c.create_rectangle(pad, pad,
                               self.CANVAS_TOTAL - pad, self.CANVAS_TOTAL - pad,
                               outline=pal["nucleus"], width=1)
        elif self._current_state_key == "error":
            pad = 6
            c.create_rectangle(pad, pad,
                               self.CANVAS_TOTAL - pad, self.CANVAS_TOTAL - pad,
                               outline=pal["nucleus"], width=1)
        elif self._current_state_key == "completed":
            # completion checkmark-ish accent in corners
            pad = 10
            c.create_oval(pad, pad, pad + 6, pad + 6,
                          fill=pal["nucleus"], outline="")
            c.create_oval(self.CANVAS_TOTAL - pad - 6, self.CANVAS_TOTAL - pad - 6,
                          self.CANVAS_TOTAL - pad, self.CANVAS_TOTAL - pad,
                          fill=pal["nucleus"], outline="")

    def _draw_ring(self, cx: int, cy: int, radius: int, angle: float,
                   coverage_deg: float, color: str, width: int) -> None:
        start = angle
        # Tk arc angles: 0 = 3 o'clock, 90 = 12 o'clock, counter-clockwise is positive?
        # Actually: create_arc start degrees, 0=east, angles increase counter-clockwise.
        # We convert so 0 = north for natural orbit.
        tk_start = 90.0 - start
        extent = -coverage_deg  # negative = clockwise sweep
        self.canvas.create_arc(
            cx - radius, cy - radius, cx + radius, cy + radius,
            start=tk_start, extent=extent,
            outline=color, width=width, style="arc",
        )

    def _draw_node(self, cx: int, cy: int, radius: int, angle_deg: float,
                   color: str, size: int) -> None:
        rad = math.radians(90.0 - angle_deg)
        x = cx + radius * math.cos(rad)
        y = cy - radius * math.sin(rad)
        self.canvas.create_oval(x - size, y - size, x + size, y + size,
                                fill=color, outline="")

    # ── animation lifecycle ──────────────────────────────────────────

    def _schedule_tick(self) -> None:
        try:
            pal = _STATE_PALETTES.get(self._current_state_key, _STATE_PALETTES["idle"])
            ms = pal.get("tick_ms", 100)
        except Exception:
            ms = 100
        try:
            self._timer = self.parent.after(ms, self._on_tick)
        except Exception:
            self._timer = None

    def _cancel_tick(self) -> None:
        if self._timer is not None:
            try:
                self.parent.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None

    def _on_tick(self) -> None:
        self._timer = None
        self._tick = (self._tick + 1) & 0xFFFFFF
        try:
            self._draw_core_frame()
        except Exception:
            pass
        self._schedule_tick()

    def destroy(self) -> None:
        self._cancel_tick()

    def __del__(self):
        try:
            self._cancel_tick()
        except Exception:
            pass
