from __future__ import annotations

from typing import Any

from hermes.ui.modern_theme import (
    CARD,
    CARD_BORDER,
    FONT_HUD,
    HOLO_GREEN,
    MUTED,
    NEON_BLUE,
    NEON_CYAN,
    NEON_GOLD,
    NEON_MAGENTA,
    ORANGE,
    TEXT,
    TEXT_BRIGHT,
)
from hermes.ui.v4_design import (
    V4Card,
    V4EmptyState,
    V4HUDPanel,
    V4PageHeader,
    V4SectionHeader,
    V4StatusBadge,
    V4StatusTile,
    _phase_to_tr,
    status_color,
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover
    ctk = None  # type: ignore


_STATE_DEFS = [
    ("GÖZLEM", NEON_CYAN, "Mevcut sayfa durumu okunuyor."),
    ("GEZİNME", NEON_BLUE, "Sayfalar veya URL'ler arasında hareket."),
    ("EYLEM", ORANGE, "Tıklama, yazma veya tarayıcı eylemi yürütme."),
    ("BEKLEME", MUTED, "Sayfa yükleme, olay veya kullanıcı sinyali bekleniyor."),
    ("TAMAMLANDI", HOLO_GREEN, "Tarayıcı hedefi başarıyla tamamlandı."),
    ("BAŞARISIZ", NEON_MAGENTA, "Tarayıcı otomasyonu hatayla durduruldu."),
]

_FIELDS = [
    ("Aktif Sayfa", "BİLDİRİLMİYOR", NEON_CYAN),
    ("URL", "BİLDİRİLMİYOR", NEON_BLUE),
    ("Başlık", "BİLDİRİLMİYOR", TEXT_BRIGHT),
    ("Aktif Sekme", "BİLDİRİLMİYOR", MUTED),
    ("Sekme Sayısı", "BİLDİRİLMİYOR", MUTED),
    ("Varlık Ekran Durumu", "BİLDİRİLMİYOR", MUTED),
    ("Seçici Önbelleği", "BİLDİRİLMİYOR", MUTED),
    ("Otomasyon Sürücüsü", "BİLDİRİLMİYOR", MUTED),
]


class V4Browser:
    def __init__(self, parent: Any, store: Any) -> None:
        self.store = store
        self._sub: Any = None
        self._timers: list[str] = []

        if ctk is None:  # pragma: no cover
            raise RuntimeError("customtkinter unavailable")

        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.widget = self.root
        self.root.pack(fill="both", expand=True, padx=18, pady=14)
        self.root.rowconfigure(3, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.header = V4PageHeader(
            self.root,
            "TARAYICI",
            subtitle="Tarayıcı otomasyon kontrol merkezi. Durum dürüst: sözleşme eksik yerde BİLDİRİLMİYOR.",
            status="unknown",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # Summary tiles
        self.summary = V4HUDPanel(self.root)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.summary.frame.columnconfigure((0, 1, 2, 3), weight=1)

        self.t_state = V4StatusTile(self.summary.frame, "Durum", "BİLİNMİYOR", dot=MUTED, value_color=TEXT_BRIGHT)
        self.t_state.grid(row=0, column=0, sticky="ew", padx=(18, 8), pady=16)
        self.t_last = V4StatusTile(self.summary.frame, "Son Olay", "BİLDİRİLMİYOR", dot=MUTED)
        self.t_last.grid(row=0, column=1, sticky="ew", padx=8, pady=16)
        self.t_session = V4StatusTile(self.summary.frame, "Oturum", "BİLDİRİLMİYOR", dot=MUTED)
        self.t_session.grid(row=0, column=2, sticky="ew", padx=8, pady=16)
        self.t_driver = V4StatusTile(self.summary.frame, "Sürücü", "BAĞLANMADI", dot=MUTED)
        self.t_driver.grid(row=0, column=3, sticky="ew", padx=(8, 18), pady=16)

        # State legend
        self.states = V4HUDPanel(self.root)
        self.states.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        header2 = V4SectionHeader(self.states.frame, "Tarayıcı Durumları")
        header2.grid(row=0, column=0, columnspan=6, sticky="ew", padx=14, pady=(14, 10))

        cols = len(_STATE_DEFS)
        for i, (name, color, desc) in enumerate(_STATE_DEFS):
            cell = V4Card(self.states.frame)
            cell.frame.grid(row=1, column=i, sticky="nsew", padx=(8 if i > 0 else 14, 14 if i == cols - 1 else 8), pady=(0, 14))
            self.states.frame.columnconfigure(i, weight=1)
            wrap = cell.frame
            V4StatusBadge(wrap, name, color=color).pack(anchor="w", padx=12, pady=(12, 6))
            ctk.CTkLabel(
                wrap,
                text=desc,
                text_color=TEXT,
                font=FONT_HUD,
                anchor="w",
                wraplength=140,
                justify="left",
            ).pack(fill="x", padx=12, pady=(0, 12))

        # Main context: 2-col field grid + empty state panel
        self.main = V4HUDPanel(self.root)
        self.main.grid(row=3, column=0, sticky="nsew")
        self.main.frame.rowconfigure(1, weight=1)
        self.main.frame.columnconfigure((0, 1), weight=1, uniform="br2c")
        sec1 = V4SectionHeader(self.main.frame, "Tarayıcı Bağlamı")
        sec1.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(14, 6))

        self.fields_wrap = ctk.CTkFrame(self.main.frame, fg_color="transparent")
        self.fields_wrap.grid(row=1, column=0, sticky="nsew", padx=(14, 8), pady=(0, 14))
        self.fields_wrap.columnconfigure(0, weight=1)
        self.tiles: dict[str, V4StatusTile] = {}
        for i, (label, val, col) in enumerate(_FIELDS):
            tile = V4StatusTile(self.fields_wrap, label, val, dot=MUTED, value_color=col)
            tile.grid(row=i, column=0, sticky="ew", pady=3)
            self.tiles[label] = tile

        self.right = ctk.CTkFrame(self.main.frame, fg_color="transparent")
        self.right.grid(row=1, column=1, sticky="nsew", padx=(8, 14), pady=(0, 14))
        self.right.rowconfigure(0, weight=1)
        self.right.columnconfigure(0, weight=1)
        self.empty = V4EmptyState(
            self.right,
            "Tarayıcı durumu bildirilmiyor",
            subtitle="Mevcut V4 anlık görüntü sözleşmesi (7 kanonik alan) üzerinden tarayıcı otomasyon durumu sunulmuyor. Tarayıcı köprüsü aktarım/sayfa durumunu yayınladığında burada görünecektir. SAHTE URL/sekme verisi ASLA sentezlenmez.",
            dot=MUTED,
        )
        self.empty.grid(row=0, column=0, sticky="nsew")

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
            token = self.root.after(120, self.refresh)
            self._timers.append(token)
        except Exception:
            pass

    def refresh(self) -> None:
        snap: dict[str, Any] = {}
        events: list[Any] = []
        try:
            snap = self.store.get_snapshot() or {}
            events = list(getattr(self.store, "_events", []) or [])
        except Exception:
            snap = {}
            events = []

        # Derive only from store snapshot event_count (honest) — do NOT invent browser state.
        count = snap.get("event_count") or 0
        self.header.set_status("active" if count else "unknown",
                              f"OLAYLAR · {count}" if count else "TARAYICI OLAYI YOK")
        phase = snap.get("phase") or "unknown"
        # Browser state still not reported; leave UNKNOWN, but derive last event kind from last 8 event types (honest)
        last_browser_kind: str | None = None
        for ev in reversed(events[-12:]):
            k = (getattr(ev, "kind", "") or "").lower()
            if any(tok in k for tok in ("browser", "navigate", "page", "click", "scroll", "type", "tab")):
                last_browser_kind = k.upper()
                break
        if last_browser_kind:
            self.t_last.set(last_browser_kind, dot=status_color("active"))
            # Update tile text in fields also:
            try:
                if "Son Olay" in self.tiles:
                    self.tiles["Son Olay"]  # placeholder noop
            except Exception:
                pass
        # All fields honest not-reported unless we found real data: fields remain as-is.

        # Leave state as UNKNOWN (never invent state from count)
        self.t_state.set("BİLİNMİYOR", dot=MUTED)

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
