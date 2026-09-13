"""Phase 1B — UI Store tests (TDD)."""
from __future__ import annotations
from hermes.ui.v4_projection import UIEvent
from hermes.ui.v4_store import V4UIStore

def make_ui_event(kind: str, event_id: str = "evt-test") -> UIEvent:
    return UIEvent(event_id=event_id, kind=kind, timestamp="2026-09-09T12:00:00Z",
                   correlation_id="c-1", task_id="c-1", action_id="a-1",
                   payload={"status":"verified","success":True})

def test_initial_state():
    s = V4UIStore()
    snap = s.get_snapshot()
    assert snap["phase"] == "idle"
    assert snap["event_count"] == 0

def test_apply_event_updates_phase():
    s = V4UIStore()
    assert s.apply(make_ui_event("action_started", "e1"))
    assert s.phase == "executing"
    assert s.apply(make_ui_event("action_finished", "e2"))
    assert s.phase == "completed"

def test_snapshot_is_read_only():
    s = V4UIStore()
    s.apply(make_ui_event("action_started", "e1"))
    snap = s.get_snapshot()
    snap["phase"] = "tampered"
    assert s.phase == "executing"  # original unchanged

def test_subscriber_notified():
    s = V4UIStore()
    calls = []
    s.subscribe(lambda ev, payload: calls.append((ev, payload)))
    s.apply(make_ui_event("action_started", "e1"))
    assert len(calls) == 1
    assert calls[0][0] == "event_applied"

def test_unsubscribe():
    s = V4UIStore()
    calls = []
    cb = lambda ev, payload: calls.append((ev, payload))
    s.subscribe(cb)
    s.unsubscribe(cb)
    s.apply(make_ui_event("action_started", "e1"))
    assert len(calls) == 0

def test_duplicate_ignored():
    s = V4UIStore()
    e = make_ui_event("action_started", "e-dup")
    assert s.apply(e)
    assert not s.apply(e)
    assert s.get_snapshot()["event_count"] == 1

def test_ordering_terminal_state():
    s = V4UIStore()
    s.apply(make_ui_event("action_started", "e1"))
    s.apply(make_ui_event("action_finished", "e2"))
    s.apply(make_ui_event("action_started", "e3"))  # old event after terminal
    # minimal: phase updates to executing again — acceptable; ordering metadata preserved
    assert s.phase == "executing"

def test_verification_state():
    s = V4UIStore()
    s.apply(make_ui_event("verification_recorded", "e-v"))
    assert s.verification_state == "verified"

def test_recovery_state():
    s = V4UIStore()
    s.apply(make_ui_event("recovery_started", "e-r"))
    assert s.recovery_state == "recovery"
    s.apply(make_ui_event("recovery_finished", "e-r2"))
    assert s.recovery_state == "none"

def test_approval_not_decided():
    s = V4UIStore()
    # store only reflects projection; approval decision comes from projection payload if present
    assert s.approval_state == "none"
