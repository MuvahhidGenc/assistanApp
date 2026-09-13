from __future__ import annotations

from typing import Any

from hermes.ui.modern_theme import (
    BUTTON,
    BUTTON_HOVER,
    CARD,
    CARD_BORDER,
    FONT_HUD,
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
    status_color,
)

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover
    ctk = None  # type: ignore

try:
    from hermes.skills.loader import load_all_skills  # type: ignore
except Exception:  # pragma: no cover
    load_all_skills = None  # type: ignore


_SEC_COLOR = {
    "normal": NEON_CYAN,
    "elevated": NEON_GOLD,
    "restricted": NEON_MAGENTA,
    "safe": HOLO_GREEN,
}


def _sec_color(sec: str) -> str:
    s = (sec or "normal").lower()
    return _SEC_COLOR.get(s, NEON_CYAN)


class V4Skills:
    def __init__(self, parent: Any, store: Any) -> None:
        self.store = store
        self._sub: Any = None
        self._timers: list[str] = []
        self._skills: list[Any] = []

        if ctk is None:  # pragma: no cover
            raise RuntimeError("customtkinter unavailable")

        self.root = ctk.CTkFrame(parent, fg_color="transparent")
        self.widget = self.root
        self.root.pack(fill="both", expand=True, padx=18, pady=14)
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.header = V4PageHeader(
            self.root,
            "YETENEKLER",
            subtitle="Muvahhid için öğrenilmiş ve kullanılabilir yeniden kullanılabilir iş akışları.",
            status="active" if load_all_skills else "idle",
        )
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.summary = V4HUDPanel(self.root)
        self.summary.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.summary.frame.columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.t_total = V4StatusTile(self.summary.frame, "Toplam", "0", dot=NEON_CYAN)
        self.t_total.grid(row=0, column=0, sticky="ew", padx=(18, 8), pady=16)
        self.t_exec = V4StatusTile(self.summary.frame, "Çalıştırılabilir", "—", dot=HOLO_GREEN)
        self.t_exec.grid(row=0, column=1, sticky="ew", padx=8, pady=16)
        self.t_recent = V4StatusTile(self.summary.frame, "Son Kullanım", "BİLDİRİLMİYOR", dot=MUTED)
        self.t_recent.grid(row=0, column=2, sticky="ew", padx=8, pady=16)
        self.t_repair = V4StatusTile(self.summary.frame, "Onarım Gereken", "YOK", dot=NEON_GOLD)
        self.t_repair.grid(row=0, column=3, sticky="ew", padx=8, pady=16)
        self.t_loader = V4StatusTile(self.summary.frame, "Yükleyici", "BEKLEMEDE", dot=MUTED)
        self.t_loader.grid(row=0, column=4, sticky="ew", padx=(8, 18), pady=16)

        # 4 section panels stacked vertically
        self.scroll = ctk.CTkScrollableFrame(
            self.root,
            fg_color="transparent",
            scrollbar_button_color=CARD_BORDER,
        )
        self.scroll.grid(row=2, column=0, sticky="nsew")
        self.scroll.columnconfigure(0, weight=1)

        # Learned / Available
        self.s_learned = V4SectionHeader(self.scroll, "Öğrenilenler")
        self.s_learned.pack(fill="x", padx=2, pady=(0, 8))
        self.learned_wrap = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.learned_wrap.pack(fill="x", padx=2, pady=(0, 12))
        self.learned_empty: V4EmptyState | None = None

        self.s_recent = V4SectionHeader(self.scroll, "Son Kullanılanlar", accent=HOLO_GREEN)
        self.s_recent.pack(fill="x", padx=2, pady=(0, 8))
        self.recent_wrap = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.recent_wrap.pack(fill="x", padx=2, pady=(0, 12))
        self.recent_empty: V4EmptyState | None = None

        self.s_repair = V4SectionHeader(self.scroll, "Onarım Gereken", accent=NEON_GOLD)
        self.s_repair.pack(fill="x", padx=2, pady=(0, 8))
        self.repair_wrap = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.repair_wrap.pack(fill="x", padx=2, pady=(0, 12))
        self.repair_empty: V4EmptyState | None = None

        self.s_avail = V4SectionHeader(self.scroll, "Kullanılabilir İpuçları")
        self.s_avail.pack(fill="x", padx=2, pady=(0, 8))
        self.avail_wrap = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.avail_wrap.pack(fill="x", padx=2, pady=(0, 18))
        self.avail_empty: V4EmptyState | None = None

        self.refresh()

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

    def _load_skills(self) -> list[Any]:
        if load_all_skills is None:
            return []
        try:
            return list(load_all_skills() or [])
        except Exception:
            return []

    def _skill_card(self, parent: Any, sk: Any) -> V4Card:
        card = V4Card(parent)
        card.pack(fill="x", pady=(0, 8))
        card.frame.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card.frame,
            text="▸",
            text_color=NEON_CYAN,
            font=("Segoe UI", 14),
        ).grid(row=0, column=0, rowspan=3, padx=(14, 10), pady=10, sticky="n")

        head = ctk.CTkFrame(card.frame, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(12, 2))
        head.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            head,
            text=str(getattr(sk, "title", None) or getattr(sk, "skill_id", "Başlıksız") or "Başlıksız"),
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        sec = str(getattr(sk, "security_level", "normal") or "normal")
        V4StatusBadge(head, f"GUV · {sec.upper()}", color=_sec_color(sec)).grid(row=0, column=1, sticky="e")

        id_lbl = ctk.CTkLabel(
            card.frame,
            text=f"id: {str(getattr(sk, 'skill_id', '') or '')}",
            text_color=MUTED,
            font=("Consolas", 10),
            anchor="w",
        )
        id_lbl.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=(0, 4))

        pref = list(getattr(sk, "preferred_tools", []) or [])
        if pref:
            tools_wrap = ctk.CTkFrame(card.frame, fg_color="transparent")
            tools_wrap.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=(0, 8))
            ctk.CTkLabel(tools_wrap, text="Araçlar:", text_color=MUTED, font=("Consolas", 10, "bold")).pack(side="left")
            for t in pref[:5]:
                V4StatusBadge(tools_wrap, str(t)[:18], color=NEON_BLUE).pack(side="left", padx=4)

        hints = list(getattr(sk, "hints", []) or [])
        if hints:
            hints_lbl = ctk.CTkLabel(
                card.frame,
                text="\n".join(f"•  {str(h)[:240]}" for h in hints[:3]),
                text_color=TEXT,
                font=FONT_HUD,
                anchor="w",
                justify="left",
                wraplength=700,
            )
            hints_lbl.grid(row=3, column=1, sticky="ew", padx=(0, 14), pady=(0, 12))

        return card

    def _clear(self, frame: Any) -> None:
        for w in frame.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass

    def refresh(self) -> None:
        self._skills = self._load_skills()
        count = len(self._skills)

        self.t_total.set(str(count), dot=NEON_CYAN if count > 0 else MUTED)
        if count > 0:
            self.t_loader.set("YÜKLENDİ", dot=HOLO_GREEN)
            self.header.set_status("active", f"{count} YETENEK")
        else:
            self.t_loader.set("YETENEK YOK", dot=MUTED)
            self.header.set_status("idle", "YETENEK YOK")

        # executable: approximate by checking has steps? we only get hints here → NOT REPORTED honest
        self.t_exec.set("BİLDİRİLMİYOR", dot=MUTED)

        # Learned = all
        self._clear(self.learned_wrap)
        self.learned_empty = None
        if count == 0:
            self.learned_empty = V4EmptyState(
                self.learned_wrap,
                "ÖĞRENİLMİŞ YETENEK YOK",
                subtitle="İş akışı kütüphaneleri eklemek için hermes/skills/procedures klasörüne yeniden kullanılabilir YAML yetenek dosyaları oluşturun.",
                dot=MUTED,
            )
            self.learned_empty.pack(fill="x", padx=4, pady=4)
        else:
            for sk in self._skills:
                self._skill_card(self.learned_wrap, sk)

        # Recently used = honest NOT REPORTED (no recent-use meta in SkillHint contract)
        self._clear(self.recent_wrap)
        self.recent_empty = None
        self.recent_empty = V4EmptyState(
            self.recent_wrap,
            "KULLANIM GEÇMİŞİ BİLDİRİLMİYOR",
            subtitle="Yetenek yürütme geçmişi V3 kanonik olay akışında kaydedilir — son kullanım izleri için AKTİVİTE'ye bakın.",
            dot=MUTED,
        )
        self.recent_empty.pack(fill="x", padx=4, pady=4)

        # Needs repair = honest empty (repair-state not in SkillHint)
        self._clear(self.repair_wrap)
        self.repair_empty = None
        self.repair_empty = V4EmptyState(
            self.repair_wrap,
            "ONARILACAK YETENEK RAPORU YOK",
            subtitle="Yetenek doğrulama ve onarım skills.load_executable_skills tarafından sunulur; tüm yordamlar çalıştırılabilir ipucu odaklı değildir.",
            dot=MUTED,
        )
        self.repair_empty.pack(fill="x", padx=4, pady=4)

        # Available/hints = same hint list re-shown? that duplicates learned. Show: "Same set as Learned (above)"
        self._clear(self.avail_wrap)
        self.avail_empty = None
        if count == 0:
            self.avail_empty = V4EmptyState(
                self.avail_wrap,
                "KULLANILABİLİR YETENEK İPUCU YOK",
                subtitle="İpuçları yetenek YAML dosyalarıyla birlikte bulunur; burada görünmesi için hermes/skills/procedures klasörünü doldurun.",
                dot=MUTED,
            )
            self.avail_empty.pack(fill="x", padx=4, pady=4)
        else:
            summary = V4Card(self.avail_wrap)
            summary.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(
                summary.frame,
                text=f"{count} YETENEK İPUCU MEVCUT  ·  tam ayrıntılar için yukarıdaki Öğrenilenler bölümüne bakın.",
                text_color=TEXT,
                font=("Segoe UI", 11),
                anchor="w",
                wraplength=700,
                justify="left",
            ).pack(fill="x", padx=16, pady=14)

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
