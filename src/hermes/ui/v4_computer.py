from __future__ import annotations

import platform
import socket
import sys
import time
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
    ("SİSTEM", NEON_CYAN, "İşletim sistemi, ana bilgisayar, çalışma süresi, Python"),
    ("PENCERELER", "#5aa9e6", "Açık pencereler ve Z-sırası (izinler beklemede)"),
    ("SÜREÇLER", NEON_BLUE, "Çalışan süreç anlık görüntüsü"),
    ("HİZMETLER", MUTED, "Windows hizmetleri durumu"),
    ("AĞ", NEON_CYAN, "Arayüzler ve uç noktalar"),
    ("DOSYALAR", HOLO_GREEN, "Dosya sistemi yolları ve sürücüler"),
    ("UYGULAMALAR", NEON_GOLD, "Yüklü uygulamalar envanteri"),
    ("EKRAN & GİRİŞ", "#7fbfd4", "Ekranlar, klavye, imleç durumu"),
]


def _windows_uptime() -> float | None:
    try:
        import ctypes as _ct

        val = _ct.windll.kernel32.GetTickCount64()
        return float(val) / 1000.0
    except Exception:
        return None


def _format_uptime(secs: float) -> str:
    try:
        s = int(max(0.0, float(secs)))
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        m, s = divmod(s, 60)
        parts: list[str] = []
        if d:
            parts.append(f"{d}g")
        if h:
            parts.append(f"{h}s")
        if m:
            parts.append(f"{m}d")
        parts.append(f"{s}sn")
        return " ".join(parts)
    except Exception:
        return "—"


class V4Computer:
    def __init__(self, parent: Any, store: Any) -> None:
        self.store = store
        self._sub: Any = None
        self._timers: list[str] = []
        self._boot_time: float | None = None
        try:
            ut = _windows_uptime()
            if ut is not None:
                self._boot_time = time.time() - ut
        except Exception:
            self._boot_time = None

        if ctk is None:  # pragma: no cover
            raise RuntimeError("customtkinter unavailable")

        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.widget = self.root
        self.root.pack(fill="both", expand=True, padx=18, pady=14)
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.header = V4PageHeader(
            self.root,
            "BİLGİSAYAR",
            subtitle="Windows & sistem gözlem merkezi — veriler yalnızca izin verildiği yerde.",
            status="active",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.summary = V4HUDPanel(self.root)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.summary.frame.columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.t_host = V4StatusTile(self.summary.frame, "Ana Bilgisayar", "—", NEON_CYAN)
        self.t_host.grid(row=0, column=0, sticky="ew", padx=(18, 8), pady=16)
        self.t_os = V4StatusTile(self.summary.frame, "İşletim Sistemi", "—", NEON_BLUE)
        self.t_os.grid(row=0, column=1, sticky="ew", padx=8, pady=16)
        self.t_arch = V4StatusTile(self.summary.frame, "Mimari", "—", MUTED)
        self.t_arch.grid(row=0, column=2, sticky="ew", padx=8, pady=16)
        self.t_py = V4StatusTile(self.summary.frame, "Python", "—", HOLO_GREEN)
        self.t_py.grid(row=0, column=3, sticky="ew", padx=8, pady=16)
        self.t_up = V4StatusTile(self.summary.frame, "Çalışma Süresi", "—", NEON_GOLD)
        self.t_up.grid(row=0, column=4, sticky="ew", padx=(8, 18), pady=16)

        self.scroll = ctk.CTkScrollableFrame(
            self.root,
            fg_color="transparent",
            scrollbar_button_color=CARD_BORDER,
        )
        self.scroll.grid(row=2, column=0, sticky="nsew")
        self.scroll.columnconfigure(0, weight=1)

        self.group_panels: dict[str, dict[str, Any]] = {}
        self._build_groups()

        self.refresh()

        try:
            self._sub = self.store.subscribe(lambda *_a, **_k: self._schedule_refresh())
        except Exception:
            self._sub = None

    def _build_groups(self) -> None:
        for name, accent, desc in _GROUPS:
            panel = V4HUDPanel(self.scroll)
            panel.pack(fill="x", padx=2, pady=(0, 10))
            panel.frame.columnconfigure(1, weight=1)
            panel.frame.rowconfigure(3, weight=1)

            head = V4SectionHeader(panel.frame, name, accent=accent)
            head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(14, 6))
            ctk.CTkLabel(
                panel.frame,
                text=desc,
                text_color=MUTED,
                font=("Segoe UI", 10),
                anchor="w",
            ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

            # placeholder status card
            inner = ctk.CTkFrame(panel.frame, fg_color="transparent")
            inner.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=12, pady=(0, 12))
            inner.columnconfigure(0, weight=1)
            self.group_panels[name] = {"panel": panel, "inner": inner}

            # default: honest not reported
            self._group_unknown(name, accent)

    def _group_unknown(self, name: str, accent: str, reason: str | None = None) -> None:
        inner = self.group_panels[name]["inner"]
        for w in inner.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        subtitle = reason or (
            f"{name} verisi bu yapıda UI-güvenli çalışma zamanı sözleşmesi tarafından sunulmuyor."
            if name != "SİSTEM"
            else ""
        )
        if name == "SİSTEM":
            self._populate_system(inner, accent)
            return
        empty = V4EmptyState(
            inner,
            title=f"{name.lower()} · BİLDİRİLMİYOR",
            subtitle=subtitle
            or f"MUVAHHİD {name} denetimini ilke-kapılı bilgisayar kontrolleri aracılığıyla yapabilir. Kapsam alanı kasıtlı olarak okuma ile kısıtlanmıştır.",
            dot=accent if accent else MUTED,
        )
        empty.pack(fill="x", padx=4, pady=4)

    def _populate_system(self, inner: Any, accent: str) -> None:
        sys_info = [
            ("Platform", _safe_platform(), accent),
            ("Sistem", platform.system() or "—", NEON_CYAN),
            ("Sürüm", platform.release() or "—", NEON_BLUE),
            ("Derleme", platform.version() or "—", MUTED),
            ("Makine", platform.machine() or "—", MUTED),
            ("İşlemci", (platform.processor() or "BİLDİRİLMİYOR")[:80], MUTED),
            ("Ana Bilgisayar", _safe_hostname(), NEON_CYAN),
            ("Python", f"{platform.python_implementation()} {sys.version.split()[0]}", HOLO_GREEN),
            ("Önyükleme", self._boot_fmt(), NEON_GOLD),
            ("Çalışma Süresi", self._uptime_fmt(), NEON_GOLD),
            ("PID", str(__import__("os").getpid()), MUTED),
        ]
        for i, (label, value, dot) in enumerate(sys_info):
            t = V4StatusTile(inner, label, value, dot=dot)
            t.grid(row=i, column=0, sticky="ew", padx=4, pady=2)

    def _boot_fmt(self) -> str:
        if self._boot_time is None:
            return "BİLDİRİLMİYOR"
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._boot_time))
        except Exception:
            return "BİLDİRİLMİYOR"

    def _uptime_fmt(self) -> str:
        up = _windows_uptime()
        if up is None:
            return "BİLDİRİLMİYOR"
        return _format_uptime(up)

    def _schedule_refresh(self) -> None:  # pragma: no cover
        try:
            token = self.root.after(250, self.refresh)
            self._timers.append(token)
        except Exception:
            pass

    def refresh(self) -> None:
        self.t_host.set(_safe_hostname(), dot=NEON_CYAN)
        self.t_os.set(_safe_platform(), dot=NEON_BLUE)
        self.t_arch.set(platform.machine() or "—", dot=MUTED)
        pyv = sys.version.split()[0] if getattr(sys, "version", None) else "—"
        self.t_py.set(f"{platform.python_implementation()} {pyv}", dot=HOLO_GREEN)
        self.t_up.set(self._uptime_fmt(), dot=NEON_GOLD)
        # SYSTEM group re-render (others NOT REPORTED honest; no fabrication)
        self._group_unknown("SİSTEM", NEON_CYAN)
        remaining = [
            ("PENCERELER", "#5aa9e6",
             "Pencere listeleme UI Otomasyonu ayrıcalığı gerektirir. Anlık görüntü istemek için Windows köprüsünü kullanın."),
            ("SÜREÇLER", NEON_BLUE,
             "Süreç listesi geniş kapsam alanını önlemek için çalışma zamanı ilkesi tarafından denetlenir."),
            ("HİZMETLER", MUTED,
             "Windows hizmeti iç gözlemi henüz kanonik bir UI sözleşmesine bağlanmamıştır."),
            ("AĞ", NEON_CYAN,
             "Ağ bağdaştırıcısı anlık görüntüsü mevcut UI kapsamında kullanılamıyor."),
            ("DOSYALAR", HOLO_GREEN,
             "Dosya sistemi görünümleri araçlar aracılığıyla salt okunurdur. Sürücüler, birimler ve yollar otomatik listelenmez."),
            ("UYGULAMALAR", NEON_GOLD,
             "Yüklü uygulama envanteri bu yapıda UI'ya sunulmamaktadır."),
            ("EKRAN & GİRİŞ", "#7fbfd4",
             "Ekran geometrisi ve giriş durumu okuma-kapılı otomasyon ayrıcalıkları gerektirir."),
        ]
        for name, accent, reason in remaining:
            self._group_unknown(name, accent, reason)

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


def _safe_hostname() -> str:
    try:
        return socket.gethostname() or "BİLDİRİLMİYOR"
    except Exception:
        return "BİLDİRİLMİYOR"


def _safe_platform() -> str:
    try:
        return platform.platform(aliased=True, terse=False) or "BİLDİRİLMİYOR"
    except Exception:
        try:
            return f"{platform.system()} {platform.release()}".strip() or "BİLDİRİLMİYOR"
        except Exception:
            return "BİLDİRİLMİYOR"
