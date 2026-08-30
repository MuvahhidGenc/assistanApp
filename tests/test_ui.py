from hermes.ui.hud_stats import collect_hud_stats
from hermes.ui.icon import create_tray_icon
from hermes.ui.notifications import notify, notify_task_completed
from hermes.ui.state import ConnectionStatus, UIState


def test_ui_state_connection_labels():
    state = UIState(connection=ConnectionStatus.CONNECTED)
    assert state.connection_label() == "Bagli"
    assert state.connection_color() == "#22c55e"


def test_ui_state_snapshot():
    from hermes.ui.state import ActivityMode

    state = UIState()
    state.set_voice(voice_enabled=True, wake_word_enabled=False)
    state.set_activity(ActivityMode.LISTENING)
    snap = state.snapshot()
    assert snap["voice_enabled"] is True
    assert snap["wake_word_enabled"] is False
    assert snap["activity"] == "listening"


def test_ui_state_append_message():
    state = UIState()
    msg = state.append_message("user", "merhaba")
    assert msg.role == "user"
    assert len(state.messages) == 1


def test_create_tray_icon():
    image = create_tray_icon(32)
    assert image.size == (32, 32)


def test_notify_disabled():
    assert notify("t", "m", enabled=False) is False


def test_collect_hud_stats_has_required_fields():
    stats = collect_hud_stats()
    assert 0 <= stats["cpu"] <= 100
    assert stats["ram_total"] > 0
    assert ":" in stats["uptime"]
    assert stats["host"]
    assert stats["clock"]


def test_notify_task_completed_truncates():
    from unittest.mock import patch

    with patch("hermes.ui.notifications.notify", return_value=True) as mocked:
        long_text = "x" * 300
        assert notify_task_completed(long_text) is True
        assert len(mocked.call_args.args[1]) <= 200
