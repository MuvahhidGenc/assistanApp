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
    MUTED,
    NEON_BLUE,
    NEON_CYAN,
    NEON_GOLD,
    NEON_MAGENTA,
    TEXT,
    TEXT_BRIGHT,
)
from hermes.ui.v4_design import (
    V4Card,
    V4EmptyState,
    V4HUDPanel,
    V4PageHeader,
    V4Panel,
    V4SectionHeader,
    V4StatusBadge,
    V4StatusTile,
    V4TimelineEvent,
    _phase_to_tr,
    status_color,
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover
    ctk = None  # type: ignore


_PHASE_META = {
    "idle": ("HAZIR", "Komut bekleniyor.", "idle"),
    "planning": ("PLANLANIYOR", "Yaklaşım değerlendiriliyor.", "active"),
    "reasoning": ("AKIL YÜRÜTÜLÜYOR", "Sorun üzerinde düşünülüyor.", "active"),
    "executing": ("ÇALIŞIYOR", "Eylemler çalıştırılıyor.", "active"),
    "verifying": ("DOĞRULANIYOR", "Sonuçlar kontrol ediliyor.", "success"),
    "awaiting_approval": ("ONAY BEKLİYOR", "İnsan kararı gerekiyor.", "warning"),
    "recovering": ("KURTARILIYOR", "Hata düzeltiliyor.", "warning"),
    "completed": ("TAMAMLANDI", "Görev sonuçlandı.", "success"),
    "failed": ("BAŞARISIZ", "Görev hatayla durduruldu.", "danger"),
}


def _fmt_ts(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%H:%M:%S")
    except Exception:
        return str(ts)[:8]


def _approval_detail(a: str) -> tuple[str, str]:
    s = (a or "").lower()
    if "required" in s or "pending" in s:
        return "GEREKLİ · İnsan Kararı", NEON_GOLD
    if "granted" in s or "approved" in s:
        return "ONAYLANDI", HOLO_GREEN
    if "denied" in s or "rejected" in s:
        return "REDDEDİLDİ", NEON_MAGENTA
    if s in {"none", "", "null"}:
        return "YOK", MUTED
    return s.upper(), status_color(s, MUTED)


def _recover_detail(r: str) -> tuple[str, str]:
    s = (r or "").lower()
    if s in {"recovering", "active"}:
        return "KURTARILIYOR", NEON_GOLD
    if s in {"recovered", "done"}:
        return "TAMAMLANDI", HOLO_GREEN
    if s in {"none", "", "null"}:
        return "YOK", MUTED
    return s.upper(), status_color(s, MUTED)


def _verify_detail(v: str) -> tuple[str, str]:
    s = (v or "").lower()
    if s in {"passed", "verified", "ok", "success"}:
        return "GEÇTİ", HOLO_GREEN
    if s in {"pending", "running", "active"}:
        return "BEKLEMEDE", NEON_CYAN
    if s in {"failed", "error"}:
        return "BAŞARISIZ", NEON_MAGENTA
    if s in {"none", "", "null"}:
        return "YOK", MUTED
    return s.upper(), status_color(s, MUTED)


class V4Agent:
    def __init__(self, parent: Any, store: Any) -> None:
        self.store = store
        self._sub: Any = None
        self._timers: list[str] = []

        if ctk is None:  # pragma: no cover
            raise RuntimeError("customtkinter unavailable")

        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.widget = self.root
        self.root.pack(fill="both", expand=True, padx=18, pady=14)
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)

        snap: dict[str, Any] = {}
        try:
            snap = self.store.get_snapshot() or {}
        except Exception:
            snap = {}
        phase = snap.get("phase") or "idle"

        self.header = V4PageHeader(
            self.root,
            "AJAN",
            subtitle="Canlı otonom yürütme merkezi.",
            status=str(phase or "idle"),
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # big status card
        self.status_wrap = V4Card(self.root)
        self.status_wrap.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.status_wrap.frame.columnconfigure(1, weight=1)
        self.status_wrap.frame.rowconfigure(0, weight=1)

        self.big_dot = ctk.CTkLabel(
            self.status_wrap.frame,
            text="●●●",
            text_color=NEON_CYAN,
            font=("Segoe UI", 26),
        )
        self.big_dot.grid(row=0, column=0, rowspan=2, padx=(22, 16), pady=18, sticky="w")
        self.big_phase = ctk.CTkLabel(
            self.status_wrap.frame,
            text="SİSTEM HAZIR",
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 24, "bold"),
            anchor="w",
        )
        self.big_phase.grid(row=0, column=1, sticky="ew", padx=(0, 20), pady=(18, 0))
        self.big_sub = ctk.CTkLabel(
            self.status_wrap.frame,
            text="Komut bekleniyor.",
            text_color=MUTED,
            font=("Segoe UI", 12),
            anchor="w",
            justify="left",
        )
        self.big_sub.grid(row=1, column=1, sticky="ew", padx=(0, 20), pady=(0, 18))

        # approval banner (hidden by default)
        self.approval_banner: ctk.CTkFrame | None = None

        # 2 column: left (8 tiles grid) + right (timeline)
        self.twocol = ctk.CTkFrame(self.root, fg_color="transparent")
        self.twocol.grid(row=2, column=0, sticky="nsew")
        self.twocol.columnconfigure(0, weight=3, uniform="ag2col")
        self.twocol.columnconfigure(1, weight=3, uniform="ag2col")
        self.twocol.rowconfigure(1, weight=1)

        self.left_panel = V4HUDPanel(self.twocol)
        self.left_panel.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
        self.left_panel.frame.columnconfigure((0, 1), weight=1, uniform="ltiles")
        self.left_panel.frame.rowconfigure(9, weight=1)

        self.s_left = V4SectionHeader(self.left_panel.frame, "Yürütme Bağlamı")
        self.s_left.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 10))

        self.t_mission = V4StatusTile(self.left_panel.frame, "Görev", "—", MUTED)
        self.t_mission.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=3)
        self.t_phase = V4StatusTile(self.left_panel.frame, "Faz", "—", NEON_CYAN)
        self.t_phase.grid(row=2, column=0, sticky="ew", padx=(18, 8), pady=3)
        self.t_step = V4StatusTile(self.left_panel.frame, "Aktif Adım", "—", NEON_CYAN, value_color=TEXT_BRIGHT)
        self.t_step.grid(row=2, column=1, sticky="ew", padx=(8, 18), pady=3)
        self.t_action = V4StatusTile(self.left_panel.frame, "Eylem", "—", NEON_BLUE)
        self.t_action.grid(row=3, column=0, sticky="ew", padx=(18, 8), pady=3)
        self.t_obs = V4StatusTile(self.left_panel.frame, "Gözlem", "—", HOLO_GREEN)
        self.t_obs.grid(row=3, column=1, sticky="ew", padx=(8, 18), pady=3)
        self.t_verify = V4StatusTile(self.left_panel.frame, "Doğrulama", "—", HOLO_GREEN)
        self.t_verify.grid(row=4, column=0, sticky="ew", padx=(18, 8), pady=3)
        self.t_approval = V4StatusTile(self.left_panel.frame, "Onay", "—", NEON_GOLD)
        self.t_approval.grid(row=4, column=1, sticky="ew", padx=(8, 18), pady=3)
        self.t_recovery = V4StatusTile(self.left_panel.frame, "Kurtarma", "—", NEON_GOLD)
        self.t_recovery.grid(row=5, column=0, sticky="ew", padx=(18, 8), pady=3)
        self.t_error = V4StatusTile(self.left_panel.frame, "Hata", "—", NEON_MAGENTA)
        self.t_error.grid(row=5, column=1, sticky="ew", padx=(8, 18), pady=3)

        # actions row (approval/start buttons — informational hooks only)
        self.actions = ctk.CTkFrame(self.left_panel.frame, fg_color="transparent")
        self.actions.grid(row=6, column=0, columnspan=2, sticky="ew", padx=18, pady=(14, 10))
        self.actions.columnconfigure((0, 1, 2, 3), weight=1)

        self.btn_approve = ctk.CTkButton(
            self.actions,
            text="ONAYLA",
            height=36,
            fg_color=HOLO_GREEN,
            hover_color="#00cc88",
            text_color="#001a14",
            corner_radius=10,
            border_width=0,
            font=("Segoe UI", 11, "bold"),
            command=self._on_approve,
            state="disabled",
        )
        self.btn_approve.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.btn_deny = ctk.CTkButton(
            self.actions,
            text="REDDET",
            height=36,
            fg_color=NEON_MAGENTA,
            hover_color="#cc1555",
            text_color="#200010",
            corner_radius=10,
            border_width=0,
            font=("Segoe UI", 11, "bold"),
            command=self._on_deny,
            state="disabled",
        )
        self.btn_deny.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.btn_pause = ctk.CTkButton(
            self.actions,
            text="DURAKLAT",
            height=36,
            fg_color=BUTTON,
            hover_color=BUTTON_HOVER,
            text_color=NEON_GOLD,
            corner_radius=10,
            border_width=1,
            border_color=CARD_BORDER,
            font=("Segoe UI", 11, "bold"),
            command=self._on_pause,
            state="disabled",
        )
        self.btn_pause.grid(row=0, column=2, sticky="ew", padx=(0, 8))
        self.btn_reset = ctk.CTkButton(
            self.actions,
            text="SIFIRLA",
            height=36,
            fg_color=BUTTON,
            hover_color=BUTTON_HOVER,
            text_color=NEON_CYAN,
            corner_radius=10,
            border_width=1,
            border_color=CARD_BORDER,
            font=("Segoe UI", 11, "bold"),
            command=self._on_reset,
            state="disabled",
        )
        self.btn_reset.grid(row=0, column=3, sticky="ew")

        # right: recent activity
        self.s_right = V4SectionHeader(self.twocol, "Son Adım Aktivitesi")
        self.s_right.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=(0, 10))

        self.right_panel = V4HUDPanel(self.twocol)
        self.right_panel.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        self.right_panel.frame.rowconfigure(0, weight=1)
        self.right_panel.frame.columnconfigure(0, weight=1)

        self.act_scroll = ctk.CTkScrollableFrame(
            self.right_panel.frame,
            fg_color="transparent",
            scrollbar_button_color=CARD_BORDER,
        )
        self.act_scroll.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.act_scroll.columnconfigure(0, weight=1)

        self.act_placeholder: V4EmptyState | None = None

        self.refresh()

        try:
            self._sub = self.store.subscribe(lambda *_a, **_k: self._schedule_refresh())
        except Exception:
            self._sub = None

    # ── actions (integration hooks only — no duplicate engine) ─────────
    def _on_approve(self) -> None:  # pragma: no cover - integration hook
        pass

    def _on_deny(self) -> None:  # pragma: no cover - integration hook
        pass

    def _on_pause(self) -> None:  # pragma: no cover - integration hook
        pass

    def _on_reset(self) -> None:  # pragma: no cover - integration hook
        pass

    def _schedule_refresh(self) -> None:  # pragma: no cover
        try:
            token = self.root.after(60, self.refresh)
            self._timers.append(token)
        except Exception:
            pass

    def _set_big_phase(self, phase: str, snap: dict[str, Any]) -> None:
        title, sub, st = _PHASE_META.get(phase, (str(phase).upper(), "Çalışma zamanı durumu", status_color(str(phase), "active")))
        col = status_color(st)
        self.big_dot.configure(text_color=col)
        self.big_phase.configure(text=title, text_color=TEXT_BRIGHT if st != "danger" else NEON_MAGENTA)
        self.big_sub.configure(text=sub)
        approval = snap.get("approval_state") or "none"
        a_val, _ = _approval_detail(str(approval))
        if str(approval).lower() in {"required", "pending"}:
            if self.approval_banner is None or not self.approval_banner.winfo_exists():
                self.approval_banner = ctk.CTkFrame(
                    self.status_wrap.frame,
                    fg_color="#2a1a00",
                    corner_radius=10,
                    border_width=1,
                    border_color=NEON_GOLD,
                )
                self.approval_banner.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 14))
                ctk.CTkLabel(
                    self.approval_banner,
                    text=f"⚑ ONAY GEREKİYOR  ·  {a_val}",
                    text_color=NEON_GOLD,
                    font=("Segoe UI", 12, "bold"),
                    anchor="w",
                ).pack(fill="x", padx=14, pady=(8, 4))
                ctk.CTkLabel(
                    self.approval_banner,
                    text="Yüksek etkili bir eylem yürütmeden önce insan incelemesi bekliyor.",
                    text_color=TEXT,
                    font=FONT_HUD,
                    anchor="w",
                ).pack(fill="x", padx=14, pady=(0, 10))
        else:
            if self.approval_banner is not None:
                try:
                    self.approval_banner.destroy()
                except Exception:
                    pass
                self.approval_banner = None

    def refresh(self) -> None:
        snap: dict[str, Any] = {}
        events: list[Any] = []
        try:
            snap = self.store.get_snapshot() or {}
            events = list(getattr(self.store, "_events", []) or [])
        except Exception:
            snap = {}
            events = []

        phase = str(snap.get("phase") or "idle")
        approval = str(snap.get("approval_state") or "none")
        verify = str(snap.get("verification_state") or "none")
        recover = str(snap.get("recovery_state") or "none")
        err = snap.get("error_state") or None

        self.header.set_status(phase)
        self._set_big_phase(phase, snap)

        # mission title: honest not available from snap → "—" unless events provide last task title
        mission_title = "—"
        for ev in reversed(events):
            payload = getattr(ev, "payload", None) or {}
            t = payload.get("mission") or payload.get("task") or payload.get("goal") or payload.get("title")
            if t:
                mission_title = str(t)[:120]
                break
        self.t_mission.set(mission_title, dot=MUTED if mission_title == "—" else NEON_CYAN)

        ptitle, _, pst = _PHASE_META.get(phase, (_phase_to_tr(phase), "Aktif", "active"))
        self.t_phase.set(ptitle, dot=status_color(pst))

        last_step = "—"
        last_action = "—"
        last_obs = "—"
        for ev in reversed(events[-16:]):
            kind = (getattr(ev, "kind", "") or "").lower()
            payload = getattr(ev, "payload", None) or {}
            if last_step == "—" and (
                "step" in kind or "plan" in kind or "reason" in kind or "task" in kind
            ):
                v = payload.get("step") or payload.get("phase") or payload.get("title") or getattr(ev, "kind", None)
                if v:
                    last_step = str(v)[:120]
            if last_action == "—" and ("action" in kind or "tool" in kind or "execut" in kind):
                v = payload.get("action") or payload.get("tool") or payload.get("command") or getattr(ev, "kind", None)
                if v:
                    last_action = str(v)[:120]
            if last_obs == "—" and ("observation" in kind or "screen" in kind or "output" in kind):
                v = payload.get("observation") or payload.get("output") or payload.get("result")
                if v:
                    last_obs = str(v)[:120]
            if "—" not in (last_step, last_action, last_obs):
                break

        self.t_step.set(last_step, dot=status_color("active" if last_step != "—" else "unknown"))
        self.t_action.set(last_action, dot=NEON_BLUE if last_action != "—" else MUTED, value_color=TEXT_BRIGHT if last_action != "—" else TEXT)
        self.t_obs.set(last_obs, dot=HOLO_GREEN if last_obs != "—" else MUTED)

        v_val, v_col = _verify_detail(verify)
        self.t_verify.set(v_val, dot=v_col)
        a_val, a_col = _approval_detail(approval)
        self.t_approval.set(a_val, dot=a_col)
        r_val, r_col = _recover_detail(recover)
        self.t_recovery.set(r_val, dot=r_col)
        if err:
            self.t_error.set(str(err)[:140], dot=NEON_MAGENTA, value_color=NEON_MAGENTA)
        else:
            self.t_error.set("YOK", dot=MUTED, value_color=TEXT)

        # enable buttons based on state
        approval_required = str(approval).lower() in {"required", "pending"}
        try:
            self.btn_approve.configure(state="normal" if approval_required else "disabled")
            self.btn_deny.configure(state="normal" if approval_required else "disabled")
            running = phase not in {"idle", "completed", "failed"}
            self.btn_pause.configure(state="normal" if running else "disabled")
            self.btn_reset.configure(state="normal" if phase != "idle" else "disabled")
        except Exception:  # pragma: no cover
            pass

        # right side: timeline last 12
        for w in self.act_scroll.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.act_placeholder = None

        if not events:
            self.act_placeholder = V4EmptyState(
                self.act_scroll,
                "Son aktivite yok",
                subtitle="Muvahhid yürüttükçe adım, eylem ve gözlem olayları burada akacaktır.",
                dot=MUTED,
            )
            self.act_placeholder.pack(fill="x", padx=8, pady=8)
            return

        max_show = min(14, len(events))
        last_events = list(events[-max_show:])
        for idx, ev in enumerate(last_events):
            kind = (getattr(ev, "kind", "") or "").lower()
            payload = getattr(ev, "payload", None) or {}
            title = str(
                payload.get("title")
                or payload.get("step")
                or payload.get("action")
                or payload.get("tool")
                or kind
                or "Olay"
            ).strip() or "Olay"
            detail_parts: list[str] = []
            for key in ("message", "detail", "observation", "result", "target", "task"):
                v = payload.get(key)
                if v:
                    detail_parts.append(str(v)[:240])
                    break
            detail = "\n".join(detail_parts)
            color = status_color(kind, NEON_CYAN)
            cat = (kind.split("_")[0] if kind else "event") or "event"
            show_connector = idx < (len(last_events) - 1)
            tle = V4TimelineEvent(
                self.act_scroll,
                title=title[:180],
                detail=detail,
                timestamp=_fmt_ts(getattr(ev, "timestamp", None)),
                color=color,
                category=cat[:10],
                show_connector=show_connector,
            )
            tle.pack(fill="x", padx=2, pady=(0, 2))

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
