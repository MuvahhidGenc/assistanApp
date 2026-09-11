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
    status_color,
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover
    ctk = None  # type: ignore


_CATEGORIES: list[tuple[str, str, str]] = [
    ("TÜMÜ", "Tüm olaylar", MUTED),
    ("AKIL YÜRÜTME", "planlama_adımı, akıl_yürütme_başladı, akıl_yürütme_sonucu", NEON_CYAN),
    ("EYLEM", "eylem_başladı, eylem_bitti, araç_çağrıldı", NEON_BLUE),
    ("GÖZLEM", "gözlem_kaydedildi, ekran_kaydedildi, çıktı", HOLO_GREEN),
    ("DOĞRULAMA", "doğrulama_başladı, doğrulama_sonucu", HOLO_GREEN),
    ("ONAY", "onay_istendi, onay_verildi, onay_reddi", NEON_GOLD),
    ("KURTARMA", "kurtarma_başladı, kurtarma_bitti", NEON_GOLD),
    ("BAŞARI", "görev_tamamlandı, adım_bitti başarılı", HOLO_GREEN),
    ("BAŞARISIZLIK", "görev_başarısız, hata_durumu, durdurma", NEON_MAGENTA),
]


def _fmt_ts(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%H:%M:%S")
    except Exception:
        return str(ts)[:8]


def _full_ts(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)[:19]


def _category_match(category: str, kind: str) -> bool:
    k = (kind or "").lower()
    if category == "TÜMÜ":
        return True
    c = category.lower()
    if c.startswith("akıl"):
        return any(tok in k for tok in ("reason", "plan", "think", "intent"))
    if c.startswith("eylem"):
        return any(tok in k for tok in ("action", "tool", "execut", "command", "step"))
    if c.startswith("gözlem"):
        return any(tok in k for tok in ("observ", "screen", "output", "result", "state"))
    if c.startswith("doğrulama"):
        return "verif" in k
    if c.startswith("onay"):
        return "approv" in k or "policy" in k
    if c.startswith("kurtarma"):
        return "recover" in k or "repair" in k
    if c.startswith("başarı"):
        return any(tok in k for tok in ("complete", "pass", "success", "finish_ok", "succeeded"))
    if c.startswith("başarısız"):
        return any(tok in k for tok in ("fail", "error", "halt", "exception", "denied"))
    return False


class V4Activity:
    def __init__(self, parent: Any, store: Any) -> None:
        self.store = store
        self._sub: Any = None
        self._timers: list[str] = []
        self._active_category: str = "TÜMÜ"

        if ctk is None:  # pragma: no cover
            raise RuntimeError("customtkinter unavailable")

        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.widget = self.root
        self.root.pack(fill="both", expand=True, padx=18, pady=14)
        self.root.rowconfigure(3, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.header = V4PageHeader(
            self.root,
            "AKTİVİTE",
            subtitle="Kanonik olay deposundan tam yürütme / gözlemlenebilirlik akışı.",
            status="idle",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # Summary
        self.summary = V4HUDPanel(self.root)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.summary.frame.columnconfigure((0, 1, 2, 3, 4, 5), weight=1)
        self.t_count = V4StatusTile(self.summary.frame, "Olaylar", "0", dot=NEON_CYAN)
        self.t_count.grid(row=0, column=0, sticky="ew", padx=(18, 8), pady=16)
        self.t_reason = V4StatusTile(self.summary.frame, "Akıl", "0", dot=NEON_CYAN)
        self.t_reason.grid(row=0, column=1, sticky="ew", padx=8, pady=16)
        self.t_action = V4StatusTile(self.summary.frame, "Eylemler", "0", dot=NEON_BLUE)
        self.t_action.grid(row=0, column=2, sticky="ew", padx=8, pady=16)
        self.t_verify = V4StatusTile(self.summary.frame, "Doğrulama", "0", dot=HOLO_GREEN)
        self.t_verify.grid(row=0, column=3, sticky="ew", padx=8, pady=16)
        self.t_app = V4StatusTile(self.summary.frame, "Onaylar", "0", dot=NEON_GOLD)
        self.t_app.grid(row=0, column=4, sticky="ew", padx=8, pady=16)
        self.t_err = V4StatusTile(self.summary.frame, "Hatalar", "0", dot=NEON_MAGENTA)
        self.t_err.grid(row=0, column=5, sticky="ew", padx=(8, 18), pady=16)

        # Filter chips
        self.filters = V4Card(self.root)
        self.filters.frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.filter_row = ctk.CTkFrame(self.filters.frame, fg_color="transparent")
        self.filter_row.pack(fill="x", padx=10, pady=10)
        self.chip_buttons: dict[str, ctk.CTkButton] = {}
        for name, _tip, col in _CATEGORIES:
            btn = ctk.CTkButton(
                self.filter_row,
                text=f"  {name}  ",
                width=1,
                height=30,
                fg_color=CARD if name != "TÜMÜ" else col,
                hover_color=BUTTON_HOVER,
                text_color="#000a10" if name == "TÜMÜ" else col,
                border_width=1,
                border_color=col,
                corner_radius=999,
                font=("Consolas", 10, "bold"),
                command=lambda n=name: self._set_category(n),
            )
            btn.pack(side="left", padx=3)
            self.chip_buttons[name] = btn
        self.chip_buttons["TÜMÜ"].configure(fg_color=NEON_CYAN, text_color="#000a10")  # selected default

        # Stream scrollable panel + header
        self.stream = V4HUDPanel(self.root)
        self.stream.grid(row=3, column=0, sticky="nsew")
        self.stream.frame.rowconfigure(1, weight=1)
        self.stream.frame.columnconfigure(0, weight=1)
        self.stream_sec = V4SectionHeader(self.stream.frame, "Olay Akışı")
        self.stream_sec.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))

        self.stream_scroll = ctk.CTkScrollableFrame(
            self.stream.frame,
            fg_color="transparent",
            scrollbar_button_color=CARD_BORDER,
        )
        self.stream_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 10))
        self.stream_scroll.columnconfigure(0, weight=1)

        self.stream_empty: V4EmptyState | None = None

        self.refresh()

        try:
            self._sub = self.store.subscribe(lambda *_a, **_k: self._schedule_refresh())
        except Exception:
            self._sub = None

    def _schedule_refresh(self) -> None:  # pragma: no cover
        try:
            token = self.root.after(60, self.refresh)
            self._timers.append(token)
        except Exception:
            pass

    def _set_category(self, name: str) -> None:
        self._active_category = name
        for n, btn in self.chip_buttons.items():
            col = [c for _n, _t, c in _CATEGORIES if _n == n][0]
            if n == name:
                btn.configure(fg_color=col, text_color="#000a10")
            else:
                btn.configure(fg_color=CARD, text_color=col)
        self.refresh()

    def _clear_stream(self) -> None:
        for w in self.stream_scroll.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self.stream_empty = None

    def _render_event(self, ev: Any, show_connector: bool) -> None:
        kind = (getattr(ev, "kind", "") or "").lower()
        payload: dict[str, Any] = getattr(ev, "payload", None) or {}
        title = str(
            payload.get("title")
            or payload.get("step")
            or payload.get("action")
            or payload.get("tool")
            or kind
            or "Olay"
        ).strip() or "Olay"

        detail_parts: list[str] = []
        for key in ("message", "detail", "observation", "result", "error", "target", "command", "output"):
            v = payload.get(key)
            if v is not None and str(v).strip():
                detail_parts.append(str(v)[:360])
                break
        extra_meta: list[str] = []
        eid = getattr(ev, "event_id", None)
        if eid:
            extra_meta.append(f"event_id: {eid}")
        corr = getattr(ev, "correlation_id", None)
        if corr:
            extra_meta.append(f"correlation_id: {corr}")
        tid = getattr(ev, "task_id", None)
        if tid:
            extra_meta.append(f"task_id: {tid}")
        aid = getattr(ev, "action_id", None)
        if aid:
            extra_meta.append(f"action_id: {aid}")
        if extra_meta:
            detail_parts.append("  ·  ".join(extra_meta))
        detail = "\n".join(detail_parts)

        color = status_color(kind, NEON_CYAN)
        cat_label = (kind.split("_")[0] if kind else "event") or "event"
        tle = V4TimelineEvent(
            self.stream_scroll,
            title=title[:200],
            detail=detail,
            timestamp=_fmt_ts(getattr(ev, "timestamp", None)),
            color=color,
            category=cat_label[:12].upper(),
            show_connector=show_connector,
        )
        tle.pack(fill="x", padx=4, pady=(0, 2))

        # full timestamp detail line under detail (secondary)
        fts = _full_ts(getattr(ev, "timestamp", None))
        if fts and detail:
            ctk.CTkLabel(
                tle.frame,
                text=fts,
                text_color=MUTED,
                font=FONT_MONO,
                anchor="w",
            ).grid(row=2, column=1, sticky="we", padx=(0, 4), pady=(0, 6))

    def refresh(self) -> None:
        snap: dict[str, Any] = {}
        events: list[Any] = []
        try:
            snap = self.store.get_snapshot() or {}
            events = list(getattr(self.store, "_events", []) or [])
        except Exception:
            snap = {}
            events = []

        total = len(events)
        c_reason = sum(1 for e in events if _category_match("AKIL YÜRÜTME", str(getattr(e, "kind", "") or "")))
        c_action = sum(1 for e in events if _category_match("EYLEM", str(getattr(e, "kind", "") or "")))
        c_verify = sum(1 for e in events if _category_match("DOĞRULAMA", str(getattr(e, "kind", "") or "")))
        c_app = sum(1 for e in events if _category_match("ONAY", str(getattr(e, "kind", "") or "")))
        c_fail = sum(1 for e in events if _category_match("BAŞARISIZLIK", str(getattr(e, "kind", "") or "")))

        self.t_count.set(str(total), dot=NEON_CYAN if total else MUTED)
        self.t_reason.set(str(c_reason), dot=NEON_CYAN if c_reason else MUTED)
        self.t_action.set(str(c_action), dot=NEON_BLUE if c_action else MUTED)
        self.t_verify.set(str(c_verify), dot=HOLO_GREEN if c_verify else MUTED)
        self.t_app.set(str(c_app), dot=NEON_GOLD if c_app else MUTED)
        self.t_err.set(str(c_fail), dot=NEON_MAGENTA if c_fail else MUTED)
        if total > 0:
            self.header.set_status("active", f"OLAYLAR · {total}")
        else:
            self.header.set_status("idle", "OLAY YOK")

        filtered = [e for e in events if _category_match(self._active_category, str(getattr(e, "kind", "") or ""))]
        self._clear_stream()
        if not filtered:
            if total == 0:
                self.stream_empty = V4EmptyState(
                    self.stream_scroll,
                    "Henüz aktivite yok",
                    subtitle="Muvahhid akıl yürüttükçe, eylemde bulundukça ve gözlemledikçe yürütme olayları burada görünecektir. Ayrıntılı izleme için geliştirici modunu etkinleştirin.",
                    dot=MUTED,
                )
            else:
                self.stream_empty = V4EmptyState(
                    self.stream_scroll,
                    f"{self._active_category.lower()} olayı yok",
                    subtitle=f"Depoda {total} olay var ancak hiçbiri {self._active_category} filtresiyle eşleşmiyor.",
                    dot=MUTED,
                )
            self.stream_empty.pack(fill="x", padx=8, pady=8)
            return
        # cap to last 200 max to avoid performance issues
        show = filtered[-200:]
        for i, ev in enumerate(show):
            self._render_event(ev, show_connector=(i < len(show) - 1))

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
