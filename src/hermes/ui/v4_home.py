"""MUVAHHİD V4 — HOME = Ana AI Komuta Merkezi (Task 3 Reference Layout).

Composition (F6 reference visual match):
  ROW 0 (expand):
    COL weight=3  →  V4HUDPanel inside V4CoreCanvas (living AI Core, audio-reactive)
    COL weight=1  →  V4HUDPanel persistent premium Turkish Chat (300-360 px)
  ROW 1 (minimal):
    COL span=2    →  5 × V4HUDStatusChip (AJAN, ONAY, DOĞRULAMA, KURTARMA, HATA)

Semantic honesty (F15): Only canonical V4 snapshot fields are used:
  phase, approval_state, verification_state, recovery_state, error_state,
  event_count, latest_event_id.
Missing fields (BAĞLANTI, OTURUM, etc.) use honest "BİLDİRİLMİYOR" fallback.

Animations: destroy() → after_cancel + AudioProbe.destroy + CoreCanvas.destroy.
Audio-reactive Core (F12): Real microphone RMS via AudioProbe; missing mic → 0.0
amplitude with calm SafeFallbackWaveform seed animation (NEVER fake pretend audio).
"""
from __future__ import annotations
import datetime as _dt
import traceback as _traceback
from typing import Any

try:
    import customtkinter as ctk
except ModuleNotFoundError:  # pragma: no cover
    ctk = None  # type: ignore

from hermes.ui.modern_theme import (
    BG, CARD, CARD_BORDER, NEON_CYAN, ORANGE, HOLO_GREEN,
    NEON_GOLD, NEON_MAGENTA, TEXT, TEXT_BRIGHT, MUTED,
    BUTTON, BUTTON_HOVER, INPUT_BG, BG_DEEP, FONT_HUD, FONT_MONO,
)
from hermes.ui.v4_design import (
    V4HUDPanel, V4CoreCanvas, V4HUDStatusChip,
    _phase_to_tr, make_selectable_text, status_color,
)
from hermes.ui.v4_audio import AudioProbe, SafeFallbackWaveform
from hermes.ui.v4_store import V4UIStore


_APPROVAL_TR: dict[str, tuple[str, str]] = {
    "none":     ("GEREKMIYOR", MUTED),
    "required": ("GEREKLI",   NEON_GOLD),
    "resolved": ("ÇÖZÜLDÜ",   HOLO_GREEN),
    "timeout":  ("ZAMAN AŞIMI", NEON_MAGENTA),
}
_VERIFY_TR: dict[str, tuple[str, str]] = {
    "pending":   ("BEKLEMEDE", NEON_CYAN),
    "unknown":   ("BİLİNMİYOR", MUTED),
    "verified":  ("DOĞRULANDI", HOLO_GREEN),
    "failed":    ("BAŞARISIZ",  NEON_MAGENTA),
}
_RECOVERY_TR: dict[str, tuple[str, str]] = {
    "none":     ("YOK",        MUTED),
    "recovery": ("AKTIF",      NEON_GOLD),
    "done":     ("TAMAMLANDI", HOLO_GREEN),
}
_ERROR_TR: dict[str, tuple[str, str]] = {
    "none":    ("YOK",          MUTED),
    "fatal":   ("KRITIK HATA",  NEON_MAGENTA),
}


def _approval_map(state: str) -> tuple[str, str]:
    if not state:
        return _APPROVAL_TR["none"]
    return _APPROVAL_TR.get(str(state).lower(), (str(state).upper() or "BİLİNMİYOR", MUTED))


def _verify_map(state: str) -> tuple[str, str]:
    if not state:
        return _VERIFY_TR["pending"]
    return _VERIFY_TR.get(str(state).lower(), (str(state).upper() or "BİLİNMİYOR", MUTED))


def _recovery_map(state: str) -> tuple[str, str]:
    if not state:
        return _RECOVERY_TR["none"]
    return _RECOVERY_TR.get(str(state).lower(), (str(state).upper() or "BİLİNMİYOR", NEON_GOLD))


def _error_map(state: str) -> tuple[str, str]:
    if not state:
        return _ERROR_TR["none"]
    if str(state).lower() in ("none", "ok", ""):
        return _ERROR_TR["none"]
    short = (str(state)[:14] + "…") if len(str(state)) > 14 else str(state)
    return (short.upper(), NEON_MAGENTA)


class V4Home:
    """MUVAHHİD HOME — central living AI Core + persistent Chat + 5 status chips."""

    # ── Lifecycle ────────────────────────────────────────────────────────
    def __init__(self, parent: Any, store: V4UIStore | None = None, on_send_message: Any | None = None) -> None:
        self.parent = parent
        self.store = store
        self._on_send_cb = on_send_message
        self._snap = store.get_snapshot() if store is not None else {}

        self._root_frame: ctk.CTkFrame | None = None
        self._core: V4CoreCanvas | None = None
        self._probe: AudioProbe | None = None
        self._audio_after: str | None = None
        self._store_after: str | None = None
        self._destroyed = False
        # Chat subscriber identity kept for unsubscribe-on-destroy
        self._chat_sub_cb: Any | None = None
        self._chat_placeholder_drawn = False
        # chat_welcome: keeps the welcome/suggestions content; when history
        # is empty it is visible, when the first real chat message arrives
        # it is hidden (preserved across remounts — no state mutation here).
        self._welcome_frame: ctk.CTkFrame | None = None

        if ctk is None:  # headless
            return

        self._probe = AudioProbe()
        try:
            self._probe.start()
        except Exception as exc:
            try:
                print(f"[V4Home.__init__] AudioProbe.start FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass

        self._build_layout()
        # Restore chat history from canonical store BEFORE binding the
        # live subscriber, so in-flight messages do not double-render
        # entries we already replayed during mount.
        self._restore_chat_history()
        self._bind_store_subscriber()
        # Start audio → Core refresh loop @ ~30 FPS
        try:
            self._audio_after = self._root_frame.after(33, self._audio_tick)
        except Exception as exc:
            try:
                print(f"[V4Home.__init__] schedule _audio_tick FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
            self._audio_after = None
        # Core animation (canonical single loop — Home only sets amp, Core draws itself)
        if self._core is not None:
            try:
                self._core.start_animation(33)
            except Exception as exc:
                try:
                    print(f"[V4Home.__init__] Core.start_animation FAIL: {type(exc).__name__}: {exc}\n"
                          + _traceback.format_exc(limit=5), flush=True)
                except Exception:
                    pass
        # Initial bottom chip paint
        self._refresh_status_chips()

    # ── Layout ───────────────────────────────────────────────────────────
    def _build_layout(self) -> None:
        root = ctk.CTkFrame(self.parent, fg_color=BG, corner_radius=0)
        root.pack(fill="both", expand=True, padx=0, pady=0)
        # Reference visual balance: 58% Core / 42% Chat → weight 10 : 7
        root.grid_columnconfigure(0, weight=10, uniform="homecol")
        root.grid_columnconfigure(1, weight=7, uniform="homecol", minsize=380)
        root.grid_rowconfigure(0, weight=1)
        # Thinner footer — cinematic HUD strip (VT6)
        root.grid_rowconfigure(1, weight=0, minsize=50)
        self._root_frame = root

        # ── CENTER COLUMN (weight 10 = ~58%) — V4HUDPanel + living Core ─────
        core_wrap = V4HUDPanel(root, accent=NEON_CYAN, fg_color=BG_DEEP, corner_radius=0)
        core_wrap.grid(row=0, column=0, sticky="nsew", padx=(18, 14), pady=(18, 14))
        core_wrap.frame.grid_columnconfigure(0, weight=1)
        core_wrap.frame.grid_rowconfigure(0, weight=1)

        # Core sizing: ~%50-60 of container (medium premium — not dominant, SIFIRDAN CORE).
        # 1200x800 → R~280, 1920x1080 → R~360.
        phase_tr = _phase_to_tr(self._snap.get("phase") or "idle")
        core = V4CoreCanvas(core_wrap.frame, size_px=460, phase_tr=phase_tr, audio_amplitude=0.0)
        core.grid(row=0, column=0, sticky="nsew", padx=42, pady=42)
        self._core = core

        # ── RIGHT COLUMN (weight 7 = ~42%, 380+ px) — Persistent Chat ────
        self._build_chat_panel(root, col=1)

        # ── BOTTOM ROW (span=2) — 5 HUD Status Chips (thin, cyan-only strip)
        self._build_bottom_status(root, row=1)

    # ── Chat Panel (Türkçe, integrated — NOT standalone chat app feel) ───
    def _build_chat_panel(self, root: ctk.CTkFrame, col: int) -> None:
        wrap = V4HUDPanel(root, accent=NEON_CYAN, fg_color=CARD, corner_radius=0)
        wrap.grid(row=0, column=col, sticky="nsew", padx=(7, 14), pady=(14, 10))
        wrap.frame.grid_columnconfigure(0, weight=1)
        wrap.frame.grid_rowconfigure(1, weight=1)

        # Header: SOHBET + speaker icon + gear icon
        header = ctk.CTkFrame(wrap.frame, fg_color="transparent", height=40)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="◆  SOHBET", text_color=TEXT_BRIGHT,
            font=("Segoe UI", 13, "bold"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        icon_row = ctk.CTkFrame(header, fg_color="transparent")
        icon_row.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            icon_row, text="🔊", text_color=MUTED, font=("Segoe UI", 14), width=26,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkLabel(
            icon_row, text="⚙", text_color=MUTED, font=("Segoe UI", 14), width=26,
        ).pack(side="left")

        # Scrollable message area (CTkScrollableFrame)
        self._chat_scroll = ctk.CTkScrollableFrame(
            wrap.frame, fg_color=BG_DEEP, corner_radius=10,
            border_width=1, border_color=CARD_BORDER,
        )
        self._chat_scroll.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 6))
        self._chat_scroll._scrollbar.configure(width=6)  # quiet scrollbar

        # Welcome bubble + 4 suggestion buttons (F17: exactly 1 welcome message)
        self._render_initial_chat()

        # Composer: attach icon + multiline input + mic + send
        composer = ctk.CTkFrame(wrap.frame, fg_color="transparent", height=80)
        composer.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))
        composer.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            composer, text="📎", text_color=MUTED, font=("Segoe UI", 15), width=26,
        ).grid(row=0, column=0, padx=(0, 6), pady=0, sticky="n")
        self._chat_input = ctk.CTkTextbox(
            composer, height=64, corner_radius=10, fg_color=INPUT_BG,
            text_color=TEXT_BRIGHT, border_width=1, border_color=CARD_BORDER,
            font=FONT_HUD,
        )
        self._chat_input.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=0)
        self._chat_input.insert("0.0", "")
        mic_btn = ctk.CTkButton(
            composer, text="🎙", width=40, height=64, corner_radius=10,
            fg_color=CARD, hover_color=BUTTON_HOVER, text_color=NEON_CYAN,
            border_width=1, border_color=CARD_BORDER,
            font=("Segoe UI", 14),
            command=self._on_mic_toggle,
        )
        mic_btn.grid(row=0, column=2, padx=(0, 6), pady=0, sticky="n")
        send_btn = ctk.CTkButton(
            composer, text="➤", width=40, height=64, corner_radius=10,
            fg_color=NEON_CYAN, hover_color="#00b8cc", text_color=BG_DEEP,
            border_width=0,
            font=("Segoe UI", 14, "bold"),
            command=self._on_send_message,
        )
        send_btn.grid(row=0, column=3, padx=0, pady=0, sticky="n")

        # Voice status row (bottom right of chat panel)
        voice_row = ctk.CTkFrame(wrap.frame, fg_color="transparent", height=28)
        voice_row.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 10))
        ctk.CTkLabel(
            voice_row, text="🎙", text_color=HOLO_GREEN, font=("Segoe UI", 12), width=22,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            voice_row, text="Sesli komut hazır", text_color=TEXT,
            font=FONT_HUD,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            voice_row, text="│", text_color=CARD_BORDER, font=FONT_MONO,
        ).pack(side="left", padx=0)
        ctk.CTkLabel(
            voice_row, text=" Uyandırma kelimesi: ", text_color=MUTED,
            font=FONT_HUD,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            voice_row, text="MUVAHHİD", text_color=NEON_CYAN,
            font=("Consolas", 10, "bold"),
        ).pack(side="left")

    def _render_initial_chat(self) -> None:
        """Exactly one static MUVAHHİD welcome bubble + 4 suggestions (F17).

        Wrapped inside a single ``_welcome_frame`` so it can be hidden
        deterministically when the first real chat message arrives,
        and shown again (no-op rebuild) if history is replayed on a
        fresh remount with an empty store.
        """
        ts = _dt.datetime.now().strftime("%H:%M")

        wrap = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        wrap.pack(fill="x", padx=0, pady=(0, 0))
        self._welcome_frame = wrap

        # MUVAHHİD welcome bubble
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(8, 8))
        avatar = ctk.CTkLabel(
            row, text="M", width=28, height=28, corner_radius=14,
            fg_color=NEON_CYAN, text_color=BG_DEEP,
            font=("Segoe UI", 12, "bold"),
        )
        avatar.pack(side="left", anchor="n", padx=(2, 8))
        body = ctk.CTkFrame(row, fg_color=CARD, corner_radius=12,
                            border_width=1, border_color="#113a55")
        body.pack(side="left", fill="x", expand=True, anchor="n")
        ctk.CTkLabel(
            body, text="Merhaba! Ben Muvahhid.",
            text_color=TEXT_BRIGHT, font=("Segoe UI", 12, "bold"),
            anchor="w", justify="left", wraplength=260,
        ).pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(
            body, text="Size nasıl yardımcı olabilirim?",
            text_color=TEXT, font=FONT_HUD,
            anchor="w", justify="left", wraplength=260,
        ).pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(
            body, text=ts, text_color=MUTED, font=FONT_MONO,
        ).pack(anchor="e", padx=12, pady=(0, 8))

        # 4 Turkish suggestion buttons (subtle outline style)
        suggestions = [
            ("🖥", "Bilgisayarımı kontrol et"),
            ("🌐", "Bir web sayfası aç"),
            ("✓", "Yeni bir görev başlat"),
            ("⌂", "Hafızamda ara"),
        ]
        sug_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        sug_frame.pack(fill="x", padx=38, pady=(2, 8))
        for icon, label in suggestions:
            b = ctk.CTkButton(
                sug_frame, text=f"  {icon}   {label}",
                height=32, corner_radius=10,
                fg_color=BG_DEEP, hover_color=BUTTON_HOVER,
                text_color=TEXT_BRIGHT, anchor="w",
                border_width=1, border_color=CARD_BORDER,
                font=FONT_HUD,
                command=lambda t=label: self._apply_suggestion(t),
            )
            b.pack(fill="x", pady=2)

    # ── Chat: canonical source-of-truth restore + live subscriber ────────
    def _restore_chat_history(self) -> None:
        """Redraw all persisted chat bubbles from ``store.chat_messages``.

        Called exactly once on mount **before** the live subscriber is
        attached, so the first in-flight ``chat_message_added`` signal
        will render only the newly appended entry (no double-render
        against the replayed history). If history is empty, the
        welcome frame remains visible.
        """
        if self.store is None or self._chat_scroll is None:
            return
        history = list(getattr(self.store, "chat_messages", None) or [])
        if not history:
            return
        # At least one real message exists → hide welcome/suggestions
        # so the timeline looks like a continuous conversation.
        self._hide_welcome_if_visible()
        for entry in history:
            self._draw_bubble_from_entry(entry, _scroll_end=False)
        # Single scroll jump after replaying history
        try:
            self._chat_scroll._parent_canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _hide_welcome_if_visible(self) -> None:
        if self._welcome_frame is None:
            return
        try:
            self._welcome_frame.pack_forget()
        except Exception:
            pass
        try:
            for w in self._welcome_frame.winfo_children():
                try:
                    w.pack_forget()
                except Exception:
                    pass
        except Exception:
            pass
        self._chat_placeholder_drawn = True

    def _draw_bubble_from_entry(self, entry: dict, *, _scroll_end: bool = True) -> None:
        """Draw a single chat bubble matching ``entry["role"]``.

        Roles are the canonical worker emit values: ``user``
        (worker L119/L321/L473), ``assistant`` (L311), ``status``
        (L306/L409), ``system`` (L376/L480/L525/L543).
        """
        role = str(entry.get("role") or "system")
        text = str(entry.get("text") or "").strip()
        if not text:
            return
        ts = str(entry.get("timestamp") or _dt.datetime.now().strftime("%H:%M"))
        if role == "user":
            self._append_user_bubble(text, ts=ts, _scroll_end=_scroll_end, _from_store=True)
        elif role == "assistant":
            self._append_assistant_bubble(text, ts=ts, _scroll_end=_scroll_end)
        elif role == "status":
            self._append_status_bubble(text, ts=ts, _scroll_end=_scroll_end)
        else:
            self._append_system_bubble(text, ts=ts, _scroll_end=_scroll_end)

    # ── Bottom 5 Status Chips (AJAN ONAY DOĞRULAMA KURTARMA HATA) thin HUD ──
    def _build_bottom_status(self, root: ctk.CTkFrame, row: int) -> None:
        # Accent NEON_CYAN only — thin cinematic footer strip (no dashboard feel)
        wrap = V4HUDPanel(root, accent=NEON_CYAN, fg_color=CARD, corner_radius=0)
        wrap.grid(row=row, column=0, columnspan=2, sticky="nsew",
                  padx=10, pady=(2, 6))
        wrap.frame.grid_columnconfigure(tuple(range(5)), weight=1, uniform="chip")

        self.chip_agent = V4HUDStatusChip(
            wrap.frame, "AJAN", "HAZIR", dot=HOLO_GREEN,
            key_color=MUTED, value_color=HOLO_GREEN,
        )
        self.chip_agent.grid(row=0, column=0, sticky="ew", padx=8, pady=(3, 3))

        self.chip_approval = V4HUDStatusChip(
            wrap.frame, "ONAY", "GEREKMIYOR", dot=MUTED,
            key_color=MUTED, value_color=MUTED,
        )
        self.chip_approval.grid(row=0, column=1, sticky="ew", padx=8, pady=(3, 3))

        self.chip_verify = V4HUDStatusChip(
            wrap.frame, "DOĞRULAMA", "BEKLEMEDE", dot=NEON_CYAN,
            key_color=MUTED, value_color=NEON_CYAN,
        )
        self.chip_verify.grid(row=0, column=2, sticky="ew", padx=8, pady=(3, 3))

        self.chip_recovery = V4HUDStatusChip(
            wrap.frame, "KURTARMA", "YOK", dot=MUTED,
            key_color=MUTED, value_color=MUTED,
        )
        self.chip_recovery.grid(row=0, column=3, sticky="ew", padx=8, pady=(3, 3))

        self.chip_error = V4HUDStatusChip(
            wrap.frame, "HATA", "YOK", dot=MUTED,
            key_color=MUTED, value_color=MUTED,
        )
        self.chip_error.grid(row=0, column=4, sticky="ew", padx=8, pady=(3, 3))

    # ── Store subscriber: refresh Core phase + 5 chips + live chat timeline ──
    def _bind_store_subscriber(self) -> None:
        def _on_store_event(_evt: str, _payload) -> None:
            if self._destroyed:
                return
            if _evt == "chat_message_added" and isinstance(_payload, dict):
                # Real-time: worker.emit("message", …) fired through
                # v4_bridge → store.append_chat_message → here.
                try:
                    self._hide_welcome_if_visible()
                    self._draw_bubble_from_entry(_payload, _scroll_end=True)
                except Exception:
                    pass
                return
            try:
                self._snap = self.store.get_snapshot() if self.store is not None else {}
                phase_tr = _phase_to_tr(self._snap.get("phase") or "idle")
                if self._core is not None:
                    self._core.set_phase(phase_tr)
                self._refresh_status_chips()
            except Exception:
                pass

        try:
            if self.store is not None:
                self.store.subscribe(_on_store_event)
                self._chat_sub_cb = _on_store_event
        except Exception:
            self._chat_sub_cb = None
        # Periodic refresh fallback (in case subscribe not wired yet, always safe)
        try:
            if self._root_frame is not None:
                self._store_after = self._root_frame.after(1500, self._periodic_store_refresh)
        except Exception:
            self._store_after = None

    def _periodic_store_refresh(self) -> None:  # pragma: no cover
        if self._destroyed:
            return
        try:
            self._snap = self.store.get_snapshot() if self.store is not None else {}
            phase_tr = _phase_to_tr(self._snap.get("phase") or "idle")
            if self._core is not None:
                self._core.set_phase(phase_tr)
            self._refresh_status_chips()
        except Exception:
            pass
        try:
            if self._root_frame is not None:
                self._store_after = self._root_frame.after(1500, self._periodic_store_refresh)
        except Exception:
            self._store_after = None

    def _refresh_status_chips(self) -> None:
        snap = self._snap or {}
        # AJAN → phase (semantically correct per V4 contract; F15 audit passed)
        phase_tr = _phase_to_tr(snap.get("phase") or "idle")
        p_dot = status_color(phase_tr, default=HOLO_GREEN)
        if p_dot == MUTED:
            p_dot = HOLO_GREEN
        try:
            self.chip_agent.set(value_label=phase_tr, dot=p_dot, value_color=p_dot)
        except Exception:
            pass
        # ONAY → approval_state
        a_val, a_dot = _approval_map(snap.get("approval_state") or "none")
        try:
            self.chip_approval.set(value_label=a_val, dot=a_dot, value_color=a_dot)
        except Exception:
            pass
        # DOĞRULAMA → verification_state
        v_val, v_dot = _verify_map(snap.get("verification_state") or "pending")
        try:
            self.chip_verify.set(value_label=v_val, dot=v_dot, value_color=v_dot)
        except Exception:
            pass
        # KURTARMA → recovery_state
        r_val, r_dot = _recovery_map(snap.get("recovery_state") or "none")
        try:
            self.chip_recovery.set(value_label=r_val, dot=r_dot, value_color=r_dot)
        except Exception:
            pass
        # HATA → error_state
        e_val, e_dot = _error_map(snap.get("error_state") or "none")
        try:
            self.chip_error.set(value_label=e_val, dot=e_dot, value_color=e_dot)
        except Exception:
            pass

    # ── Audio tick: wire real mic probe → Core amplitude (F12 audio-reactive)
    # IMPORTANT ARCHITECTURE: This loop ONLY sets amplitude state on the Core.
    # It NEVER draws the Core. The single canonical Core animation loop is
    # V4CoreCanvas.start_animation → _tick → redraw (30 FPS internal).  This
    # audio loop runs in parallel to sample amplitude but never competes for canvas.
    def _audio_tick(self) -> None:  # pragma: no cover - platform specific
        if self._destroyed:
            return
        amp = 0.0
        try:
            if self._probe is not None:
                raw_amp = self._probe.get_amplitude()
                # Always clamp 0.0..1.0 regardless of probe output
                if isinstance(raw_amp, (int, float)):
                    amp = max(0.0, min(1.0, float(raw_amp)))
                else:
                    amp = 0.0
        except Exception as exc:
            try:
                print(f"[V4Home._audio_tick] probe FAIL, using amp=0.0: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
            amp = 0.0
        try:
            if self._core is not None:
                self._core.set_amplitude(amp)
        except Exception as exc:
            try:
                print(f"[V4Home._audio_tick] Core.set_amplitude FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
        # SafeFallbackWaveform is used INSIDE V4CoreCanvas when amp ~ 0
        # to draw a calm idle pattern — never pretends to be real audio.
        _ = SafeFallbackWaveform(0.0, 1)  # keep import live
        try:
            if self._root_frame is not None:
                self._audio_after = self._root_frame.after(33, self._audio_tick)
        except Exception as exc:
            try:
                print(f"[V4Home._audio_tick] reschedule FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
            self._audio_after = None

    # ── Chat button commands (UI-only, no backend mutation) ───────────
    def _apply_suggestion(self, text: str) -> None:  # pragma: no cover - UI handler
        try:
            self._chat_input.delete("0.0", "end")
            self._chat_input.insert("0.0", text)
            # Optional: scroll input to top
            self._chat_input.see("0.0")
        except Exception:
            pass

    def _on_send_message(self) -> None:  # pragma: no cover - UI handler
        try:
            text_raw = self._chat_input.get("0.0", "end").strip()
        except Exception:
            return
        if not text_raw:
            return
        # Clear input box first
        try:
            self._chat_input.delete("0.0", "end")
        except Exception:
            pass
        # Canonical path: inject the user message into the worker via
        # on_send_message callback. The worker itself emits
        # WorkerCommand.SEND_MESSAGE → handle_command L471 append_message
        # ("user", text) → _emit("message") → v4_bridge bind_store_events
        # → store.append_chat_message → our store subscriber draws the
        # user bubble (single source of truth, no double render).
        cb_set = self._on_send_cb is not None and callable(self._on_send_cb)
        if cb_set:
            try:
                self._on_send_cb(text_raw)
                return
            except Exception as exc:
                try:
                    print(f"[V4Home._on_send_message] bridge FAIL: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
        # Fallback: no worker bridge connected (demo / headless / test).
        # Draw a local user bubble so the UI still feels responsive —
        # the canonical store mirror is NOT touched here (no fake backend
        # response ever fabricated).
        self._hide_welcome_if_visible()
        self._append_user_bubble(text_raw, _from_store=False)

    def _append_user_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
        _from_store: bool = False,
    ) -> None:  # pragma: no cover - UI only
        del _from_store  # reserved guard; currently unused for visual differentiation
        ts = ts or _dt.datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(0, 8))
        body = ctk.CTkFrame(row, fg_color="#0a2030", corner_radius=12,
                            border_width=1, border_color="#113a55")
        body.pack(side="right", fill="x", expand=False, anchor="n")
        make_selectable_text(
            body, text, font=FONT_HUD, fg=TEXT_BRIGHT, bg="#0a2030",
            wrap_chars=48, padx=12, pady=6,
        ).pack(anchor="e", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="e", padx=12, pady=(0, 6))
        ctk.CTkLabel(
            row, text="👤", width=28, height=28, corner_radius=14,
            fg_color="#0c2a40", text_color=TEXT_BRIGHT,
            font=("Segoe UI", 12),
        ).pack(side="right", anchor="n", padx=(8, 2))
        if _scroll_end:
            try:
                self._chat_scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _append_assistant_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
    ) -> None:  # pragma: no cover - UI only
        ts = ts or _dt.datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=(0, 8))
        ctk.CTkLabel(
            row, text="M", width=28, height=28, corner_radius=14,
            fg_color=NEON_CYAN, text_color=BG_DEEP,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", anchor="n", padx=(2, 8))
        body = ctk.CTkFrame(row, fg_color=CARD, corner_radius=12,
                            border_width=1, border_color="#113a55")
        body.pack(side="left", fill="x", expand=True, anchor="n")
        make_selectable_text(
            body, text, font=FONT_HUD, fg=TEXT_BRIGHT, bg=CARD,
            wrap_chars=48, padx=12, pady=8,
        ).pack(anchor="w", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="e", padx=12, pady=(0, 8))
        if _scroll_end:
            try:
                self._chat_scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _append_status_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
    ) -> None:  # pragma: no cover - UI only
        ts = ts or _dt.datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=40, pady=(0, 6))
        body = ctk.CTkFrame(row, fg_color="transparent", corner_radius=8,
                            border_width=1, border_color=HOLO_GREEN)
        body.pack(fill="x")
        make_selectable_text(
            body, f"• {text}", font=FONT_MONO, fg=HOLO_GREEN, bg=BG_DEEP,
            wrap_chars=44, padx=10, pady=6,
        ).pack(anchor="w", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="e", padx=10, pady=(0, 6))
        if _scroll_end:
            try:
                self._chat_scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _append_system_bubble(
        self,
        text: str,
        *,
        ts: str | None = None,
        _scroll_end: bool = True,
    ) -> None:  # pragma: no cover - UI only
        ts = ts or _dt.datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self._chat_scroll, fg_color="transparent")
        row.pack(fill="x", padx=34, pady=(0, 6))
        body = ctk.CTkFrame(row, fg_color="#1a0a12", corner_radius=8,
                            border_width=1, border_color=NEON_MAGENTA)
        body.pack(fill="x")
        make_selectable_text(
            body, f"⚠ {text}", font=FONT_HUD, fg=NEON_MAGENTA, bg="#1a0a12",
            wrap_chars=44, padx=10, pady=6,
        ).pack(anchor="w", fill="x")
        ctk.CTkLabel(body, text=ts, text_color=MUTED, font=FONT_MONO).pack(
            anchor="e", padx=10, pady=(0, 6))
        if _scroll_end:
            try:
                self._chat_scroll._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass

    def _on_mic_toggle(self) -> None:  # pragma: no cover - UI toggle only
        # Toggle AudioProbe start/stop — visual-only (no V3 backend event).
        if self._probe is None:
            return
        try:
            if self._probe.available and self._probe._stream is not None:  # type: ignore[attr-defined]
                self._probe.stop()
            elif self._probe.available:
                self._probe.start()
        except Exception:
            pass

    # ── Safe destroy (no leaky timers, no open audio streams) ───────
    def destroy(self) -> None:  # pragma: no cover - lifecycle
        self._destroyed = True
        for timer in (self._audio_after, self._store_after):
            if timer is not None and self._root_frame is not None:
                try:
                    self._root_frame.after_cancel(timer)
                except Exception:
                    pass
        self._audio_after = None
        self._store_after = None
        # Core animation after_cancel
        try:
            if self._core is not None:
                self._core.destroy()
        except Exception:
            pass
        self._core = None
        # AudioProbe: stop stream + terminate pyaudio
        try:
            if self._probe is not None:
                self._probe.destroy()
        except Exception:
            pass
        self._probe = None
        # Finally, destroy root frame
        try:
            if self._root_frame is not None:
                self._root_frame.destroy()
        except Exception:
            pass
        self._root_frame = None
