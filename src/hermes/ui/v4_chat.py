from __future__ import annotations

from datetime import datetime
from typing import Any

from hermes.ui.modern_theme import (
    BUTTON,
    BUTTON_HOVER,
    CARD,
    CARD_BORDER,
    FONT_HUD,
    FONT_MONO,
    HOLO_GREEN,
    INPUT_BG,
    MUTED,
    NEON_BLUE,
    NEON_CYAN,
    NEON_GOLD,
    ORANGE,
    TEXT,
    TEXT_BRIGHT,
)
from hermes.ui.v4_design import (
    V4EmptyState,
    V4HUDPanel,
    V4PageHeader,
    V4SectionHeader,
    V4StatusBadge,
    V4StatusTile,
    _phase_to_tr,
    make_selectable_text,
    status_color,
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover - headless fallback
    ctk = None  # type: ignore


_EVENT_VISUAL = {
    "reasoning": ("🧠 Akıl Yürütme", NEON_CYAN),
    "action": ("▶ Eylem", NEON_BLUE),
    "tool": ("🛠 Araç çağrısı", NEON_BLUE),
    "observation": ("👁 Gözlem", HOLO_GREEN),
    "verification": ("✓ Doğrulama", HOLO_GREEN),
    "approval": ("⚑ Onay", ORANGE),
    "recovery": ("↻ Kurtarma", NEON_GOLD),
    "completion": ("✓ Tamamlandı", HOLO_GREEN),
    "failure": ("✗ Başarısızlık", NEON_GOLD if NEON_GOLD else "#ff2a6d"),
    "task": ("◎ Görev", NEON_CYAN),
    "default": ("• Olay", MUTED),
}


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except Exception:
        return str(ts)[:8]


def _event_meta(kind: str) -> tuple[str, str]:
    k = (kind or "").lower()
    for key, (label, color) in _EVENT_VISUAL.items():
        if key in k:
            return label, color
    return _EVENT_VISUAL["default"]


def _approval_label(v: str) -> str:
    s = str(v or "none").lower()
    if s in {"required", "pending"}:
        return "GEREKİYOR"
    if s in {"granted", "approved"}:
        return "VERİLDİ"
    if s in {"denied", "rejected"}:
        return "REDDEDİLDİ"
    if s in {"none", "unnecessary"}:
        return "GEREKMİYOR"
    return str(v or "none").upper()


class V4Chat:
    def __init__(
        self,
        parent: Any,
        store: Any,
        on_send_message: Any | None = None,
        worker: Any | None = None,
    ) -> None:
        self.store = store
        self._on_send_cb = on_send_message
        self._worker = worker
        self._voice_last_state: dict[str, bool] = {"voice": True, "wake": True}
        self._sub: Any = None
        self._chat_sub_cb: Any | None = None
        self._timers: list[str] = []

        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")

        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.widget = self.root
        self.root.pack(fill="both", expand=True, padx=18, pady=14)

        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

        snap = {}
        try:
            snap = self.store.get_snapshot() or {}
        except Exception:
            snap = {}
        phase = snap.get("phase") or "idle"
        count = snap.get("event_count") or 0

        self.header = V4PageHeader(
            self.root,
            "SOHBET",
            subtitle="MUVAHHİD konuşma arayüzü — metin ve ses.",
            status=str(phase or "idle"),
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # main panel (timeline + composer stacked)
        self.main = V4HUDPanel(self.root)
        self.main.grid(row=1, column=0, sticky="nsew")
        self.main.frame.rowconfigure(0, weight=1)
        self.main.frame.columnconfigure(0, weight=1)

        # status subcluster (hero tiles mini)
        self.info = ctk.CTkFrame(self.main.frame, fg_color="transparent")
        self.info.grid(row=0, column=0, sticky="new", padx=18, pady=(16, 10))
        self.info.columnconfigure((0, 1, 2, 3), weight=1)
        self.tile_phase = V4StatusTile(self.info, "Faz", "—", NEON_CYAN)
        self.tile_phase.grid(row=0, column=0, sticky="ew", padx=(0, 12), pady=2)
        self.tile_approval = V4StatusTile(self.info, "Onay", "—", MUTED)
        self.tile_approval.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=2)
        self.tile_events = V4StatusTile(
            self.info, "Olaylar", f"OLAY · {count}", MUTED
        )
        self.tile_events.grid(row=0, column=2, sticky="ew", padx=(0, 12), pady=2)
        self.tile_mode = V4StatusTile(self.info, "Mod", "METİN + SES", HOLO_GREEN)
        self.tile_mode.grid(row=0, column=3, sticky="ew", pady=2)

        self.s1 = V4SectionHeader(self.main.frame, "Konuşma Zaman Çizelgesi")
        self.s1.grid(row=1, column=0, sticky="ew", padx=18, pady=(4, 6))

        self.scroll = ctk.CTkScrollableFrame(
            self.main.frame,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=CARD_BORDER,
        )
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 8))
        self.main.frame.rowconfigure(2, weight=1)
        self.scroll.columnconfigure(0, weight=1)

        self._msg_index = 0
        self.composer = ctk.CTkFrame(self.main.frame, fg_color=CARD, corner_radius=12, border_width=1, border_color=CARD_BORDER)
        self.composer.grid(row=3, column=0, sticky="ew", padx=14, pady=(4, 14))
        self.composer.columnconfigure(0, weight=1)
        self.composer.rowconfigure(0, weight=1)

        self.text_input = ctk.CTkTextbox(
            self.composer,
            height=64,
            fg_color=INPUT_BG,
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=10,
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 12),
        )
        self.text_input.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(12, 10), pady=10)

        self.mic_btn = ctk.CTkButton(
            self.composer,
            text="🎤",
            width=48,
            height=48,
            fg_color=BUTTON,
            hover_color=BUTTON_HOVER,
            text_color=NEON_GOLD,
            border_width=1,
            border_color=CARD_BORDER,
            corner_radius=999,
            font=("Segoe UI", 16),
            command=self._on_mic,
        )
        self.mic_btn.grid(row=0, column=1, padx=(0, 10), pady=(10, 4), sticky="ne")

        self.send_btn = ctk.CTkButton(
            self.composer,
            text="GÖNDER ⏎",
            width=96,
            height=44,
            fg_color=NEON_CYAN,
            hover_color=NEON_BLUE,
            text_color="#000a10",
            border_width=0,
            corner_radius=10,
            font=("Segoe UI", 11, "bold"),
            command=self._on_send,
        )
        self.send_btn.grid(row=1, column=1, padx=(0, 10), pady=(4, 10), sticky="se")

        self.placeholder_added = False
        # Restore persisted chat history BEFORE the live subscriber is
        # wired AND before the default placeholder is rendered. If the
        # store already has messages the empty state is skipped.
        self._restore_chat_history()
        if not self.placeholder_added and not getattr(store, "chat_messages", None):
            try:
                self.refresh()
            except Exception:
                self._add_placeholder_if_needed()

        # subscribe after initial build to prevent double render: merged
        # chat_message_added (real time bubbles) + legacy event listener
        def _on_store_signal(_evt: str, _payload) -> None:
            if _evt == "chat_message_added" and isinstance(_payload, dict):
                # Real-time worker → bridge → store pipeline.
                # Hide empty placeholder on first real message.
                if not self.placeholder_added:
                    self._clear_timeline_preserve_composer()
                    self.placeholder_added = False
                self._draw_bubble_from_entry(_payload, _scroll_end=True)
                return
            # All other store signals (event_applied, state ticks, etc.)
            # trigger the existing header/tile refresh path.
            self._schedule_refresh()

        try:
            self.store.subscribe(_on_store_signal)
            self._chat_sub_cb = _on_store_signal
            self._sub = self._chat_sub_cb
        except Exception:
            self._sub = None
            self._chat_sub_cb = None

    # ── Internal helpers ───────────────────────────────────────────────
    def _schedule_refresh(self) -> None:  # pragma: no cover - runtime only
        try:
            token = self.root.after(60, self.refresh)
            self._timers.append(token)
        except Exception:
            pass

    def _clear_timeline_preserve_composer(self) -> None:
        """Remove all scroll children except the composer (bottom row).

        ``_add_placeholder_if_needed`` creates a single V4EmptyState
        card inside ``self.scroll``; conversation bubbles are drawn
        there too. This helper clears both so a remount with history
        can redraw cleanly — ``placeholder_added`` is reset to False
        so callers can re-insert the placeholder if history later
        becomes empty again.
        """
        for w in self.scroll.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.placeholder_added = False

    # ── Chat history restore (store source-of-truth on remount) ─────────
    def _restore_chat_history(self) -> None:
        history = list(getattr(self.store, "chat_messages", None) or [])
        if not history:
            return
        # Existing placeholder (if any) was already rendered; clear it
        # so the replay looks like a continuous conversation.
        self._clear_timeline_preserve_composer()
        for entry in history:
            self._draw_bubble_from_entry(entry, _scroll_end=False)
        # Single scroll jump after replay
        try:
            self.scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _draw_bubble_from_entry(self, entry: dict, *, _scroll_end: bool = True) -> None:
        role = str(entry.get("role") or "system")
        text = str(entry.get("text") or "").strip()
        if not text:
            return
        ts = str(entry.get("timestamp") or datetime.now().strftime("%H:%M"))
        if role == "user":
            self._append_user_bubble(text, ts=ts, _scroll_end=_scroll_end, _from_store=True)
        elif role == "assistant":
            self._append_assistant_bubble(text, ts=ts, _scroll_end=_scroll_end)
        elif role == "status":
            self._append_status_bubble(text, ts=ts, _scroll_end=_scroll_end)
        else:
            self._append_system_bubble(text, ts=ts, _scroll_end=_scroll_end)

    def _on_send(self) -> None:  # pragma: no cover - integration hook
        try:
            text = self.text_input.get("1.0", "end").strip()
        except Exception:
            return
        if not text:
            return
        # Clear input box first
        self.placeholder_added = False
        try:
            self.text_input.delete("1.0", "end")
        except Exception:
            pass
        # Canonical path: inject into the worker → worker.emit("message")
        # → v4_bridge → store → subscriber draws the user bubble as a
        # single source of truth (no double render).
        cb_set = self._on_send_cb is not None and callable(self._on_send_cb)
        if cb_set:
            try:
                self._on_send_cb(text)
                return
            except Exception as exc:
                try:
                    print(f"[V4Chat._on_send] bridge FAIL: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
        # Fallback: no worker connected (demo / headless / test). Render
        # a local user bubble so the UI still feels responsive. The
        # canonical store mirror is NOT touched; fake AI answers are
        # NEVER fabricated.
        self._append_user_bubble(text, _from_store=False)

    def _append_user_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
        _from_store: bool = False,
    ) -> None:  # pragma: no cover
        del _from_store
        ts = ts or datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(0, 8))
        body = ctk.CTkFrame(row, fg_color="#0a2030", corner_radius=12,
                            border_width=1, border_color="#113a55")
        body.pack(side="right", fill="x", expand=False, anchor="n")
        make_selectable_text(
            body, text, font=FONT_HUD, fg=TEXT_BRIGHT, bg="#0a2030",
            wrap_chars=58, padx=12, pady=6,
        ).pack(anchor="e", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="e", padx=12, pady=(0, 6))
        try:
            self.scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _append_assistant_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
    ) -> None:  # pragma: no cover
        ts = ts or datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(
            row, text="M", width=28, height=28, corner_radius=14,
            fg_color=NEON_CYAN, text_color="#010508",
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", anchor="n", padx=(2, 8))
        body = ctk.CTkFrame(row, fg_color=CARD, corner_radius=12,
                            border_width=1, border_color="#113a55")
        body.pack(side="left", fill="x", expand=True, anchor="n")
        make_selectable_text(
            body, text, font=FONT_HUD, fg=TEXT_BRIGHT, bg=CARD,
            wrap_chars=58, padx=12, pady=8,
        ).pack(anchor="w", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="w", padx=12, pady=(0, 8))
        if _scroll_end:
            try:
                self.scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _append_status_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
    ) -> None:  # pragma: no cover
        ts = ts or datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", padx=60, pady=(0, 6))
        body = ctk.CTkFrame(row, fg_color="transparent", corner_radius=8,
                            border_width=1, border_color=HOLO_GREEN)
        body.pack(fill="x")
        make_selectable_text(
            body, f"• {text}", font=FONT_MONO, fg=HOLO_GREEN, bg=BG_DEEP,
            wrap_chars=52, padx=12, pady=6,
        ).pack(anchor="w", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="e", padx=12, pady=(0, 6))
        if _scroll_end:
            try:
                self.scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _append_system_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
    ) -> None:  # pragma: no cover
        ts = ts or datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.pack(fill="x", padx=48, pady=(0, 6))
        body = ctk.CTkFrame(row, fg_color="#1a0a12", corner_radius=8,
                            border_width=1, border_color="#ff2a6d")
        body.pack(fill="x")
        make_selectable_text(
            body, f"⚠ {text}", font=FONT_HUD, fg="#ff2a6d", bg="#1a0a12",
            wrap_chars=52, padx=12, pady=6,
        ).pack(anchor="w", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="e", padx=12, pady=(0, 6))
        if _scroll_end:
            try:
                self.scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _on_mic(self) -> None:  # pragma: no cover - integration hook
        """Toggle voice + wake-word using canonical BackgroundWorker APIs.

        Uses the existing worker.set_voice_enabled() /
        worker.set_wake_word_enabled() API surface — no new voice engine
        is introduced. When the worker is unavailable (demo / test env)
        a status bubble is emitted instead of silently failing.
        """
        w = getattr(self, "_worker", None)
        has_voice_api = bool(
            w is not None
            and hasattr(w, "set_voice_enabled")
            and callable(getattr(w, "set_voice_enabled"))
            and hasattr(w, "set_wake_word_enabled")
            and callable(getattr(w, "set_wake_word_enabled"))
        )
        if not has_voice_api:
            try:
                self.store.append_chat_message(
                    "status",
                    "Mikrofon: arka plan servisi bağlantısı yok. Ses + wake-word şu an için devre dışı.",
                )
            except Exception:
                pass
            try:
                tile_mode = getattr(self, "tile_mode", None)
                if tile_mode is not None:
                    tile_mode.set("METİN", dot=MUTED)
            except Exception:
                pass
            return
        state = getattr(self, "_voice_last_state", {}) or {}
        currently_on = bool(state.get("voice", True) and state.get("wake", True))
        next_on = not currently_on
        try:
            w.set_voice_enabled(next_on)
            w.set_wake_word_enabled(next_on)
            state["voice"] = next_on
            state["wake"] = next_on
        except Exception:
            next_on = currently_on
        try:
            tile_mode = getattr(self, "tile_mode", None)
            if tile_mode is not None:
                label = "METİN + SES" if next_on else "SADECE METİN"
                tile_mode.set(label, dot=HOLO_GREEN if next_on else MUTED)
        except Exception:
            pass
        try:
            msg = (
                "Ses + Wake-word: AÇIK (\"MUVAHHİD\" / \"abi\" diyerek çağırın)"
                if next_on
                else "Ses + Wake-word: KAPALI — yalnız metin."
            )
            self.store.append_chat_message("status", msg)
        except Exception:
            pass

    def _clear_timeline(self) -> None:
        for w in self.scroll.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.placeholder_added = False

    def _add_placeholder_if_needed(self) -> None:
        if self.placeholder_added:
            return
        empty = V4EmptyState(
            self.scroll,
            title="Henüz konuşma yok",
            subtitle="Ajan çalıştırdıkça Muvahhid etkinliği burada görünür. Başlamak için bir mesaj gönderin veya sesi kullanın.",
            dot=MUTED,
        )
        empty.pack(fill="x", padx=10, pady=(6, 6))
        self.placeholder_added = True

    def _render_event(self, ev: Any) -> None:
        kind = getattr(ev, "kind", "") or ""
        label, color = _event_meta(kind)
        ts = _fmt_ts(getattr(ev, "timestamp", None))
        payload: dict[str, Any] = getattr(ev, "payload", None) or {}
        text_title = str(payload.get("title") or payload.get("step") or payload.get("action") or getattr(ev, "kind", "Olay") or "").strip() or label
        detail_val = ""
        for key in ("detail", "message", "observation", "result", "tool", "target"):
            v = payload.get(key)
            if v:
                detail_val = str(v)
                break

        row = ctk.CTkFrame(self.scroll, fg_color="transparent")
        row.columnconfigure(1, weight=1)
        row.pack(fill="x", padx=12, pady=(0, 8))

        rail = ctk.CTkFrame(row, fg_color="transparent", width=24)
        rail.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 8))
        ctk.CTkLabel(rail, text="●", text_color=color, font=("Segoe UI", 10)).pack(side="top", anchor="n")
        ctk.CTkFrame(rail, width=1, fg_color=CARD_BORDER).pack(side="top", fill="y", expand=True, pady=(2, 0), padx=8)

        head = ctk.CTkFrame(row, fg_color="transparent")
        head.columnconfigure(1, weight=1)
        head.grid(row=0, column=1, sticky="ew")
        badge = V4StatusBadge(head, label.split()[0].strip("🧠▶🛠👁✓⚑↻✗◎•") or "OLAY", color=color)
        badge.grid(row=0, column=0, sticky="w", padx=(0, 8))
        ctk.CTkLabel(
            head,
            text=text_title[:160],
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
            justify="left",
        ).grid(row=0, column=1, sticky="we")
        if ts:
            ctk.CTkLabel(head, text=ts, text_color=MUTED, font=FONT_MONO).grid(row=0, column=2, padx=(10, 0), sticky="e")

        if detail_val:
            body = ctk.CTkFrame(
                row,
                fg_color=CARD,
                corner_radius=10,
                border_width=1,
                border_color=CARD_BORDER,
            )
            body.grid(row=1, column=1, sticky="we", pady=(3, 0))
            make_selectable_text(
                body, detail_val[:400], font=FONT_HUD, fg=TEXT, bg=CARD,
                wrap_chars=64, padx=12, pady=8,
            ).pack(fill="x", anchor="w", padx=0, pady=0)

    # ── Public refresh ─────────────────────────────────────────────────
    def refresh(self) -> None:
        snap: dict[str, Any] = {}
        events: list[Any] = []
        try:
            snap = self.store.get_snapshot() or {}
            events = list(getattr(self.store, "_events", []) or [])
        except Exception:
            snap = {}
            events = []

        phase = snap.get("phase") or "idle"
        approval = snap.get("approval_state") or "none"
        count = snap.get("event_count") or 0

        dot_p = status_color(str(phase))
        self.tile_phase.set(_phase_to_tr(str(phase)) or "—", dot=dot_p)
        self.tile_approval.set(
            _approval_label(str(approval or "none")),
            dot=status_color("warning" if str(approval).lower() in {"required", "pending"} else str(approval or "none")),
        )
        self.tile_events.set(f"OLAY · {count}")
        self.header.set_status(str(phase or "idle"))

        self._clear_timeline()
        if not events:
            self._add_placeholder_if_needed()
            return
        for ev in events[-64:]:
            self._render_event(ev)

    # ── Lifecycle ──────────────────────────────────────────────────────
    def destroy(self) -> None:  # pragma: no cover - lifecycle
        for t in list(self._timers):
            try:
                self.root.after_cancel(t)
            except Exception:
                pass
        self._timers.clear()
        if self._sub is not None:
            try:
                unsub = self._sub
                if callable(unsub):
                    unsub()
            except Exception:
                pass
            self._sub = None
        try:
            self.root.destroy()
        except Exception:
            pass

    def __del__(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.destroy()
        except Exception:
            pass
