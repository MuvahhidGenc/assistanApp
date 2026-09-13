"""MUVAHHİD V4 — App Shell and Navigation (Task 1 brand + HUD header + Turkish nav).

Canonical architecture preserved:
  V3 Runtime → EventEnvelope → v4_projection → V4UIStore → V4 UI shell

NAV_ITEMS constant (L14 English keys) MUST remain unchanged per existing
AST test contract. Display labels are mapped via _NAV_TR Turkish dict.
"""
from __future__ import annotations
import datetime as _dt
from typing import Any

try:
    import customtkinter as ctk
except ModuleNotFoundError:  # Linux / no GUI env — shell import deferred to runtime
    ctk = None  # type: ignore

try:
    from hermes.ui.worker import BackgroundWorker
except Exception:  # pragma: no cover
    BackgroundWorker = None  # type: ignore

from hermes.ui.modern_theme import (
    apply_appearance, CARD, BG, NEON_CYAN, NEON_BLUE, CARD_BORDER,
    FONT_MONO, MUTED, TEXT_BRIGHT, BUTTON_HOVER, TEXT, HOLO_GREEN,
)
from hermes.ui.v4_design import (
    _NAV_TR,
    _phase_to_tr,
    V4HUDStatusChip,
    status_color,
)
from hermes.ui.v4_store import V4UIStore
from hermes.ui.v4_home import V4Home

try:
    from hermes.ui.v4_bridge import bind_store_events
except Exception:  # pragma: no cover — import-safe
    def bind_store_events(*_a, **_k):  # type: ignore[misc]
        return None

try:
    from hermes.ui.v4_chat import V4Chat
except Exception:  # pragma: no cover - import-safe
    V4Chat = None  # type: ignore
try:
    from hermes.ui.v4_agent import V4Agent
except Exception:  # pragma: no cover
    V4Agent = None  # type: ignore
try:
    from hermes.ui.v4_memory import V4Memory
except Exception:  # pragma: no cover
    V4Memory = None  # type: ignore
try:
    from hermes.ui.v4_skills import V4Skills
except Exception:  # pragma: no cover
    V4Skills = None  # type: ignore
try:
    from hermes.ui.v4_computer import V4Computer
except Exception:  # pragma: no cover
    V4Computer = None  # type: ignore
try:
    from hermes.ui.v4_browser import V4Browser
except Exception:  # pragma: no cover
    V4Browser = None  # type: ignore
try:
    from hermes.ui.v4_tasks import V4Tasks
except Exception:  # pragma: no cover
    V4Tasks = None  # type: ignore
try:
    from hermes.ui.v4_activity import V4Activity
except Exception:  # pragma: no cover
    V4Activity = None  # type: ignore
try:
    from hermes.ui.v4_settings import V4Settings
except Exception:  # pragma: no cover
    V4Settings = None  # type: ignore


# ── PERMANENTLY UNCHANGED per AST test contract (display uses _NAV_TR below) ──
NAV_ITEMS = ["HOME","CHAT","AGENT","MEMORY","SKILLS","COMPUTER","BROWSER","TASKS","ACTIVITY","SETTINGS"]

PAGE_REGISTRY: dict[str, Any] = {
    "HOME": V4Home,
    "CHAT": V4Chat,
    "AGENT": V4Agent,
    "MEMORY": V4Memory,
    "SKILLS": V4Skills,
    "COMPUTER": V4Computer,
    "BROWSER": V4Browser,
    "TASKS": V4Tasks,
    "ACTIVITY": V4Activity,
    "SETTINGS": V4Settings,
}

_BRAND_TITLE = "MUVAHHİD V4"
_BRAND_SUB = "AI BİLGİSAYAR AJANI"
_NAV_ICONS: dict[str, str] = {
    "HOME": "⌂", "CHAT": "◆", "AGENT": "◉", "MEMORY": "▦",
    "SKILLS": "❖", "COMPUTER": "▣", "BROWSER": "◎", "TASKS": "✓",
    "ACTIVITY": "☰", "SETTINGS": "✦",
}


class V4AppShell(ctk.CTk):
    def __init__(
        self,
        store: V4UIStore | None = None,
        on_send_message: Any | None = None,
        worker: Any | None = None,
        config_path: str | None = None,
        debug: bool = False,
        start_worker: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        apply_appearance()
        self.store = store or V4UIStore()
        self._worker_owned_here = False
        self._worker = worker
        if (
            self._worker is None
            and start_worker
            and BackgroundWorker is not None
            and callable(BackgroundWorker)
        ):
            try:
                self._worker = BackgroundWorker(config_path=config_path, debug=debug)
                self._worker_owned_here = True
                try:
                    self._worker.start()
                except Exception:  # pragma: no cover - runtime only
                    pass
            except Exception:  # pragma: no cover - runtime only
                self._worker = worker
                self._worker_owned_here = False
        # Chat bridge priority: (1) worker.send_message (canonical V3 path),
        # (2) explicit on_send_message kwarg (tests / legacy integrations).
        if self._worker is not None and hasattr(self._worker, "send_message") and callable(self._worker.send_message):
            self._on_send_message = self._worker.send_message
        else:
            self._on_send_message = on_send_message
        # Wire worker emit → store: "message", "approval_*", "error" events.
        # The canonical worker already emits these (worker.py L91/L119/L313/L377
        # /L409/L473 — no backend code paths are altered).
        if self._worker is not None:
            try:
                bind_store_events(self._worker, self.store)
            except Exception as exc:  # pragma: no cover
                try:
                    print(f"[V4AppShell] bind_store_events FAIL: {type(exc).__name__}: {exc}")
                except Exception:
                    pass
            # Replay any messages already accumulated in worker.state.messages
            # so the chat timeline is populated immediately if the worker was
            # started before this V4 shell was mounted (source-of-truth only).
            try:
                state_msgs = getattr(getattr(self._worker, "state", None), "messages", None) or []
                ts_now = _dt.datetime.now().strftime("%H:%M")
                for m in list(state_msgs):
                    role = str(getattr(m, "role", "system") or "system")
                    text = str(getattr(m, "text", "") or "").strip()
                    if text:
                        m_ts = getattr(m, "timestamp", None) or ts_now
                        if isinstance(m_ts, _dt.datetime):
                            m_ts = m_ts.strftime("%H:%M")
                        else:
                            m_ts = str(m_ts)[:5] or ts_now
                        self.store.append_chat_message(role, text, timestamp=m_ts)
            except Exception:
                pass
        self.title(_BRAND_TITLE)
        self.geometry("1200x800")
        self.minsize(1100, 720)
        self.configure(fg_color=BG)
        self._nav_active = "HOME"
        self._mounted_page: Any = None
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._clock_after_id: str | None = None
        self._store_after_id: str | None = None
        self._build_ui()
        # Clock + store subscriber: start shortly after layout
        try:
            self.after(50, self._tick_clock)
        except Exception:
            pass
        try:
            self.after(100, self._on_snapshot_update)
        except Exception:
            pass

    # ───────────────────────────── UI BUILD ─────────────────────────────
    def _build_ui(self):
        # ── HEADER (thin futuristic HUD bar) ─────────────────────────────
        self.header = ctk.CTkFrame(
            self, fg_color=CARD, height=56, corner_radius=0,
            border_width=1, border_color=CARD_BORDER,
        )
        self.header.pack(fill="x", side="top")
        self.header.pack_propagate(False)

        # LEFT: M lettermark canvas + brand string
        self._build_brand_cell(self.header).pack(side="left", padx=(14, 24), pady=6)

        # CENTER-LEFT: 3 HUD status chips — FAZ • BAĞLANTI • OLAY
        chip_group = ctk.CTkFrame(self.header, fg_color="transparent")
        chip_group.pack(side="left", padx=(4, 12), pady=6)
        snap = self.store.get_snapshot()
        phase_tr = _phase_to_tr(snap.get("phase") or "idle")
        phase_dot = status_color(phase_tr, default=HOLO_GREEN)
        # NB: F15 semantic honesty rule: event_count != connection.
        # Connection state is NOT in canonical snapshot → "BİLİNMİYOR"
        self.chip_phase = V4HUDStatusChip(
            chip_group, "FAZ", phase_tr, dot=phase_dot,
            key_color=MUTED, value_color=phase_dot,
        )
        self.chip_phase.pack(side="left", padx=(0, 14))
        self.chip_conn = V4HUDStatusChip(
            chip_group, "BAĞLANTI", "BİLİNMİYOR", dot=MUTED,
            key_color=MUTED, value_color=MUTED,
        )
        self.chip_conn.pack(side="left", padx=(0, 14))
        self.chip_event = V4HUDStatusChip(
            chip_group, "OLAY", str(snap.get("event_count") or 0),
            dot=NEON_CYAN, key_color=MUTED, value_color=TEXT_BRIGHT,
        )
        self.chip_event.pack(side="left")

        # RIGHT: 24h clock HH:MM (Consolas monospace)
        self.clock_label = ctk.CTkLabel(
            self.header, text="--:--", text_color=TEXT_BRIGHT,
            font=FONT_MONO, anchor="e", width=80,
        )
        self.clock_label.pack(side="right", padx=(0, 22), pady=6)

        # ── MAIN CONTAINER (narrow left nav + wide content) ──────────────
        main = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        main.pack(fill="both", expand=True)

        # LEFT SIDEBAR: 130px — quiet futuristic rail
        nav = ctk.CTkFrame(
            main, fg_color=CARD, width=130,
            border_color=CARD_BORDER, border_width=1, corner_radius=0,
        )
        nav.pack(side="left", fill="y", padx=0, pady=0)
        nav.pack_propagate(False)
        self._build_nav(nav)

        # Content container → page mounts here; page itself draws HUD corners
        self.content = ctk.CTkFrame(main, fg_color=BG, corner_radius=0)
        self.content.pack(side="left", fill="both", expand=True, padx=0, pady=0)
        home_cls = PAGE_REGISTRY["HOME"]
        if ctk is not None and callable(home_cls):
            try:
                self._mounted_page = home_cls(self.content, self.store, on_send_message=self._on_send_message, worker=self._worker)
            except TypeError:
                self._mounted_page = home_cls(self.content, self.store, on_send_message=self._on_send_message)
        # Paint initial nav selected state
        self._update_nav_selection()

    # ── Brand cell (M lettermark glow canvas + title/sub) ───────────────
    def _build_brand_cell(self, parent: Any) -> ctk.CTkFrame:
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        # M lettermark: small 34x34 canvas
        try:
            mark = ctk.CTkCanvas(
                cell, width=34, height=34, bg=CARD,
                highlightthickness=0, bd=0,
            )
            mark.pack(side="left", padx=(0, 10))
            # layered glow rings + M
            cx, cy, r = 17, 17, 15
            mark.create_oval(cx-r, cy-r, cx+r, cy+r, fill="#061523", outline="")
            mark.create_oval(cx-r+3, cy-r+3, cx+r-3, cy+r-3, fill="#0c2637", outline="")
            mark.create_oval(cx-r+6, cy-r+6, cx+r-6, cy+r-6, fill=NEON_CYAN, outline="")
            mark.create_text(cx, cy+1, text="M", fill="#ffffff",
                             font=("Segoe UI", 14, "bold"))
        except Exception:
            mark = None  # pragma: no cover
        # Brand text stack: MUVAHHİD V4 + AI BİLGİSAYAR AJANI
        txt = ctk.CTkFrame(cell, fg_color="transparent")
        txt.pack(side="left")
        ctk.CTkLabel(
            txt, text=_BRAND_TITLE, text_color=TEXT_BRIGHT,
            font=("Segoe UI", 15, "bold"), anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            txt, text=_BRAND_SUB, text_color=MUTED,
            font=("Segoe UI", 9), anchor="w",
        ).pack(anchor="w")
        return cell

    # ── Navigation sidebar builder (Turkish via _NAV_TR, minimal rail style) ─
    def _build_nav(self, nav_frame: ctk.CTkFrame) -> None:
        # Quiet top spacer (compact for minimal feel)
        ctk.CTkFrame(nav_frame, fg_color="transparent", height=8).pack(fill="x")

        for item in NAV_ITEMS:
            label = _NAV_TR.get(item, item)
            icon = _NAV_ICONS.get(item, "•")
            btn = ctk.CTkButton(
                nav_frame,
                text=f" {icon}  {label}",
                fg_color="transparent",
                hover_color=BUTTON_HOVER,
                text_color=TEXT,
                text_color_disabled=MUTED,
                font=("Segoe UI", 10),
                height=30,
                corner_radius=8,
                border_width=0,
                border_color=CARD,
                anchor="w",
                command=lambda i=item: self._set_nav(i),
            )
            btn.pack(fill="x", padx=6, pady=0)
            self._nav_buttons[item] = btn

        # Bottom quiet corner spacer
        ctk.CTkFrame(nav_frame, fg_color="transparent", height=8).pack(fill="x")

    # ───────────────────── NAV STATE + PAGE MOUNTING ────────────────────
    def _update_nav_selection(self) -> None:
        for item, btn in self._nav_buttons.items():
            if item == self._nav_active:
                try:
                    btn.configure(
                        fg_color="#0a2030",
                        border_width=2,
                        border_color="#00f7ff",
                        text_color="#00f7ff",
                        font=("Segoe UI", 10, "bold"),
                    )
                except Exception:
                    pass
            else:
                try:
                    btn.configure(
                        fg_color="transparent",
                        border_width=0,
                        border_color=CARD,
                        text_color=TEXT,
                        font=("Segoe UI", 10),
                    )
                except Exception:
                    pass

    def _set_nav(self, item: str):
        if item not in NAV_ITEMS:
            return
        self._nav_active = item
        self._update_nav_selection()
        # Call page-level destroy first if it has one (unsubscribes timers / listeners)
        prev = getattr(self, "_mounted_page", None)
        if prev is not None and hasattr(prev, "destroy") and callable(prev.destroy):
            try:
                prev.destroy()
            except Exception:
                pass
            self._mounted_page = None
        # Destroy all content children
        for w in self.content.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        # Mount the page from the canonical registry
        page_cls = PAGE_REGISTRY.get(item)
        if ctk is not None and callable(page_cls):
            try:
                if item in {"HOME", "CHAT"}:
                    try:
                        self._mounted_page = page_cls(self.content, self.store, on_send_message=self._on_send_message, worker=self._worker)
                    except TypeError:
                        self._mounted_page = page_cls(self.content, self.store, on_send_message=self._on_send_message)
                else:
                    self._mounted_page = page_cls(self.content, self.store)
            except Exception as exc:  # pragma: no cover
                err = ctk.CTkLabel(
                    self.content,
                    text=f"SAYFA YÜKLEME HATASI  •  {_NAV_TR.get(item, item)}  •  {exc}",
                    text_color="#ff2a6d",
                    font=("Consolas", 11),
                )
                err.pack(expand=True, padx=30, pady=30)
        else:
            # F19/F27 honest empty state: page module is not available
            ph = ctk.CTkLabel(
                self.content,
                text=f"{_NAV_TR.get(item, item)}  •  KULLANILAMIYOR",
                text_color=MUTED,
                font=("Segoe UI", 22, "bold"),
            )
            ph.pack(expand=True)

    # ───────────────────── HEADER STATUS LIVENESS ───────────────────────
    def _tick_clock(self) -> None:  # pragma: no cover
        try:
            now = _dt.datetime.now()
            self.clock_label.configure(text=now.strftime("%H:%M"))
        except Exception:
            pass
        try:
            self._clock_after_id = self.after(15000, self._tick_clock)
        except Exception:
            self._clock_after_id = None

    def _on_snapshot_update(self) -> None:  # pragma: no cover
        try:
            snap = self.store.get_snapshot()
            phase_tr = _phase_to_tr(snap.get("phase") or "idle")
            p_dot = status_color(phase_tr, default=HOLO_GREEN)
            self.chip_phase.set(value_label=phase_tr, dot=p_dot, value_color=p_dot)
            self.chip_event.set(value_label=str(snap.get("event_count") or 0))
        except Exception:
            pass
        try:
            self._store_after_id = self.after(1000, self._on_snapshot_update)
        except Exception:
            self._store_after_id = None

    # ───────────────────── SAFE DESTROY (no leaky timers) ───────────────
    def destroy(self) -> None:  # pragma: no cover - lifecycle
        for timer in (self._clock_after_id, self._store_after_id):
            if timer is not None:
                try:
                    self.after_cancel(timer)
                except Exception:
                    pass
        self._clock_after_id = None
        self._store_after_id = None
        # Page-level destroy (cancels audio probe, core animation, v4_store subs)
        prev = getattr(self, "_mounted_page", None)
        if prev is not None and hasattr(prev, "destroy") and callable(prev.destroy):
            try:
                prev.destroy()
            except Exception:
                pass
        # Worker teardown: only stop if this shell instance owns the worker
        # (i.e. it was created inside __init__, not injected by the caller).
        worker = getattr(self, "_worker", None)
        owned = bool(getattr(self, "_worker_owned_here", False))
        if worker is not None and owned and hasattr(worker, "stop") and callable(worker.stop):
            try:
                worker.stop(timeout=3.0)
            except Exception:
                pass
            self._worker = None
        super().destroy()


if __name__ == "__main__":
    from hermes.utils.logging import setup_app_logging

    try:
        setup_app_logging()
    except Exception:
        pass

    from hermes.ui.v4_store import V4UIStore
    store = V4UIStore()
    app = V4AppShell(store=store)
    try:
        app.mainloop()
    finally:
        try:
            app.destroy()
        except Exception:
            pass
