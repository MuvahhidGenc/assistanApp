from __future__ import annotations

from typing import Any

# ── Sci-fi palette (JARVIS / holographic) ──
BG = "#010508"
BG_DEEP = "#000204"
CARD = "#060c14"
CARD_BORDER = "#0f2840"
CARD_GLOW = "#0a1e30"

NEON_CYAN = "#00e5ff"
NEON_BLUE = "#0077b6"
NEON_MAGENTA = "#ff2a6d"
NEON_GOLD = "#ffd60a"
HOLO_GREEN = "#05ffa1"

# Backward-compatible aliases
CYAN = NEON_CYAN
BLUE = NEON_BLUE
MAGENTA = NEON_MAGENTA
GREEN = HOLO_GREEN
AMBER = NEON_GOLD
PURPLE = "#b5179e"
ORANGE = "#ff6b35"

GRID = "#0a1628"
DIM = "#4a6a7a"
TEXT = "#d6f4ff"
TEXT_BRIGHT = "#ecfeff"
MUTED = "#64748b"

INPUT_BG = "#040810"
BUBBLE_USER = "#0a2540"
BUBBLE_AI = "#0c1220"
BUBBLE_SYSTEM = "#2d1035"
BUBBLE_STATUS = "#0f1a28"
BUTTON = "#0c1824"
BUTTON_HOVER = "#0e3d52"

FONT_MONO = ("Consolas", 11)
FONT_HUD = ("Segoe UI", 11)
FONT_TITLE = ("Segoe UI", 22, "bold")
FONT_HERO = ("Segoe UI", 26, "bold")


def apply_appearance() -> None:
    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def holo_card(ctk: Any, parent: Any, **kwargs: Any) -> Any:
    opts = {
        "fg_color": CARD,
        "corner_radius": 6,
        "border_width": 1,
        "border_color": CARD_BORDER,
    }
    opts.update(kwargs)
    return ctk.CTkFrame(parent, **opts)


def card(ctk: Any, parent: Any, **kwargs: Any) -> Any:
    return holo_card(ctk, parent, **kwargs)


def section_label(ctk: Any, parent: Any, text: str, **kwargs: Any) -> Any:
    display = text if text.startswith("◈") else f"◈ {text.upper()}"
    opts = {
        "text": display,
        "text_color": NEON_CYAN,
        "font": ctk.CTkFont(family="Consolas", size=11, weight="bold"),
        "anchor": "w",
    }
    opts.update(kwargs)
    return ctk.CTkLabel(parent, **opts)


def ghost_button(ctk: Any, parent: Any, text: str, command: Any, **kwargs: Any) -> Any:
    opts = {
        "text": text,
        "fg_color": BUTTON,
        "hover_color": BUTTON_HOVER,
        "text_color": TEXT,
        "border_width": 1,
        "border_color": CARD_BORDER,
        "corner_radius": 4,
        "command": command,
        "height": 28,
        "font": ctk.CTkFont(family="Consolas", size=11),
    }
    opts.update(kwargs)
    return ctk.CTkButton(parent, **opts)


def primary_button(ctk: Any, parent: Any, text: str, command: Any, **kwargs: Any) -> Any:
    opts = {
        "text": text,
        "fg_color": NEON_CYAN,
        "hover_color": NEON_BLUE,
        "text_color": BG_DEEP,
        "border_width": 0,
        "corner_radius": 4,
        "command": command,
        "height": 34,
        "font": ctk.CTkFont(family="Consolas", size=12, weight="bold"),
    }
    opts.update(kwargs)
    return ctk.CTkButton(parent, **opts)


def neon_button(ctk: Any, parent: Any, text: str, command: Any, *, accent: str = "", **kwargs: Any) -> Any:
    color = accent or MAGENTA
    opts = {
        "text": text,
        "fg_color": "#120818",
        "hover_color": color,
        "text_color": color,
        "border_width": 1,
        "border_color": color,
        "corner_radius": 4,
        "command": command,
        "height": 36,
        "font": ctk.CTkFont(family="Consolas", size=11, weight="bold"),
    }
    opts.update(kwargs)
    return ctk.CTkButton(parent, **opts)


def selectable_log(ctk: Any, parent: Any, **kwargs: Any) -> Any:
    box = ctk.CTkTextbox(
        parent,
        fg_color=BG_DEEP,
        text_color=TEXT,
        border_width=1,
        border_color=CARD_BORDER,
        corner_radius=4,
        wrap="word",
        activate_scrollbars=True,
        font=ctk.CTkFont(family="Consolas", size=12),
        **kwargs,
    )
    box.configure(state="normal")
    return box


def bind_copy_shortcuts(widget: Any, root: Any) -> None:
    def _copy(_event: object = None) -> str:
        try:
            text = widget.get("sel.first", "sel.last")
        except Exception:
            return "break"
        if text:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
        return "break"

    def _select_all(_event: object = None) -> str:
        widget.tag_add("sel", "1.0", "end-1c")
        return "break"

    widget.bind("<Control-c>", _copy)
    widget.bind("<Control-C>", _copy)
    widget.bind("<Control-a>", _select_all)
    widget.bind("<Control-A>", _select_all)
