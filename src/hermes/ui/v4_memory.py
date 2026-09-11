from __future__ import annotations

from typing import Any

from hermes.ui.modern_theme import (
    CARD,
    CARD_BORDER,
    HOLO_GREEN,
    MUTED,
    NEON_CYAN,
    NEON_GOLD,
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
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover
    ctk = None  # type: ignore


_GROUP_META = {
    "OTURUM": {
        "accent": NEON_CYAN,
        "desc": "Etkin oturum için kısa ömürlü konuşma bağlamı.",
        "policy": "Denetlendi · Bellek Politikası uygulandı · doğrudan UI listesi yok",
    },
    "BÖLÜMSEL": {
        "accent": HOLO_GREEN,
        "desc": "Zaman damgaları ile saklanan yapılandırılmış bölümler (görevler, konuşmalar, görevler).",
        "policy": "episode_id ile indekslendi · Erişim kontrollü",
    },
    "UZUN SÜRELİ": {
        "accent": NEON_GOLD,
        "desc": "Oturumlar arasında kalıcı tutulan bilgiler (kullanıcı tercihleri, öğrenilen gerçekler).",
        "policy": "Kalıcılıktan önce gizliler temizlenir",
    },
    "BAĞLAM": {
        "accent": "#7388a4",
        "desc": "Uçuş sırasında enjekte edilen bağlam: araç çıktıları, ekran gözlemleri, planlama notları.",
        "policy": "V3 çalışma zamanı belleğinde yaşar · Doğrudan listelenmez",
    },
}


class V4Memory:
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
            "HAFIZA",
            subtitle="Bellek kontrol merkezi — bağlam çalışma zamanı ilkesi tarafından denetlenir.",
            status="idle",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        # top summary
        self.summary = V4HUDPanel(self.root)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.summary.frame.columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.t_session = V4StatusTile(self.summary.frame, "Oturum", "POLİTİKA KAPILI", dot=NEON_CYAN)
        self.t_session.grid(row=0, column=0, sticky="ew", padx=(18, 8), pady=16)
        self.t_episodic = V4StatusTile(self.summary.frame, "Bölümsel", "POLİTİKA KAPILI", dot=HOLO_GREEN)
        self.t_episodic.grid(row=0, column=1, sticky="ew", padx=8, pady=16)
        self.t_longterm = V4StatusTile(self.summary.frame, "Uzun Süreli", "POLİTİKA KAPILI", dot=NEON_GOLD)
        self.t_longterm.grid(row=0, column=2, sticky="ew", padx=8, pady=16)
        self.t_context = V4StatusTile(self.summary.frame, "Bağlam", "ÇALIŞMA ZAMANINDA", dot=MUTED)
        self.t_context.grid(row=0, column=3, sticky="ew", padx=8, pady=16)
        self.t_secrets = V4StatusTile(self.summary.frame, "Gizliler", "HER ZAMAN GİZLİ", dot="#ff2a6d", value_color="#ff2a6d")
        self.t_secrets.grid(row=0, column=4, sticky="ew", padx=(8, 18), pady=16)

        # 2x2 grid = 4 groups (each group: header + description + policy + empty state)
        self.grid_wrap = ctk.CTkFrame(self.root, fg_color="transparent")
        self.grid_wrap.grid(row=2, column=0, sticky="nsew")
        self.grid_wrap.columnconfigure((0, 1), weight=1, uniform="memgrp")
        self.grid_wrap.rowconfigure((0, 1), weight=1)

        self.group_widgets: dict[str, dict[str, Any]] = {}
        positions = [
            ("OTURUM", 0, 0),
            ("BÖLÜMSEL", 0, 1),
            ("UZUN SÜRELİ", 1, 0),
            ("BAĞLAM", 1, 1),
        ]
        for gname, r, c in positions:
            meta = _GROUP_META[gname]
            panel = V4HUDPanel(self.grid_wrap)
            panel.grid(row=r, column=c, sticky="nsew", padx=(0 if c == 0 else 8, 0), pady=(0 if r == 0 else 8, 0 if r == 1 else 8))
            panel.frame.rowconfigure(4, weight=1)
            panel.frame.columnconfigure(0, weight=1)
            head = V4SectionHeader(panel.frame, gname, accent=meta["accent"])
            head.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))

            ctk.CTkLabel(
                panel.frame,
                text=meta["desc"],
                text_color="#d6f4ff",
                font=("Segoe UI", 11),
                wraplength=360,
                justify="left",
                anchor="w",
            ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

            ctk.CTkLabel(
                panel.frame,
                text=f"Politika: {meta['policy']}",
                text_color=MUTED,
                font=("Segoe UI", 10),
                anchor="w",
            ).grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))

            badge_wrap = ctk.CTkFrame(panel.frame, fg_color="transparent")
            badge_wrap.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 10))
            V4StatusBadge(badge_wrap, "UI LİSTESİ YOK", color=meta["accent"]).pack(side="left")

            empty = V4EmptyState(
                panel.frame,
                title="Bellek verisi sunulmadı",
                subtitle="Bellek girdileri V3 Bellek Politikası katmanı tarafından denetlenir ve doğrudan UI'ya sunulmaz. Girdiler diskte %LOCALAPPDATA%\\HermesClient\\state altında saklanır ve gizli temizleme test paketi tarafından doğrulanır.",
                dot=MUTED,
            )
            empty.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 12))
            self.group_widgets[gname] = {"panel": panel, "empty": empty}

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
            token = self.root.after(80, self.refresh)
            self._timers.append(token)
        except Exception:
            pass

    def refresh(self) -> None:
        snap: dict[str, Any] = {}
        try:
            snap = self.store.get_snapshot() or {}
        except Exception:
            snap = {}
        evc = snap.get("event_count") or 0
        if evc:
            self.header.set_status("active", f"OLAYLAR · {evc}")

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
