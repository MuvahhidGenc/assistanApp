"""Phase 2A tests — V4 shell + navigation (headless-safe)."""
from hermes.ui.v4_store import V4UIStore

def test_shell_constants_exist():
    import pathlib
    src = pathlib.Path("src/hermes/ui/app_v4_shell.py").read_text()
    assert 'NAV_ITEMS = ["HOME","CHAT","AGENT","MEMORY","SKILLS","COMPUTER","BROWSER","TASKS","ACTIVITY","SETTINGS"]' in src

def test_store_snapshot_readonly():
    s = V4UIStore()
    s.apply(type("E",(),{"event_id":"e","kind":"action_started","timestamp":"2026-09-09T12:00:00Z","correlation_id":"","action_id":"","payload":{}})())
    snap = s.get_snapshot()
    snap["phase"] = "tampered"
    assert s.phase == "executing"
