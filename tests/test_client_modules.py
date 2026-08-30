from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hermes.config_client import DEFAULT_SERVER_URL, apply_server_defaults, default_server_url
from hermes.cursor_bridge import cursor_status, find_cursor_executable, run_cursor_cli
from hermes.ui.modern_theme import BG, CYAN
from hermes.voice_listener import WAKE_WORDS, VoiceListener
from hermes.voice.wake_word import detect_wake_word


def test_default_server_url():
    assert default_server_url() == "http://50.6.226.228:8642"
    payload = apply_server_defaults({})
    assert payload["server"]["url"] == DEFAULT_SERVER_URL
    assert payload["server"]["model"]


def test_apply_server_defaults_keeps_existing_url():
    payload = apply_server_defaults({"server": {"url": "http://127.0.0.1:8642"}})
    assert payload["server"]["url"] == "http://127.0.0.1:8642"


def test_voice_listener_wake_words():
    assert WAKE_WORDS == ("abi", "akhi", "dostum")
    listener = object.__new__(VoiceListener)
    listener.wake_words = WAKE_WORDS
    wake, rest = listener.parse_wake("abi chrome ac")
    assert wake == "abi"
    assert rest == "chrome ac"
    assert detect_wake_word("dostum merhaba")[0] == "dostum"


def test_modern_theme_palette():
    assert BG.startswith("#")
    assert CYAN.startswith("#")


def test_cursor_status_shape():
    status = cursor_status()
    assert "available" in status
    assert "path" in status


def test_run_cursor_cli_without_binary():
    with patch("hermes.cursor_bridge.find_cursor_executable", return_value=None):
        result = run_cursor_cli(["--version"])
    assert result["ok"] is False
    assert "bulunamadi" in result["error"]


def test_ocr_screen_without_image():
    from hermes.vision import ocr_screen

    with patch("hermes.vision.capture_screen", return_value={"path": ""}):
        result = ocr_screen()
    assert result["ocr"] is False
    assert result["text"] == ""


def test_find_cursor_respects_env(tmp_path: Path):
    fake = tmp_path / "Cursor.exe"
    fake.write_text("x", encoding="utf-8")
    with patch.dict("os.environ", {"CURSOR_PATH": str(fake)}, clear=False):
        found = find_cursor_executable()
    assert found == fake
