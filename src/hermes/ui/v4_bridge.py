"""Phase 1B minimal bridge — event/command separation.

Worker → Store via projected UI events (read-only).
No backend mutation; no new execution path.
"""
from __future__ import annotations
from hermes.ui.v4_store import V4UIStore

def bind_store_events(worker, store: V4UIStore) -> None:
    """Wire worker callbacks to V4UIStore subscription (not execution)."""
    # The actual projection happens in Phase 1A (v4_projection.py).
    # This bridge only ensures store subsections can observe worker state
    # without altering the worker or backend.
    def _subscriber(evt: str, payload) -> None:
        if evt == "approval_required":
            store.approval_state = "required"
        elif evt == "approval_resolved":
            store.approval_state = "resolved"
        elif evt == "approval_timeout":
            store.approval_state = "timeout"
        elif evt == "error":
            store.error_state = payload.get("message") if isinstance(payload, dict) else str(payload)
    store.subscribe(_subscriber)
