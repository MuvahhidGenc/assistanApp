from __future__ import annotations

from typing import Any

from hermes.ui.modern_theme import (
    BG,
    BG_DEEP,
    BUTTON,
    BUTTON_HOVER,
    CARD,
    CARD_BORDER,
    FONT_HERO,
    FONT_HUD,
    FONT_MONO,
    FONT_TITLE,
    HOLO_GREEN,
    INPUT_BG,
    MUTED,
    NEON_BLUE,
    NEON_CYAN,
    NEON_GOLD,
    NEON_MAGENTA,
    ORANGE,
    TEXT,
    TEXT_BRIGHT,
)
import math as _math
import traceback as _traceback

try:
    import customtkinter as ctk  # type: ignore
except Exception:  # pragma: no cover - headless fallback
    ctk = None  # type: ignore

try:
    from PIL import Image as _PIL_Image
    from PIL import ImageDraw as _PIL_Draw
    from PIL import ImageFilter as _PIL_Filter
    from PIL import ImageTk as _PIL_Tk
    _PIL_OK = True
except Exception:  # pragma: no cover
    _PIL_OK = False
    _PIL_Image = None  # type: ignore
    _PIL_Draw = None   # type: ignore
    _PIL_Filter = None  # type: ignore
    _PIL_Tk = None     # type: ignore


_STATUS_COLORS: dict[str, str] = {
    "idle": MUTED,
    "active": NEON_CYAN,
    "success": HOLO_GREEN,
    "warning": NEON_GOLD,
    "danger": NEON_MAGENTA,
    "info": NEON_BLUE,
    "unknown": MUTED,
}

_NAV_TR: dict[str, str] = {
    "HOME": "ANA SAYFA",
    "CHAT": "SOHBET",
    "AGENT": "AJAN",
    "MEMORY": "HAFIZA",
    "SKILLS": "YETENEKLER",
    "COMPUTER": "BİLGİSAYAR",
    "BROWSER": "TARAYICI",
    "TASKS": "GÖREVLER",
    "ACTIVITY": "AKTİVİTE",
    "SETTINGS": "AYARLAR",
}

_TR_PHASE: dict[str, str] = {
    "idle": "HAZIR",
    "listen": "DİNLİYOR",
    "listening": "DİNLİYOR",
    "think": "DÜŞÜNÜYOR",
    "thinking": "DÜŞÜNÜYOR",
    "executing": "ÇALIŞIYOR",
    "execute": "ÇALIŞIYOR",
    "speaking": "KONUŞUYOR",
    "speak": "KONUŞUYOR",
    "approve": "ONAY BEKLİYOR",
    "awaiting_approval": "ONAY BEKLİYOR",
    "completed": "TAMAMLANDI",
    "complete": "TAMAMLANDI",
    "error": "HATA",
    "failed": "HATA",
}

_MUVA_PALETTES: dict[str, dict[str, str]] = {
    "HAZIR": {
        "nucleus": "#7ff2ff", "ring1": "#00e5ff", "ring2": "#00b8d9", "ring3": "#11425b",
        "orange1": "#ff8855", "orange2": "#ff6b35", "wave": "#4feaff", "glow_outer": "#0b283a",
        "glow_mid": "#13415c", "part": "#9ff4ff", "bg": BG_DEEP,
    },
    "DİNLİYOR": {
        "nucleus": "#a6fff2", "ring1": "#00ffe0", "ring2": "#00d6c1", "ring3": "#124a46",
        "orange1": "#ffaa77", "orange2": "#ff8855", "wave": "#77ffe8", "glow_outer": "#0d3334",
        "glow_mid": "#185757", "part": "#c2fff3", "bg": BG_DEEP,
    },
    "DÜŞÜNÜYOR": {
        "nucleus": "#ffe97f", "ring1": "#ffd60a", "ring2": "#e6bf00", "ring3": "#4d3c06",
        "orange1": "#ffb04d", "orange2": "#ff9500", "wave": "#ffe55c", "glow_outer": "#2a1e04",
        "glow_mid": "#4d3b00", "part": "#fff2a6", "bg": BG_DEEP,
    },
    "ÇALIŞIYOR": {
        "nucleus": "#7ff2ff", "ring1": "#00e5ff", "ring2": "#00b8d9", "ring3": "#123e5a",
        "orange1": "#ff8a5c", "orange2": "#ff6b35", "wave": "#5eefff", "glow_outer": "#241208",
        "glow_mid": "#4a2510", "part": "#b3a08f", "bg": BG_DEEP,
    },
    "KONUŞUYOR": {
        "nucleus": "#b2ffdc", "ring1": "#05ffa1", "ring2": "#00d88a", "ring3": "#0c4430",
        "orange1": "#ffaa77", "orange2": "#ff8855", "wave": "#86ffc9", "glow_outer": "#0a2e1e",
        "glow_mid": "#165338", "part": "#c8ffe5", "bg": BG_DEEP,
    },
    "ONAY BEKLİYOR": {
        "nucleus": "#ffe97f", "ring1": "#ffd60a", "ring2": "#e6bf00", "ring3": "#4a3b04",
        "orange1": "#ffe055", "orange2": "#ffd60a", "wave": "#ffe55c", "glow_outer": "#282104",
        "glow_mid": "#4c4100", "part": "#fff2a6", "bg": BG_DEEP,
    },
    "TAMAMLANDI": {
        "nucleus": "#b2ffdc", "ring1": "#05ffa1", "ring2": "#00d88a", "ring3": "#0c4631",
        "orange1": "#c7ffdd", "orange2": "#5bca92", "wave": "#86ffc9", "glow_outer": "#0a2e20",
        "glow_mid": "#16553a", "part": "#c8ffe5", "bg": BG_DEEP,
    },
    "HATA": {
        "nucleus": "#ffa9c7", "ring1": "#ff2a6d", "ring2": "#e00a53", "ring3": "#4a0a20",
        "orange1": "#ff8fb5", "orange2": "#ff2a6d", "wave": "#ff77a3", "glow_outer": "#300817",
        "glow_mid": "#4a0a1c", "part": "#ff2a6d", "bg": BG,
    },
}


def _phase_to_tr(phase: str) -> str:
    if not phase:
        return "HAZIR"
    p = str(phase).lower().strip()
    if p in _TR_PHASE:
        return _TR_PHASE[p]
    for k, v in _TR_PHASE.items():
        if k in p:
            return v
    return "HAZIR"


def _phase_palette(phase_tr: str) -> dict[str, str]:
    return _MUVA_PALETTES.get(phase_tr, _MUVA_PALETTES["HAZIR"])


def status_color(state: str, default: str = MUTED) -> str:
    s = (state or "unknown").lower()
    for k, v in _STATUS_COLORS.items():
        if k in s:
            return v
    return default


# ───────────────────────────────────────────────────────────────────────────────
#  1. V4Panel — premium card container
# ───────────────────────────────────────────────────────────────────────────────
class V4Panel:
    def __init__(self, parent: Any, **kw: Any) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        opts: dict[str, Any] = {
            "fg_color": CARD,
            "corner_radius": 14,
            "border_width": 1,
            "border_color": CARD_BORDER,
        }
        opts.update(kw)
        self.frame = ctk.CTkFrame(parent, **opts)
        self.widget = self.frame

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
#  2. V4PageHeader — top page banner
# ───────────────────────────────────────────────────────────────────────────────
class V4PageHeader:
    def __init__(
        self,
        parent: Any,
        title: str,
        subtitle: str | None = None,
        status: str = "idle",
        **kw: Any,
    ) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        self.frame = ctk.CTkFrame(parent, fg_color="transparent", **kw)
        self.widget = self.frame

        self.frame.columnconfigure(1, weight=1)

        dot_color = status_color(status)
        self.dot = ctk.CTkLabel(
            self.frame,
            text="●",
            text_color=dot_color,
            font=("Segoe UI", 16),
            width=22,
            anchor="w",
        )
        self.dot.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=(4, 0), sticky="w")

        self.title_label = ctk.CTkLabel(
            self.frame,
            text=title.upper(),
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 22, "bold"),
            anchor="w",
        )
        self.title_label.grid(row=0, column=1, sticky="w", pady=(0, 2))

        if subtitle:
            self.subtitle_label: ctk.CTkLabel | None = ctk.CTkLabel(
                self.frame,
                text=subtitle,
                text_color=MUTED,
                font=FONT_HUD,
                anchor="w",
            )
            self.subtitle_label.grid(row=1, column=1, sticky="w")
        else:
            self.subtitle_label = None

        self.status_pill = ctk.CTkLabel(
            self.frame,
            text=f"  {status.upper()}  ",
            text_color=dot_color,
            fg_color=CARD,
            corner_radius=999,
            font=("Consolas", 10, "bold"),
            border_width=1,
            border_color=CARD_BORDER,
        )
        self.status_pill.grid(row=0, column=2, rowspan=2, padx=(16, 0), sticky="e")

    def set_status(self, status: str, text: str | None = None) -> None:  # pragma: no cover
        col = status_color(status)
        self.dot.configure(text_color=col)
        self.status_pill.configure(
            text_color=col, text=f"  {(text or status).upper()}  "
        )

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
#  3. V4SectionHeader — subsection separator
# ───────────────────────────────────────────────────────────────────────────────
class V4SectionHeader:
    def __init__(self, parent: Any, title: str, accent: str = NEON_CYAN, **kw: Any) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        self.frame = ctk.CTkFrame(parent, fg_color="transparent", **kw)
        self.widget = self.frame
        self.frame.columnconfigure(1, weight=1)

        self.label = ctk.CTkLabel(
            self.frame,
            text=f"◈ {title.upper()}",
            text_color=accent,
            font=("Consolas", 11, "bold"),
            anchor="w",
        )
        self.label.grid(row=0, column=0, sticky="w", padx=(2, 10))

        self.rule = ctk.CTkFrame(self.frame, height=1, fg_color=CARD_BORDER)
        self.rule.grid(row=0, column=1, sticky="ew", padx=(0, 2), pady=(10, 10))

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
#  4. V4StatusBadge — small uppercase pill chip
# ───────────────────────────────────────────────────────────────────────────────
class V4StatusBadge:
    def __init__(
        self,
        parent: Any,
        text: str,
        color: str = NEON_CYAN,
        **kw: Any,
    ) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        opts: dict[str, Any] = {
            "text": f"  {text.upper()}  ",
            "text_color": color,
            "fg_color": CARD,
            "corner_radius": 999,
            "border_width": 1,
            "border_color": CARD_BORDER,
            "font": ("Consolas", 10, "bold"),
        }
        opts.update(kw)
        self.label = ctk.CTkLabel(parent, **opts)
        self.widget = self.label

    def pack(self, **kw: Any) -> None:
        self.label.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.label.grid(**kw)

    def configure(self, **kw: Any) -> None:
        self.label.configure(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.label.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
#  5. V4StatusTile — 3-column key/value row with status dot
# ───────────────────────────────────────────────────────────────────────────────
class V4StatusTile:
    def __init__(
        self,
        parent: Any,
        label: str,
        value: str = "—",
        dot: str = MUTED,
        value_color: str = TEXT,
        **kw: Any,
    ) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        self.frame = ctk.CTkFrame(parent, fg_color="transparent", **kw)
        self.widget = self.frame
        self.frame.columnconfigure(1, weight=0)
        self.frame.columnconfigure(2, weight=1)

        self.dot_label = ctk.CTkLabel(
            self.frame,
            text="●",
            text_color=dot,
            font=("Segoe UI", 10),
            width=14,
            anchor="w",
        )
        self.dot_label.grid(row=0, column=0, padx=(8, 8), pady=1, sticky="w")

        self.key_label = ctk.CTkLabel(
            self.frame,
            text=label.upper(),
            text_color=MUTED,
            font=("Consolas", 10, "bold"),
            width=118,
            anchor="w",
        )
        self.key_label.grid(row=0, column=1, padx=(8, 12), pady=1, sticky="w")

        self.value_label = ctk.CTkLabel(
            self.frame,
            text=value,
            text_color=value_color,
            font=FONT_HUD,
            anchor="w",
            justify="left",
        )
        self.value_label.grid(row=0, column=2, pady=1, sticky="we")

    def set(self, value: str, dot: str | None = None, value_color: str | None = None) -> None:  # pragma: no cover - runtime
        self.value_label.configure(text=value)
        if dot is not None:
            self.dot_label.configure(text_color=dot)
        if value_color is not None:
            self.value_label.configure(text_color=value_color)

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
#  6. V4TimelineEvent — single timeline rail row (dot + connector + card)
# ───────────────────────────────────────────────────────────────────────────────
class V4TimelineEvent:
    HAS_CONNECTOR = True

    def __init__(
        self,
        parent: Any,
        title: str,
        detail: str = "",
        timestamp: str = "",
        color: str = NEON_CYAN,
        category: str = "event",
        show_connector: bool = True,
        **kw: Any,
    ) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        self.frame = ctk.CTkFrame(parent, fg_color="transparent", **kw)
        self.widget = self.frame
        self.frame.columnconfigure(1, weight=1)

        self.rail = ctk.CTkFrame(self.frame, fg_color="transparent", width=24)
        self.rail.grid(row=0, column=0, rowspan=2, padx=(2, 10), sticky="ns")

        self.dot = ctk.CTkLabel(
            self.rail,
            text="●",
            text_color=color,
            font=("Segoe UI", 10),
            anchor="n",
        )
        self.dot.pack(side="top", padx=2, pady=(2, 0))

        if show_connector:
            self.connector = ctk.CTkFrame(self.rail, width=1, fg_color=CARD_BORDER)
            self.connector.pack(side="top", fill="y", expand=True, pady=(2, 0), padx=8)

        self.head = ctk.CTkFrame(self.frame, fg_color="transparent")
        self.head.grid(row=0, column=1, sticky="we", pady=(0, 2))
        self.head.columnconfigure(1, weight=1)

        self.cat_badge = V4StatusBadge(self.head, category, color=color)
        self.cat_badge.grid(row=0, column=0, padx=(0, 10), pady=(0, 0), sticky="w")

        self.title_lbl = ctk.CTkLabel(
            self.head,
            text=title,
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
            justify="left",
        )
        self.title_lbl.grid(row=0, column=1, sticky="we")

        if timestamp:
            self.time_lbl: ctk.CTkLabel | None = ctk.CTkLabel(
                self.head,
                text=timestamp,
                text_color=MUTED,
                font=FONT_MONO,
                anchor="e",
            )
            self.time_lbl.grid(row=0, column=2, padx=(10, 0), sticky="e")
        else:
            self.time_lbl = None

        if detail:
            self.detail_lbl: ctk.CTkLabel | None = ctk.CTkLabel(
                self.frame,
                text=detail,
                text_color=TEXT,
                font=FONT_HUD,
                anchor="w",
                justify="left",
                wraplength=640,
            )
            self.detail_lbl.grid(row=1, column=1, sticky="we", padx=(0, 4), pady=(0, 4))
        else:
            self.detail_lbl = None

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
#  7. V4EmptyState — honest "no data" card
# ───────────────────────────────────────────────────────────────────────────────
class V4EmptyState:
    def __init__(
        self,
        parent: Any,
        title: str,
        subtitle: str = "",
        dot: str = MUTED,
        **kw: Any,
    ) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        opts: dict[str, Any] = {
            "fg_color": CARD,
            "corner_radius": 12,
            "border_width": 1,
            "border_color": CARD_BORDER,
        }
        opts.update(kw)
        self.frame = ctk.CTkFrame(parent, **opts)
        self.widget = self.frame

        inner = ctk.CTkFrame(self.frame, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=20)

        self.dot_lbl = ctk.CTkLabel(
            inner,
            text="●",
            text_color=dot,
            font=("Segoe UI", 18),
        )
        self.dot_lbl.pack(anchor="center", pady=(0, 6))

        self.title_lbl = ctk.CTkLabel(
            inner,
            text=title.upper(),
            text_color=TEXT_BRIGHT,
            font=("Segoe UI", 14, "bold"),
            anchor="center",
            justify="center",
        )
        self.title_lbl.pack(anchor="center", pady=(0, 6))

        if subtitle:
            self.sub_lbl: ctk.CTkLabel | None = ctk.CTkLabel(
                inner,
                text=subtitle,
                text_color=MUTED,
                font=FONT_HUD,
                anchor="center",
                justify="center",
                wraplength=520,
            )
            self.sub_lbl.pack(anchor="center")
        else:
            self.sub_lbl = None

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
#  8. V4Card — nested info card (small radius, 1px border)
# ───────────────────────────────────────────────────────────────────────────────
class V4Card:
    def __init__(self, parent: Any, **kw: Any) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        opts: dict[str, Any] = {
            "fg_color": BG_DEEP,
            "corner_radius": 10,
            "border_width": 1,
            "border_color": CARD_BORDER,
        }
        opts.update(kw)
        self.frame = ctk.CTkFrame(parent, **opts)
        self.widget = self.frame

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        try:
            self.frame.destroy()
        except Exception:
            pass


__all__ = [
    "V4Panel",
    "V4PageHeader",
    "V4SectionHeader",
    "V4StatusBadge",
    "V4StatusTile",
    "V4TimelineEvent",
    "V4EmptyState",
    "V4Card",
    "V4HUDPanel",
    "V4CoreCanvas",
    "V4HUDStatusChip",
    "status_color",
    "_NAV_TR",
    "_TR_PHASE",
    "_MUVA_PALETTES",
    "_phase_to_tr",
    "_phase_palette",
]


# ───────────────────────────────────────────────────────────────────────────────
#  9. V4HUDPanel — 4 köşe HUD çizgili premium panel (referans görsel L köşe)
# ───────────────────────────────────────────────────────────────────────────────
class V4HUDPanel:
    """Panel with 4 thin L-shaped HUD corner accents.

    SAFE BY DESIGN:
      * 4 separate 40x40 corner canvases at each corner (nw, ne, sw, se)
      * No full-size relwidth=1/relheight=1 overlay that would cover children
      * No tkraise() over child widgets (V4CoreCanvas stays visible)
    """

    CORNER_LEN = 18
    CORNER_GAP = 6
    CORNER_W = 2
    CORNER_CELL = 40  # each small corner canvas size (pixels)

    def __init__(self, parent: Any, accent: str = NEON_CYAN, **kw: Any) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        opts: dict[str, Any] = {
            "fg_color": CARD,
            "corner_radius": 0,
            "border_width": 1,
            "border_color": CARD_BORDER,
        }
        opts.update(kw)
        self._accent = accent
        self.frame = ctk.CTkFrame(parent, **opts)
        self.widget = self.frame
        # Four tiny corner canvases (40x40 each)
        self._corner_nw: Any = None
        self._corner_ne: Any = None
        self._corner_sw: Any = None
        self._corner_se: Any = None
        # List of 4 lists (each sub-list holds 2 line ids for that corner)
        self._corner_ids: list[list[int]] = [[], [], [], []]
        self.frame.bind("<Configure>", self._on_configure, add="+")
        try:
            self.frame.after(1, self._draw_corners)
        except Exception:
            pass

    def _ensure_corners(self) -> bool:  # pragma: no cover - UI
        """Create 4 small corner canvases once. Return True if all usable."""
        size = self.CORNER_CELL
        try:
            bg = self.frame.cget("fg_color")
        except Exception:
            bg = CARD
        def _mk():  # small helper
            try:
                return ctk.CTkCanvas(self.frame, width=size, height=size,
                                     bg=bg, highlightthickness=0, bd=0)
            except Exception as exc:
                import traceback as _tb
                try:
                    print(f"[V4HUDPanel] _mk FAIL: {type(exc).__name__}: {exc}")
                    print(_tb.format_exc(limit=3))
                except Exception:
                    pass
                return None
        try:
            if self._corner_nw is None:
                c = _mk()
                if c is not None:
                    c.place(x=0, y=0, anchor="nw")
                self._corner_nw = c
            if self._corner_ne is None:
                c = _mk()
                if c is not None:
                    c.place(relx=1.0, y=0, anchor="ne")
                self._corner_ne = c
            if self._corner_sw is None:
                c = _mk()
                if c is not None:
                    c.place(x=0, rely=1.0, anchor="sw")
                self._corner_sw = c
            if self._corner_se is None:
                c = _mk()
                if c is not None:
                    c.place(relx=1.0, rely=1.0, anchor="se")
                self._corner_se = c
        except Exception as exc:
            import traceback as _tb
            try:
                print(f"[V4HUDPanel._ensure_corners] FAIL: {type(exc).__name__}: {exc}")
                print(_tb.format_exc(limit=3))
            except Exception:
                pass
            return False
        return (self._corner_nw is not None and self._corner_ne is not None and
                self._corner_sw is not None and self._corner_se is not None)

    # noinspection PyProtectedMember,PyUnresolvedReferences
    def _on_configure(self, _evt: object = None) -> None:  # pragma: no cover - UI
        try:
            self.frame.after_idle(self._draw_corners)
        except Exception:
            pass

    def _draw_corners(self) -> None:  # pragma: no cover - UI
        try:
            w = max(10, self.frame.winfo_width())
            h = max(10, self.frame.winfo_height())
        except Exception as exc:
            import traceback as _tb
            try:
                print(f"[V4HUDPanel._draw_corners] SIZE FAIL: {type(exc).__name__}: {exc}")
                print(_tb.format_exc(limit=3))
            except Exception:
                pass
            return
        if w < 20 or h < 20:
            return
        if not self._ensure_corners():
            return
        cvs: tuple[Any, Any, Any, Any] = (self._corner_nw, self._corner_ne, self._corner_sw, self._corner_se)
        # Clear each corner safely (40x40 cell, never touches central children)
        for idx, cv in enumerate(cvs):
            if cv is None:
                continue
            for _id in self._corner_ids[idx]:
                try:
                    cv.delete(_id)
                except Exception:
                    pass
            self._corner_ids[idx] = []
        col = self._accent
        L = self.CORNER_LEN
        G = self.CORNER_GAP
        cs = self.CORNER_CELL
        # Each corner is drawn within 40x40 corner cell coordinates
        local: list[tuple[float, float, float, float, float, float, float, float]] = [
            (G, G, G, G + L, G, G, G + L, G),
            (cs - G, G, cs - G, G + L, cs - G - L, G, cs - G, G),
            (G, cs - G, G, cs - G - L, G, cs - G, G + L, cs - G),
            (cs - G, cs - G, cs - G, cs - G - L, cs - G - L, cs - G, cs - G, cs - G),
        ]
        for idx, (cv, coords) in enumerate(zip(cvs, local)):
            if cv is None:
                continue
            x1a, y1a, x2a, y2a, x1b, y1b, x2b, y2b = coords
            try:
                i1 = cv.create_line(x1a, y1a, x2a, y2a, fill=col, width=self.CORNER_W)
                i2 = cv.create_line(x1b, y1b, x2b, y2b, fill=col, width=self.CORNER_W)
                self._corner_ids[idx].extend([i1, i2])
            except Exception as exc:
                import traceback as _tb
                try:
                    print(f"[V4HUDPanel._draw_corners] idx={idx} FAIL: {type(exc).__name__}: {exc}")
                    print(_tb.format_exc(limit=3))
                except Exception:
                    pass

    def pack(self, **kw: Any) -> None:  # pragma: no cover - UI
        self.frame.pack(**kw)
        try:
            self.frame.after(2, self._draw_corners)
        except Exception:
            pass

    def grid(self, **kw: Any) -> None:  # pragma: no cover - UI
        self.frame.grid(**kw)
        try:
            self.frame.after(2, self._draw_corners)
        except Exception:
            pass

    def configure(self, **kw: Any) -> None:  # pragma: no cover - runtime
        if "accent" in kw:
            self._accent = kw.pop("accent")
            try:
                self.frame.after(1, self._draw_corners)
            except Exception:
                pass
        try:
            self.frame.configure(**kw)
        except Exception:
            pass

    def destroy(self) -> None:  # pragma: no cover - lifecycle
        for cv in (self._corner_nw, self._corner_ne, self._corner_sw, self._corner_se):
            try:
                if cv is not None:
                    cv.destroy()
            except Exception:
                pass
        self._corner_nw = self._corner_ne = self._corner_sw = self._corner_se = None
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
# 10. V4HUDStatusChip — inline key dot + label HUD minibutton (header/bottom)
# ───────────────────────────────────────────────────────────────────────────────
class V4HUDStatusChip:
    def __init__(
        self,
        parent: Any,
        key_label: str,
        value_label: str,
        dot: str = NEON_CYAN,
        key_color: str = MUTED,
        value_color: str = TEXT,
        **kw: Any,
    ) -> None:
        if ctk is None:  # pragma: no cover - headless
            raise RuntimeError("customtkinter unavailable")
        self.frame = ctk.CTkFrame(parent, fg_color="transparent", **kw)
        self.widget = self.frame
        self._dot = dot

        self.dot_lbl = ctk.CTkLabel(
            self.frame, text="●", text_color=dot, font=("Segoe UI", 10), width=12, anchor="w"
        )
        self.dot_lbl.grid(row=0, column=0, padx=(8, 6), pady=1, sticky="w")

        self.key_lbl = ctk.CTkLabel(
            self.frame, text=key_label, text_color=key_color,
            font=("Segoe UI", 10, "bold"), anchor="w"
        )
        self.key_lbl.grid(row=0, column=1, padx=(8, 6), pady=1, sticky="w")

        self.sep = ctk.CTkLabel(
            self.frame, text="│", text_color=CARD_BORDER, font=FONT_MONO, width=10, anchor="center"
        )
        self.sep.grid(row=0, column=2, padx=(8, 6), pady=1, sticky="w")

        self.val_lbl = ctk.CTkLabel(
            self.frame, text=value_label, text_color=value_color,
            font=("Segoe UI", 11, "bold"), anchor="w"
        )
        self.val_lbl.grid(row=0, column=3, padx=(8, 0), pady=1, sticky="w")

    def set(self, value_label: str | None = None, dot: str | None = None, value_color: str | None = None) -> None:  # pragma: no cover
        if value_label is not None:
            self.val_lbl.configure(text=value_label)
        if dot is not None:
            self.dot_lbl.configure(text_color=dot)
            self._dot = dot
        if value_color is not None:
            self.val_lbl.configure(text_color=value_color)

    def pack(self, **kw: Any) -> None:
        self.frame.pack(**kw)

    def grid(self, **kw: Any) -> None:
        self.frame.grid(**kw)

    def destroy(self) -> None:  # pragma: no cover
        try:
            self.frame.destroy()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────────────────────
# 11. V4CoreCanvas — büyük yaşayan MUVAHHİD AI Core canvas widget
# ───────────────────────────────────────────────────────────────────────────────
class V4CoreCanvas:
    PARTICLE_COUNT = 4
    WAVE_BARS = 140
    CORE_ASSET_PATH = str(
        __import__("pathlib").Path(__file__).resolve().parents[3] / "assets" / "coreback.png"
    )

    def __init__(
        self,
        parent: Any,
        size_px: int = 620,
        phase_tr: str = "HAZIR",
        audio_amplitude: float = 0.0,
        **kw: Any,
    ) -> None:
        if ctk is None:  # pragma: no cover
            raise RuntimeError("customtkinter unavailable")
        self.size = size_px
        self._phase_tr = phase_tr
        self._amp = max(0.0, min(1.0, audio_amplitude))
        self._amp_smooth = 0.0
        self._t = 0.0
        self._ids: list[int] = []
        self._after_id: str | None = None
        self._destroyed = False
        self._draw_timer: str | None = None
        self._photo_refs: list[Any] = []
        # Static base asset — load ONCE in __init__, NO reloading each frame
        self._core_asset_raw: Any = None
        self._core_asset_key: tuple[int, int, int] | None = None
        self._core_asset_photo: Any = None
        if _PIL_OK:
            try:
                if __import__("os").path.exists(self.CORE_ASSET_PATH):
                    raw = _PIL_Image.open(self.CORE_ASSET_PATH).convert("RGBA")
                    self._core_asset_raw = raw
            except Exception as exc:
                try:
                    print(f"[V4CoreCanvas load core asset FAIL: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
        opts: dict[str, Any] = {
            "bg": BG_DEEP,
            "highlightthickness": 0,
            "bd": 0,
            "width": size_px,
            "height": size_px,
        }
        opts.update(kw)
        # CTkCanvas prefers tk Canvas sub (no customtkinter canvas); use tk Canvas via Toplevel call
        try:
            # noinspection PyUnresolvedReferences,PyProtectedMember
            self.canvas = ctk.CTkCanvas(parent, **opts) if hasattr(ctk, "CTkCanvas") else ctk.tk.Canvas(parent, **opts)
        except Exception:
            self.canvas = parent  # pragma: no cover
        self.widget = self.canvas
        try:
            self.canvas.bind("<Configure>", self._on_resize, add="+")
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas.__init__ bind Configure] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass

    # ── geometry helpers ──────────────────────────────────────────────────
    @property
    def cx(self) -> int:
        try:
            return int(self.canvas.winfo_width() / 2)
        except Exception:
            return int(self.size / 2)

    @property
    def cy(self) -> int:
        try:
            return int(self.canvas.winfo_height() / 2)
        except Exception:
            return int(self.size / 2)

    @property
    def R(self) -> int:
        return min(self.cx, self.cy) - 8

    def _on_resize(self, _evt: object = None) -> None:  # pragma: no cover
        try:
            self.canvas.after_idle(self.redraw)
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas._on_resize] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass

    # ── animation loop (30 FPS) ────────────────────────────────────────────
    def start_animation(self, interval_ms: int = 33) -> None:  # pragma: no cover
        self._schedule(interval_ms)

    def _schedule(self, ms: int) -> None:  # pragma: no cover
        if self._destroyed:
            return
        try:
            if self._after_id is not None:
                self.canvas.after_cancel(self._after_id)
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas._schedule cancel] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=2), flush=True)
            except Exception:
                pass
        try:
            self._after_id = self.canvas.after(ms, self._tick)
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas._schedule after] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
            self._after_id = None

    def _tick(self) -> None:  # pragma: no cover
        if self._destroyed:
            return
        self._t = (self._t + 0.033) % 10000.0
        try:
            self.redraw()
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas._tick] FAIL redraw: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=5), flush=True)
            except Exception:
                pass
        self._schedule(33)

    def set_phase(self, phase_tr: str) -> None:
        self._phase_tr = phase_tr

    def set_amplitude(self, amp: float) -> None:
        try:
            if isinstance(amp, (int, float)):
                self._amp = max(0.0, min(1.0, float(amp)))
            else:
                self._amp = 0.0
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas.set_amplitude] FAIL (amp={amp!r}): {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
            self._amp = 0.0

    def redraw(self) -> None:
        pal = _phase_palette(self._phase_tr)
        try:
            w = max(200, int(self.canvas.winfo_width() or self.size))
            h = max(200, int(self.canvas.winfo_height() or self.size))
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas.redraw geometry] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
            w, h = self.size, self.size
        cx, cy = int(w / 2), int(h / 2)
        R = min(cx, cy) - 8
        # ⚠ CRITICAL ORDER: Check R BEFORE deleting anything.
        # If we delete first and R<40, canvas becomes BLACK EMPTY (Failure Mode 4).
        if R < 40:
            return
        # Now safe to clear previous frame — we WILL draw new content this frame.
        for _id in self._ids:
            try:
                self.canvas.delete(_id)
            except Exception as exc:
                try:
                    print(f"[V4CoreCanvas.redraw delete] FAIL id={_id}: {type(exc).__name__}: {exc}\n"
                          + _traceback.format_exc(limit=2), flush=True)
                except Exception:
                    pass
        self._ids = []
        self._photo_refs = []
        # ═══════════════════════════════════════════════════════════════════════
        #  MUVAHHID V4 — ASSET-BASED CORE v2 (ORBITAL ENERJİ BAĞLANTILARI)
        #
        #  PERFORMANS:
        #   - Base asset __init__'te 1 defa yüklenir, (w,h,R) değişince 1× LANCZOS scale
        #   - EVERY FRAME PIL BLUR / COMPOSITE YOK (kullanıcı yavaşlama şikayeti — korunur)
        #   - Sadece düşük maliyetli TK primitive: 3 ambience ring + 4 orbital (top 13 arc
        #     + 8 düğüm) + 4 part + 2 text = ~27 nesne. ÇOK HAFİF.
        #
        #  Stacking (back → front, NO FLAT FILLED CIRCLE, NO blue disk):
        #   L0  HOLOGRAPHIC ENERGY AMBIENCE (arkada, asset altı): 3 hafif konsantrik
        #       elips glow ring — fill="", sadece outline, çok ince, amplitude ile hafif
        #       büyüme + yavaş pulse. Siyah dark bg korunur.
        #   L1  STATIC ASSET coreback.png (DEĞİŞTİRİLMEDİ — M + halkalar + glow STATİK)
        #   L2  4 tiny particles (3 cyan / 1 orange — korunur)
        #   L5  4 AYRI SEGMENTLİ ORBİTAL BAĞLANTI ÇİZGİLERİ (üstte, asset üzerinde):
        #       farklı yarıçap (0.55→1.04R), farklı elips tilt (rx/ry), 2-4 arc segment,
        #       her orbital 1-2 enerji düğümü, farklı RPM, amplitude ile kalınlık pulse.
        #       Tek çizgi gibi görünmez. Önünden/arkasından geçen hissi.
        #   L6  MUVAHHID + 8 phase subtitle (canonical map korunur)
        # ═══════════════════════════════════════════════════════════════════════
        t_ang = self._t
        # 1. Audio smoothing — α=0.38 (ani zıplama YOK, hızlı ama pürüzsüz)
        target = float(self._amp)
        self._amp_smooth = self._amp_smooth + 0.38 * (target - self._amp_smooth)
        if self._amp_smooth < 0:
            self._amp_smooth = 0.0
        elif self._amp_smooth > 1:
            self._amp_smooth = 1.0
        amp_s = self._amp_smooth
        amp_draw = amp_s
        if amp_draw < 0.015:
            amp_draw = 0.020 + 0.020 * (0.5 + 0.5 * _math.sin(t_ang * 1.8))
        # Shared amplitude glow multiplier: 1.0 idle → 1.45 high volume
        amp_grow = 1.0 + 0.45 * amp_draw
        amp_line = 0.9 + 1.2 * amp_draw  # idle 0.9 → high 2.1

        # ── L0. HOLOGRAPHIC ENERGY AMBIENCE (arkada, asset ALTINDA):
        #    NO flat filled disk. NO blue circle. 3 cok ince outline-only elips ring.
        #    Amplitude: radius hafif büyür, outline kalınlaşır. Siyah bg korunur.
        amb_pulse = 1.0 + 0.04 * _math.sin(t_ang * 1.1) + 0.025 * _math.sin(t_ang * 2.9)
        amb_colors = (pal["glow_outer"], pal["glow_mid"], pal["ring2"])
        amb_rates = (1.12, 0.96, 0.80)  # 3 ayrı radius (asset biraz dışından başlar içeri)
        amb_tilts = ((0.98, 1.00), (1.00, 0.96), (0.97, 1.02))  # küçük elips tilt — tam yuvarlak DEĞİL
        amb_widths = (0.8, 1.1, 1.4)
        for ia in range(3):
            try:
                r_base = int(R * amb_rates[ia] * amb_pulse * (1.0 + 0.04 * amp_draw))
                rx = int(r_base * amb_tilts[ia][0])
                ry = int(r_base * amb_tilts[ia][1])
                w_line = max(0.7, amb_widths[ia] * amp_line * 0.7)
                _id = self.canvas.create_oval(cx - rx, cy - ry, cx + rx, cy + ry,
                                              fill="", outline=amb_colors[ia], width=w_line)
                self._ids.append(_id)
            except Exception:
                pass

        # ── L1. STATIC ASSET (coreback.png) — load once, scale cache by (w,h,R)
        used_asset = False
        if _PIL_OK and self._core_asset_raw is not None:
            try:
                key = (w, h, R)
                if self._core_asset_key != key:
                    aw0, ah0 = self._core_asset_raw.size
                    pad = max(6, int(w * 0.025))
                    fit_w = max(1, w - pad * 2)
                    fit_h = max(1, h - pad * 2)
                    s_ratio = min(fit_w / aw0, fit_h / ah0)
                    s_ratio = min(s_ratio, 0.92)
                    nw_ = max(1, int(aw0 * s_ratio))
                    nh_ = max(1, int(ah0 * s_ratio))
                    small = self._core_asset_raw.resize((nw_, nh_), _PIL_Image.LANCZOS)
                    blank = _PIL_Image.new("RGBA", (w, h), (0, 0, 0, 0))
                    px = int((w - nw_) / 2)
                    py = int((h - nh_) / 2)
                    blank.paste(small, (px, py, px + nw_, py + nh_), small)
                    self._core_asset_photo = _PIL_Tk.PhotoImage(blank)
                    self._core_asset_key = key
                if self._core_asset_photo is not None:
                    self._photo_refs.append(self._core_asset_photo)
                    _id = self.canvas.create_image(cx, cy, image=self._core_asset_photo, anchor="center")
                    self._ids.append(_id)
                    used_asset = True
            except Exception as exc:
                try:
                    print(f"[V4CoreCanvas asset render FAIL: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
        if not used_asset:
            for dr, col in ((int(R * 0.95), pal["glow_outer"]), (int(R * 0.82), pal["glow_mid"])):
                try:
                    _id = self.canvas.create_oval(cx - dr, cy - dr, cx + dr, cy + dr, fill=col, outline="")
                    self._ids.append(_id)
                except Exception:
                    pass

        # ── L2. 4 tiny particles (3 cyan / 1 orange — ultra yavaş hareket, amplitude pulse korunur)
        p_vel = (0.06, -0.09, 0.12, -0.05)
        p_rads = (int(R * 0.94), int(R * 0.74), int(R * 0.52), int(R * 0.34))
        for i in range(self.PARTICLE_COUNT):
            rad = _math.radians(t_ang * 60.0 * p_vel[i])
            x = cx + _math.cos(rad) * p_rads[i]
            y = cy + _math.sin(rad) * p_rads[i]
            size = 2 + (i % 2)
            if i == 3:
                col = pal["orange1"]
            else:
                col = pal["part"]
            ab = 0.8 + amp_draw * 0.6
            try:
                _id = self.canvas.create_oval(x - size * ab, y - size * ab,
                                               x + size * ab, y + size * ab,
                                               fill=col, outline="")
                self._ids.append(_id)
            except Exception:
                pass

        # ── L5. 4 AYRI SEGMENTLİ ORBİTAL ENERJİ BAĞLANTI ÇİZGİLERİ
        # Her orbital: farklı yarıçap / farklı elips tilt (rx != ry) / 2-4 arc segment
        # / 1-2 parlak düğüm (cyan veya orange) / farklı RPM + yön
        orbit_col_cyan = pal["ring2"]
        orbit_col_orange = pal["orange1"]
        orbit_col_mid = pal["glow_mid"]
        # (radius_factor, tilt_rx_factor, tilt_ry_factor, rpm_sign_abs, segments N,
        #  color_idx 0=cyan 1=orange 2=midcyan, width_base, node count, node color idx)
        orbit_defs = (
            (0.56, 1.00, 0.70,  0.055, 3, 0, 1.0, 1, 0),  # Orbital 1: iç, yatay elips, 3 seg, cyan
            (0.74, 0.78, 1.00, -0.085, 2, 1, 1.1, 1, 1),  # Orbital 2: orta-dış, dikey elips, ters yön, 2 seg, orange
            (0.90, 1.03, 0.88,  0.115, 4, 2, 0.9, 2, 0),  # Orbital 3: dış, hafif yatay, 4 seg, mid-cyan, 2 node
            (1.05, 0.86, 1.05, -0.045, 3, 0, 1.2, 2, 1),  # Orbital 4: en dış (asset dışı), ters, 3 seg, orange node x2
        )
        for io, orb in enumerate(orbit_defs):
            rf, trx, try_, rpm, nseg, ci, wb, nn, nci = orb
            ro = int(R * rf * amp_grow)
            rx_ = int(ro * trx)
            ry_ = int(ro * try_)
            col_c = (orbit_col_cyan, orbit_col_orange, orbit_col_mid)[ci]
            col_n = (orbit_col_cyan, orbit_col_orange, orbit_col_mid)[nci]
            w_orb = max(0.8, wb * amp_line)
            start0 = (t_ang * 60.0 * rpm) % 360
            # Segments: N equal steps around 360; each arc extent ~ (0.58 / N) * 360
            extent_each = int(360 / nseg * 0.58)
            gap_each = 360 // nseg
            for iseg in range(nseg):
                seg_start = (start0 + iseg * gap_each) % 360
                try:
                    # Elliptical arc approximation via oval with rx != ry → görünür farklı tilt
                    _id = self.canvas.create_arc(
                        (cx - rx_, cy - ry_, cx + rx_, cy + ry_),
                        start=seg_start, extent=extent_each, style="arc",
                        outline=col_c, width=w_orb,
                    )
                    self._ids.append(_id)
                except Exception:
                    pass
            # Energy nodes (1 or 2 per orbital): place at midpoint of first 1-2 segments
            for inod in range(nn):
                n_start = (start0 + inod * gap_each + extent_each / 2) % 360
                n_rad = _math.radians(n_start)
                # Use ellipse point (rx*cos, ry*sin) → positioned correctly on tilted orbital
                nx = cx + _math.cos(n_rad) * rx_
                ny = cy + _math.sin(n_rad) * ry_
                n_size = 1.5 + 1.5 * amp_draw + (0 if inod == 0 else 0.4)
                try:
                    _id = self.canvas.create_oval(
                        nx - n_size, ny - n_size, nx + n_size, ny + n_size,
                        fill=col_n, outline="",
                    )
                    self._ids.append(_id)
                except Exception:
                    pass

        # ── L6. Bottom MUVAHHID Title + Real 8 Phase Subtitle (canonical map korunur)
        y_b = cy + R - 10
        try:
            _id = self.canvas.create_text(cx, y_b, text="MUVAHHİD", fill="#f0fcff",
                                          font=("Segoe UI", 20, "bold"))
            self._ids.append(_id)
        except Exception:
            pass
        sub_map = {
            "HAZIR": "Hazır. Komut bekleniyor.",
            "DİNLİYOR": "Sizi dinliyor...",
            "DÜŞÜNÜYOR": "Düşünüyor...",
            "ÇALIŞIYOR": "Çalışıyor. Görev yürütülüyor.",
            "KONUŞUYOR": "Konuşuyor...",
            "ONAY BEKLİYOR": "Onay bekleniyor.",
            "TAMAMLANDI": "Tamamlandı.",
            "HATA": "Hata oluştu.",
        }
        sub = sub_map.get(self._phase_tr, sub_map["HAZIR"])
        try:
            _id = self.canvas.create_text(cx, y_b + 22, text=sub, fill=pal["ring2"],
                                          font=("Segoe UI", 12))
            self._ids.append(_id)
        except Exception:
            pass

    def destroy(self) -> None:  # pragma: no cover
        self._destroyed = True
        if self._after_id is not None:
            try:
                self.canvas.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.canvas.destroy()
        except Exception:
            pass

    def pack(self, **kw: Any) -> None:  # pragma: no cover
        try:
            self.canvas.pack(**kw)
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas.pack] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
        try:
            self.canvas.after(2, self.redraw)
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas.pack after redraw] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass

    def grid(self, **kw: Any) -> None:  # pragma: no cover
        try:
            self.canvas.grid(**kw)
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas.grid] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
        try:
            self.canvas.after(2, self.redraw)
        except Exception as exc:
            try:
                print(f"[V4CoreCanvas.grid after redraw] FAIL: {type(exc).__name__}: {exc}\n"
                      + _traceback.format_exc(limit=3), flush=True)
            except Exception:
                pass
