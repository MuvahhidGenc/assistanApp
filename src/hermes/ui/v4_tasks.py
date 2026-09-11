from __future__ import annotations

from datetime import datetime
from typing import Any

from hermes.ui.modern_theme import (
    CARD,
    CARD_BORDER,
    FONT_HUD,
    FONT_MONO,
    HOLO_GREEN,
    MUTED,
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
    status_color,
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover
    ctk = None  # type: ignore


_GROUPS = [
    ("AKTİF", NEON_CYAN),
    ("SON", "#7388a4"),
    ("TAMAMLANDI", HOLO_GREEN),
    ("İPTAL EDİLDİ", MUTED),
    ("BAŞARISIZ", NEON_MAGENTA),
]


def _fmt_ts(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)[:19]


class _TaskView:
    def __init__(self, task_id: str, group: str) -> None:
        self.task_id = task_id
        self.group = group
        self.title = task_id
        self.phase: str | None = None
        self.outcome: str | None = None
        self.first_ts: Any = None
        self.last_ts: Any = None
        self.count = 0


def _classify_group(last_kind: str, last_payload: dict[str, Any]) -> str:
    k = (last_kind or "").lower()
    if "complete" in k or str(last_payload.get("result")).lower() in {"ok", "success", "pass"}:
        return "TAMAMLANDI"
    if "cancel" in k:
        return "İPTAL EDİLDİ"
    if "fail" in k or "error" in k or str(last_payload.get("error")).lower() not in {"", "none"}:
        return "BAŞARISIZ"
    if "executing" in k or "step" in k or "action" in k:
        return "AKTİF"
    return "SON"


class V4Tasks:
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

        self.header = V4PageHeader(
            self.root,
            "GÖREVLER",
            subtitle="Kanonik olay akışından türetilen görev geçmişi.",
            status="idle",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # Summary row
        self.summary = V4HUDPanel(self.root)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.summary.frame.columnconfigure((0, 1, 2, 3, 4, 5), weight=1)
        self.summary_tiles: dict[str, V4StatusTile] = {}
        for i, (name, col) in enumerate(_GROUPS):
            t = V4StatusTile(self.summary.frame, name, "0", dot=col)
            t.grid(row=0, column=i, sticky="ew", padx=(18 if i == 0 else 8, 8 if i < 4 else 18), pady=16)
            self.summary_tiles[name] = t
        self.total_tile = V4StatusTile(self.summary.frame, "Olaylar", "0", dot=MUTED)
        self.total_tile.grid(row=0, column=5, sticky="ew", padx=(8, 18), pady=16)

        # Scrollable stacked sections
        self.scroll = ctk.CTkScrollableFrame(
            self.root,
            fg_color="transparent",
            scrollbar_button_color=CARD_BORDER,
        )
        self.scroll.grid(row=2, column=0, sticky="nsew")
        self.scroll.columnconfigure(0, weight=1)

        self.group_wraps: dict[str, Any] = {}
        for name, accent in _GROUPS:
            V4SectionHeader(self.scroll, name, accent=accent).pack(fill="x", padx=2, pady=(0, 8))
            wrap = ctk.CTkFrame(self.scroll, fg_color="transparent")
            wrap.pack(fill="x", padx=2, pady=(0, 14))
            wrap.columnconfigure(0, weight=1)
            self.group_wraps[name] = wrap

        try:
            self.refresh()
        except Exception:
            pass

        try:
            self._sub = self.store.subscribe(lambda *_a, **_k: self._schedule_refresh())
        except Exception:
            self._sub = None

    def _schedule_refresh(self) -> None:  # pragma: no cover
        try:
            token = self.root.after(100, self.refresh)
            self._timers.append(token)
        except Exception:
            pass

    def _clear_group(self, name: str) -> None:
        wrap = self.group_wraps[name]
        for w in wrap.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    def _build_groups_honest(self) -> dict[str, list[_TaskView]]:
        events: list[Any] = []
        try:
            events = list(getattr(self.store, "_events", []) or [])
        except Exception:
            events = []

        buckets: dict[str, list[_TaskView]] = {g: [] for g, _ in _GROUPS}
        if not events:
            return buckets

        by_task: dict[str, _TaskView] = {}
        for ev in events:
            tid = (
                str(getattr(ev, "task_id", None) or "")
                or str(getattr(ev, "correlation_id", None) or "")
                or str(getattr(ev, "event_id", None) or "")
                or None
            )
            if not tid:
                continue
            if tid not in by_task:
                by_task[tid] = _TaskView(tid, "SON")
            tv = by_task[tid]
            tv.count += 1
            ts = getattr(ev, "timestamp", None)
            if tv.first_ts is None or (ts and ts < tv.first_ts):
                tv.first_ts = ts
            if tv.last_ts is None or (ts and ts > tv.last_ts):
                tv.last_ts = ts
            payload: dict[str, Any] = getattr(ev, "payload", None) or {}
            k = (getattr(ev, "kind", "") or "").lower()
            if "task" in k or "mission" in k or "goal" in k:
                t = payload.get("title") or payload.get("task") or payload.get("mission") or payload.get("goal")
                if t:
                    tv.title = str(t)[:160]
                p = payload.get("phase")
                if p:
                    tv.phase = str(p)
                tv.group = _classify_group(k, payload)
            else:
                # update group conservatively
                cand = _classify_group(k, payload)
                if cand in {"BAŞARISIZ", "TAMAMLANDI", "İPTAL EDİLDİ", "AKTİF"}:
                    if tv.group == "SON":
                        tv.group = cand
                    elif tv.group == "AKTİF" and cand != "AKTİF":
                        tv.group = cand

        # If AKTİF has more than 1 entry: pick newest as AKTİF, rest SON
        current_list = [tv for tv in by_task.values() if tv.group == "AKTİF"]
        if len(current_list) > 1:
            current_list.sort(key=lambda t: t.last_ts or "")
            for tv in current_list[:-1]:
                tv.group = "SON"

        for tv in by_task.values():
            g = tv.group
            if g not in buckets:
                g = "SON"
            buckets[g].append(tv)
        for g in buckets:
            buckets[g].sort(key=lambda t: t.last_ts or "", reverse=True)
        # max 15 recent; limit per bucket to avoid huge list
        buckets["SON"] = buckets["SON"][:10]
        buckets["TAMAMLANDI"] = buckets["TAMAMLANDI"][:20]
        buckets["BAŞARISIZ"] = buckets["BAŞARISIZ"][:20]
        buckets["İPTAL EDİLDİ"] = buckets["İPTAL EDİLDİ"][:20]
        return buckets

    def _render_task_card(self, parent: Any, tv: _TaskView, accent: str) -> None:
        card = V4Card(parent)
        card.pack(fill="x", pady=(0, 8))
        card.frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card.frame,
            text="◎",
            text_color=accent,
            font=("Segoe UI", 16),
        ).grid(row=0, column=0, rowspan=2, padx=(14, 10), pady=12, sticky="n")

        head = ctk.CTkFrame(card.frame, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(12, 4))
        head.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            head,
            text=tv.title[:160] or "(başlıksız görev)",
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        V4StatusBadge(head, tv.group, color=accent).grid(row=0, column=1, sticky="e")

        meta = ctk.CTkFrame(card.frame, fg_color="transparent")
        meta.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=(0, 12))
        meta.columnconfigure((0, 1, 2, 3), weight=1)
        id_lbl = ctk.CTkLabel(
            meta,
            text=f"id: {tv.task_id[:40]}",
            text_color=MUTED,
            font=FONT_MONO,
            anchor="w",
            justify="left",
        )
        id_lbl.grid(row=0, column=0, columnspan=2, sticky="we")

        parts: list[tuple[str, str]] = []
        if tv.phase:
            parts.append(("FAZ", str(tv.phase)[:20]))
        parts.append(("OLAYLAR", str(tv.count)))
        if tv.first_ts:
            parts.append(("BAŞLANGIÇ", _fmt_ts(tv.first_ts)[:19]))
        if tv.last_ts and tv.last_ts != tv.first_ts:
            parts.append(("SON", _fmt_ts(tv.last_ts)[:19]))
        for i, (k, v) in enumerate(parts[:4]):
            col = 2 + i if i < 2 else i
            ctk.CTkLabel(meta, text=f"{k}: {v}", text_color=MUTED, font=FONT_HUD,
                         anchor="w").grid(row=1, column=min(i, 3), sticky="we", pady=(2, 0))

    def refresh(self) -> None:
        buckets = self._build_groups_honest()
        total_count = 0
        for name, _accent in _GROUPS:
            size = len(buckets[name])
            total_count += size
            self.summary_tiles[name].set(str(size))
            self._clear_group(name)
            if size == 0:
                if name == "AKTİF":
                    empty = V4EmptyState(
                        self.group_wraps[name],
                        title="Aktif görev yok",
                        subtitle="Muvahhid bir sonraki komutunuz için hazır.",
                        dot=MUTED,
                    )
                elif name == "SON":
                    empty = V4EmptyState(
                        self.group_wraps[name],
                        title="Son görev yok",
                        subtitle="Görevler yürütüldükçe görev geçmişi burada görünecektir.",
                        dot=MUTED,
                    )
                elif name == "TAMAMLANDI":
                    empty = V4EmptyState(
                        self.group_wraps[name],
                        title="Tamamlanan görev yok",
                        subtitle="Tamamlanan görevler burada kaydedilecektir.",
                        dot=MUTED,
                    )
                elif name == "İPTAL EDİLDİ":
                    empty = V4EmptyState(
                        self.group_wraps[name],
                        title="İptal edilen görev yok",
                        subtitle="İptal edilen veya yarım bırakılan görevler burada görünür.",
                        dot=MUTED,
                    )
                elif name == "BAŞARISIZ":
                    empty = V4EmptyState(
                        self.group_wraps[name],
                        title="Başarısız görev yok",
                        subtitle="Hata sonucu başarısız olan görevler denetim için burada görünür.",
                        dot=MUTED,
                    )
                else:
                    empty = V4EmptyState(self.group_wraps[name], "—", "", dot=MUTED)
                empty.pack(fill="x", padx=4, pady=4)
            else:
                accent = dict(_GROUPS)[name]
                for tv in buckets[name]:
                    self._render_task_card(self.group_wraps[name], tv, accent)
        total_events = 0
        try:
            snap = self.store.get_snapshot() or {}
            total_events = snap.get("event_count") or 0
        except Exception:
            total_events = 0
        self.total_tile.set(str(total_events))
        self.header.set_status("active" if total_events or total_count else "idle",
                              f"OLAYLAR · {total_events}" if total_events else "GÖREV YOK")

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
