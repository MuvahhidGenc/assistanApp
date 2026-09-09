"""Phase 1B — UI State Store (read-only projection only).

Not agent / executor / memory / policy / log.
Only holds projected UI state from V3 canonical events.
"""
from __future__ import annotations
from typing import Callable, Any
from hermes.ui.v4_projection import UIEvent

class V4UIStore:
    def __init__(self) -> None:
        self._events: list[UIEvent] = []
        self._seen_ids: set[str] = set()
        self._subs: list[Callable[[str, Any], None]] = []
        self.phase = "idle"
        self.approval_state = "none"
        self.verification_state = "pending"
        self.recovery_state = "none"
        self.error_state = None

    def apply(self, event: UIEvent) -> bool:
        if event.event_id in self._seen_ids:
            return False
        self._seen_ids.add(event.event_id)
        self._events.append(event)
        # minimal lifecycle update (source-backed only)
        kind = event.kind
        if kind == "action_started":
            self.phase = "executing"
        elif kind == "action_finished":
            self.phase = "completed" if event.payload.get("success") else "failed"
        elif kind == "verification_recorded":
            self.verification_state = event.payload.get("status") or "unknown"
        elif kind == "recovery_started":
            self.recovery_state = "recovery"
        elif kind == "recovery_finished":
            self.recovery_state = "none"
        # notify subscribers
        for cb in self._subs:
            try:
                cb("event_applied", event)
            except Exception:
                pass
        return True

    def subscribe(self, cb: Callable[[str, Any], None]) -> None:
        self._subs.append(cb)

    def unsubscribe(self, cb: Callable[[str, Any], None]) -> None:
        if cb in self._subs:
            self._subs.remove(cb)

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "approval_state": self.approval_state,
            "verification_state": self.verification_state,
            "recovery_state": self.recovery_state,
            "error_state": self.error_state,
            "event_count": len(self._events),
            "latest_event_id": self._events[-1].event_id if self._events else None,
        }
