"""Internal mission telemetry must not reach user chat."""
from __future__ import annotations

from hermes.voice.spoken import is_internal_chat_text, sanitize_chat_reply


def test_mission_telemetry_detected() -> None:
    assert is_internal_chat_text("Mission tamamlandi:\nbrowser.navigate yetenegini kullaniyorum")
    assert is_internal_chat_text("screen.observe yetenegini kullaniyorum")


def test_sanitize_falls_back() -> None:
    out = sanitize_chat_reply("Mission tamamlandi:\n- browser.navigate yetenegini kullaniyorum")
    assert "Mission" not in out
    assert "yetenegini" not in out.casefold()
    assert out


def test_natural_reply_kept() -> None:
    assert sanitize_chat_reply("Tamam abi, Not Defteri'ni açtım.") == "Tamam abi, Not Defteri'ni açtım."
