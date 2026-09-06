from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from typing import Any

from hermes.ui.approval_dialog import ApprovalDialog
from hermes.ui.hud_stats import collect_hud_stats
from hermes.ui.modern_theme import (
    BG,
    CARD_BORDER,
    CYAN,
    DIM,
    HOLO_GREEN,
    INPUT_BG,
    MAGENTA,
    NEON_GOLD,
    ORANGE,
    TEXT,
    TEXT_BRIGHT,
    apply_appearance,
    bind_copy_shortcuts,
    card,
    ghost_button,
    neon_button,
    primary_button,
    section_label,
    selectable_log,
)
from hermes.ui.sci_fi_hud import SciFiHudRenderer
from hermes.ui.state import ActivityMode, UIState


class ChatWindow:
    """JARVIS-style HUD: holographic core, system telemetry, conversation."""

    def __init__(
        self,
        state: UIState,
        on_send: Callable[[str], None],
        on_open_settings: Callable[[], None] | None = None,
        on_approve: Callable[[str], None] | None = None,
        on_toggle_voice: Callable[[], None] | None = None,
        width: int = 1360,
        height: int = 820,
    ) -> None:
        import customtkinter as ctk

        self._ctk = ctk
        self._state = state
        self._on_send = on_send
        self._on_open_settings = on_open_settings
        self._on_approve = on_approve
        self._on_toggle_voice = on_toggle_voice
        self._tick = 0.0
        self._camera_on = False
        self._hud = SciFiHudRenderer()
        self._root = ctk.CTk()
        self._root.title("HERMES")
        self._root.geometry(f"{max(width, 1280)}x{max(height, 780)}")
        self._root.minsize(1024, 640)
        self._root.configure(fg_color=BG)
        apply_appearance()
        self._approval_dialog: ApprovalDialog | None = None
        self._build_ui()
        self._root.protocol("WM_DELETE_WINDOW", self.hide)
        self._root.after(80, self._tick_hud)

    @property
    def root(self) -> Any:
        return self._root

    def _card(self, parent: Any, **kwargs: Any) -> Any:
        return card(self._ctk, parent, **kwargs)

    def _build_ui(self) -> None:
        ctk = self._ctk
        header = self._card(self._root)
        header.pack(fill="x", padx=16, pady=(14, 8))

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(side="left", padx=(16, 8), pady=10)
        ctk.CTkLabel(
            title_row,
            text="HERMES",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=22, weight="bold"),
            text_color=CYAN,
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_row,
            text="Sesli asistan",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=DIM,
        ).pack(anchor="w")

        self._conn_dot = ctk.CTkLabel(header, text="◉", text_color=self._state.connection_color())
        self._conn_dot.pack(side="left", padx=(8, 0))
        self._conn_label = ctk.CTkLabel(
            header,
            text=self._state.connection_label(),
            text_color=HOLO_GREEN,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self._conn_label.pack(side="left", padx=(2, 16))

        self._clock_label = ctk.CTkLabel(
            header,
            text="",
            text_color=TEXT_BRIGHT,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._clock_label.pack(side="left", expand=True)

        self._status_label = ctk.CTkLabel(
            header,
            text=self._state.status_text,
            text_color=DIM,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self._status_label.pack(side="left", padx=8)

        if self._on_open_settings is not None:
            ghost_button(
                ctk,
                header,
                "SYS",
                self._on_open_settings,
                width=48,
                height=30,
                hover_color=CYAN,
                text_color=CYAN,
            ).pack(side="right", padx=(0, 14), pady=10)

        body = ctk.CTkFrame(self._root, fg_color=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        body.grid_columnconfigure(0, weight=1, minsize=220)
        body.grid_columnconfigure(1, weight=2, minsize=300)
        body.grid_columnconfigure(2, weight=3, minsize=420)
        body.grid_rowconfigure(0, weight=1)
        body.bind("<Configure>", self._on_resize)

        self._build_left(body)
        self._build_center(body)
        self._build_right(body)

        if self._on_approve is not None:
            self._approval_dialog = ApprovalDialog(self._root, self._on_approve)

    def _build_left(self, body: Any) -> None:
        ctk = self._ctk
        left = ctk.CTkFrame(body, fg_color=BG, width=250)
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 8))

        stats = self._card(left)
        stats.pack(fill="x", pady=(0, 8))
        section_label(ctk, stats, "SYSTEM TELEMETRY").pack(anchor="w", padx=14, pady=(12, 6))
        self._cpu_bar, self._cpu_label = self._meter(stats, "CPU")
        self._ram_bar, self._ram_label = self._meter(stats, "RAM")
        self._disk_bar, self._disk_label = self._meter(stats, "DISK")

        host = self._card(left)
        host.pack(fill="x", pady=(0, 8))
        section_label(ctk, host, "HOST NODE").pack(anchor="w", padx=14, pady=(12, 4))
        self._host_label = ctk.CTkLabel(
            host, text="—", text_color=TEXT, font=ctk.CTkFont(family="Consolas", size=11)
        )
        self._host_label.pack(anchor="w", padx=14, pady=(0, 12))

        cam = self._card(left)
        cam.pack(fill="x", pady=(0, 8))
        top = ctk.CTkFrame(cam, fg_color="transparent")
        top.pack(fill="x", padx=10, pady=(10, 4))
        section_label(ctk, top, "OPTICS").pack(side="left")
        ghost_button(ctk, top, "PWR", self._toggle_camera, width=40, height=24).pack(side="right")
        self._cam_label = ctk.CTkLabel(
            cam,
            text="◌ OFFLINE",
            text_color=DIM,
            height=70,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self._cam_label.pack(fill="x", padx=14, pady=(8, 12))

        up = self._card(left)
        up.pack(fill="x")
        section_label(ctk, up, "UPTIME").pack(anchor="w", padx=14, pady=(12, 4))
        self._uptime_label = ctk.CTkLabel(
            up,
            text="00:00:00",
            text_color=CYAN,
            font=ctk.CTkFont(family="Consolas", size=22, weight="bold"),
        )
        self._uptime_label.pack(anchor="w", padx=14)
        self._load_label = ctk.CTkLabel(
            up, text="LOAD: —", text_color=DIM, font=ctk.CTkFont(family="Consolas", size=10)
        )
        self._load_label.pack(anchor="w", padx=14, pady=(0, 6))
        self._load_bar = ctk.CTkProgressBar(up, progress_color=CYAN, fg_color=INPUT_BG, height=6)
        self._load_bar.pack(fill="x", padx=14, pady=(0, 14))
        self._load_bar.set(0)

    def _meter(self, parent: Any, title: str) -> tuple[Any, Any]:
        ctk = self._ctk
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=14, pady=(0, 10))
        label = ctk.CTkLabel(
            wrap,
            text=f"{title}  0%",
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        label.pack(anchor="w")
        bar = ctk.CTkProgressBar(wrap, progress_color=CYAN, fg_color=INPUT_BG, height=6)
        bar.pack(fill="x", pady=(4, 0))
        bar.set(0)
        return bar, label

    def _build_center(self, body: Any) -> None:
        ctk = self._ctk
        center = ctk.CTkFrame(body, fg_color=BG)
        center.grid(row=0, column=1, sticky="nsew", padx=4)

        self._canvas = tk.Canvas(center, bg=BG, highlightthickness=0, height=380)
        self._canvas.pack(fill="both", expand=True, pady=(12, 0))

        self._listen_label = ctk.CTkLabel(
            center,
            text="◈ STANDBY",
            text_color=HOLO_GREEN,
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
        )
        self._listen_label.pack(pady=(6, 2))
        self._activity_bar = ctk.CTkProgressBar(
            center, height=4, progress_color=CYAN, fg_color=INPUT_BG, width=240
        )
        self._activity_bar.pack(pady=(0, 12))
        self._activity_bar.set(0.35)

        actions = ctk.CTkFrame(center, fg_color="transparent")
        actions.pack(pady=(0, 18))
        ghost_button(ctk, actions, "OPTICS", self._toggle_camera, width=96, height=34).pack(
            side="left", padx=5
        )
        neon_button(
            ctk,
            actions,
            "VOICE",
            self._on_toggle_voice or (lambda: None),
            accent=HOLO_GREEN,
            width=96,
            height=34,
        ).pack(side="left", padx=5)
        ghost_button(
            ctk, actions, "INPUT", lambda: self._input.focus_set(), width=96, height=34
        ).pack(side="left", padx=5)

    def _build_right(self, body: Any) -> None:
        ctk = self._ctk
        right = self._card(body)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 4))

        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(12, 6))
        section_label(ctk, head, "Sohbet").pack(side="left")
        ghost_button(ctk, head, "Temizle", self._clear_chat, width=72).pack(side="right", padx=(6, 0))
        ghost_button(ctk, head, "Kopyala", self._copy_selection_or_all, width=72).pack(side="right")

        self._transcript = selectable_log(ctk, right)
        self._transcript.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        bind_copy_shortcuts(self._transcript, self._root)
        self._transcript.bind("<Button-3>", self._show_copy_menu)

        self._debug_label = ctk.CTkLabel(
            right,
            text="trace —",
            text_color=DIM,
            font=ctk.CTkFont(family="Consolas", size=10),
            anchor="w",
        )
        self._debug_label.pack(fill="x", padx=12, pady=(0, 8))

        self._approval_bar = ctk.CTkFrame(
            right,
            fg_color="#1a0a08",
            corner_radius=6,
            border_width=1,
            border_color=ORANGE,
        )
        self._approval_title = ctk.CTkLabel(
            self._approval_bar,
            text="◈ AUTHORIZATION REQUIRED",
            text_color=NEON_GOLD,
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            wraplength=520,
            justify="left",
        )
        self._approval_title.pack(anchor="w", padx=12, pady=(10, 4))
        self._approval_desc = ctk.CTkLabel(
            self._approval_bar,
            text="",
            text_color=TEXT,
            wraplength=520,
            justify="left",
            font=ctk.CTkFont(family="Consolas", size=11),
        )
        self._approval_desc.pack(anchor="w", padx=12, pady=(0, 8))
        approve_row = ctk.CTkFrame(self._approval_bar, fg_color="transparent")
        approve_row.pack(fill="x", padx=12, pady=(0, 10))
        neon_button(
            ctk,
            approve_row,
            "CONFIRM",
            lambda: self._on_approve and self._on_approve("evet"),
            accent=HOLO_GREEN,
            width=108,
            height=34,
        ).pack(side="left", padx=(0, 8))
        ghost_button(
            ctk,
            approve_row,
            "ABORT",
            lambda: self._on_approve and self._on_approve("iptal"),
            width=108,
            height=34,
        ).pack(side="left")
        ctk.CTkLabel(
            approve_row,
            text="voice: evet / iptal",
            text_color=NEON_GOLD,
            font=ctk.CTkFont(family="Consolas", size=10),
        ).pack(side="left", padx=10)

        box = ctk.CTkFrame(
            right,
            fg_color=INPUT_BG,
            corner_radius=6,
            border_width=1,
            border_color=CARD_BORDER,
        )
        self._input_box = box
        box.pack(fill="x", padx=10, pady=(0, 12))
        self._input = ctk.CTkEntry(
            box,
            placeholder_text="Transmit message...",
            height=38,
            border_width=0,
            fg_color=INPUT_BG,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self._input.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=8)
        self._input.bind("<Return>", self._submit)
        primary_button(ctk, box, "TX", self._submit, width=44).pack(side="right", padx=(0, 8))

    def _transcript_text(self) -> str:
        return self._transcript.get("1.0", "end-1c")

    def _copy_text(self, text: str) -> None:
        if not text:
            return
        self._root.clipboard_clear()
        self._root.clipboard_append(text)
        self._root.update()

    def _copy_selection_or_all(self) -> None:
        try:
            selected = self._transcript.get("sel.first", "sel.last")
        except Exception:
            selected = ""
        self._copy_text(selected or self._transcript_text())

    def _show_copy_menu(self, event: Any) -> None:
        menu = tk.Menu(self._root, tearoff=0, bg=INPUT_BG, fg=TEXT)
        menu.add_command(label="Kopyala", command=self._copy_selection_or_all)
        menu.add_command(label="Tumunu kopyala", command=lambda: self._copy_text(self._transcript_text()))
        menu.add_command(label="Tumunu sec", command=lambda: self._transcript.tag_add("sel", "1.0", "end-1c"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _toggle_camera(self) -> None:
        self._camera_on = not self._camera_on
        self._cam_label.configure(
            text="◉ ONLINE" if self._camera_on else "◌ OFFLINE",
            text_color=HOLO_GREEN if self._camera_on else DIM,
        )

    def _clear_chat(self) -> None:
        self._transcript.delete("1.0", "end")

    def _activity_palette(self) -> tuple[str, str, float]:
        mode = self._state.activity
        palettes = {
            ActivityMode.IDLE: (CYAN, "#0077b6", 0.35),
            ActivityMode.LISTENING: (HOLO_GREEN, "#00cc66", 0.85),
            ActivityMode.THINKING: (NEON_GOLD, ORANGE, 0.65),
            ActivityMode.SPEAKING: (MAGENTA, "#c9184a", 0.75),
            ActivityMode.EXECUTING: ("#0077b6", CYAN, 0.7),
            ActivityMode.AWAITING_APPROVAL: (ORANGE, NEON_GOLD, 0.55),
        }
        return palettes.get(mode, (CYAN, "#0077b6", 0.35))

    def _tick_hud(self) -> None:
        if not self._root.winfo_exists():
            return
        stats = collect_hud_stats()
        self._clock_label.configure(text=stats["clock"])
        self._cpu_bar.set(stats["cpu"] / 100)
        self._cpu_label.configure(text=f"CPU  {stats['cpu']:.0f}%")
        self._ram_bar.set(stats["ram_pct"] / 100)
        self._ram_label.configure(text=f"RAM  {stats['ram_used']:.0f}GB / {stats['ram_pct']:.0f}%")
        self._disk_bar.set(stats["disk_pct"] / 100)
        self._disk_label.configure(
            text=f"DISK  {stats['disk_used']:.0f}/{stats['disk_total']:.0f} GB"
        )
        self._host_label.configure(text=stats["host"])
        self._uptime_label.configure(text=stats["uptime"])
        self._load_bar.set(stats["load"] / 100)
        self._load_label.configure(text=f"LOAD: {stats['load_label']}  {stats['load']:.0f}%")
        level = self._state.audio_level
        if self._state.activity.value in ("listening", "speaking"):
            import random

            level = min(1.0, level + random.uniform(0.08, 0.28))
            self._state.set_audio_level(level)
        else:
            self._state.set_audio_level(level * 0.82)
        self._hud.advance(0.12)
        self._hud.spawn_particles(level)
        self._hud.draw(
            self._canvas,
            activity=self._state.activity,
            audio_level=self._state.audio_level,
            bg=BG,
        )
        self.refresh_state()
        self._tick += 0.18
        self._root.after(80, self._tick_hud)

    def _on_resize(self, _event: object = None) -> None:
        wrap = max(320, int(self._root.winfo_width() * 0.28))
        if hasattr(self, "_approval_title"):
            self._approval_title.configure(wraplength=wrap)
            self._approval_desc.configure(wraplength=wrap)

    def show_approval(self, title: str, description: str) -> None:
        self.show()
        self.append_message("status", f"{title}\n{description}\nSesle veya butonla: evet / iptal")
        self._approval_title.configure(text=f"◈ {title.upper()}" if title else "◈ AUTHORIZATION REQUIRED")
        self._approval_desc.configure(text=description or "Bu islem icin onay gerekiyor.")
        self._approval_bar.pack(fill="x", padx=10, pady=(0, 8), before=self._input_box)
        if self._approval_dialog is not None:
            self._approval_dialog.show(title, description)

    def hide_approval(self) -> None:
        if hasattr(self, "_approval_bar"):
            self._approval_bar.pack_forget()
        if self._approval_dialog is not None:
            self._approval_dialog.hide()

    def _submit(self, _event: object = None) -> None:
        text = self._input.get().strip()
        if not text:
            return
        self._input.delete(0, "end")
        self.append_message("user", text)
        self._on_send(text)

    def append_message(self, role: str, text: str, *, via: str = "") -> None:
        if role == "status":
            # Technical status never enters the conversation pane.
            return
        who = {"user": "Sen", "system": "Sistem"}.get(role, "Hermes")
        if role == "user" and via == "voice":
            who = "Sen (mikrofon)"
        self._transcript.insert("end", f"{who}\n{text}\n\n")
        self._transcript.see("end")

    def refresh_state(self) -> None:
        self._conn_dot.configure(text_color=self._state.connection_color())
        online = self._state.connection.value == "connected"
        self._conn_label.configure(
            text="Bağlı" if online else self._state.connection_label(),
            text_color=HOLO_GREEN if online else self._state.connection_color(),
        )
        self._status_label.configure(text=self._state.status_text)
        label = self._state.activity_label()
        color = self._state.activity_color()
        self._listen_label.configure(text=f"◈ {label}", text_color=color)
        _, _, energy = self._activity_palette()
        self._activity_bar.configure(progress_color=color)
        self._activity_bar.set(max(0.12, min(1.0, 0.25 + energy + 0.15 * math.sin(self._tick))))
        trace = getattr(self._state, "last_turn_trace", None) or {}
        if isinstance(trace, dict) and trace.get("trace_id") and hasattr(self, "_debug_label"):
            total = trace.get("total_ms", "?")
            path = trace.get("path") or "-"
            self._debug_label.configure(
                text=f"trace {trace.get('trace_id')} · {path} · {total} ms"
            )
        self._root.configure(fg_color=BG)

    def show(self) -> None:
        self._root.deiconify()
        self._root.lift()
        self._root.focus_force()
        self._input.focus_set()

    def hide(self) -> None:
        self._root.withdraw()

    def destroy(self) -> None:
        if self._approval_dialog is not None:
            self._approval_dialog.destroy()
        self._root.destroy()

    def schedule(self, callback: Callable[[], None]) -> None:
        self._root.after(0, callback)

    def mainloop(self) -> None:
        self._root.mainloop()
