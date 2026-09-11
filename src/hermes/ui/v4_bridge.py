"""Phase 1B minimal bridge — event/command separation.

Worker → Store via projected UI events (read-only).
No backend mutation; no new execution path.
"""
from __future__ import annotations
from hermes.ui.v4_store import V4UIStore

def bind_store_events(worker, store: V4UIStore) -> None:
    """Wire worker callbacks to V4UIStore subscription (not execution).

    - Preserves existing worker.on_event chain by wrapping the previous
      handler if one was installed — no other UI module loses its
      callback wiring.
    - Only projects READ-ONLY signals the canonical worker already
      emits; no backend payloads are mutated.
    """
    _prev_on_event = getattr(worker, "on_event", None)

    def _subscriber(evt: str, payload) -> None:
        # Pass-through so the previous worker.on_event handler (if any)
        # still fires — order: outer consumers first, store second.
        if _prev_on_event is not None and callable(_prev_on_event):
            try:
                _prev_on_event(evt, payload or {})
            except Exception:
                pass
        # Bridge: worker's canonical "message" emit → store chat list.
        # Roles: "user" | "assistant" | "status" | "system" (worker.py
        # L119/L313/L321/L377/L409/L473/L525/L543/L549).
        if evt == "message" and isinstance(payload, dict):
            role = str(payload.get("role") or "system")
            text = str(payload.get("text") or "").strip()
            if text:
                store.append_chat_message(
                    role,
                    text,
                    source=str(payload.get("source")) if payload.get("source") else None,
                )
        elif evt == "approval_required":
            store.approval_state = "required"
        elif evt == "approval_resolved":
            store.approval_state = "resolved"
        elif evt == "approval_timeout":
            store.approval_state = "timeout"
        elif evt == "error":
            msg = payload.get("message") if isinstance(payload, dict) else str(payload)
            if msg:
                store.append_chat_message("system", msg)
                store.error_state = msg

    worker.on_event = _subscriber
