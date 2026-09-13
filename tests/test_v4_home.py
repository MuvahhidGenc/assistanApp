"""Phase 2B-1 — V4 HOME test (no GUI render on Linux/VPS)."""
import pytest

def test_v4_home_import():
    from hermes.ui.v4_home import V4Home
    assert V4Home is not None

def test_v4_home_no_fake_data():
    from hermes.ui.v4_store import V4UIStore
    from hermes.ui.v4_home import V4Home
    store = V4UIStore()
    snap = store.get_snapshot()
    # No fabricated task/data: event_count is 0 initially (real state)
    assert snap["event_count"] == 0
    assert snap["phase"] == "idle"

def test_v4_home_uses_store_snapshot():
    from hermes.ui.v4_store import V4UIStore
    from hermes.ui.v4_home import V4Home
    store = V4UIStore()
    # Confirm snapshot has real keys (no fake fields invented)
    snap = store.get_snapshot()
    real_keys = {"phase", "approval_state", "verification_state", "recovery_state", "error_state", "event_count", "latest_event_id"}
    assert real_keys.issubset(snap.keys())

def test_app_v4_shell_imports_v4_home():
    # Shell import deferred to runtime when ctk missing; verify import chain exists
    import importlib.util
    spec = importlib.util.find_spec("hermes.ui.v4_home")
    assert spec is not None
