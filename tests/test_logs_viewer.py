from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hermes.ui.logs_viewer import open_log_file, read_log_tail


def test_read_log_tail_missing_file(tmp_path, monkeypatch):
    missing = tmp_path / "missing.log"
    monkeypatch.setattr("hermes.ui.logs_viewer.app_log_path", lambda: missing)

    lines, message = read_log_tail(max_lines=200)

    assert lines == []
    assert message is not None
    assert "henuz" in message.lower() or "olusturulmadi" in message.lower()


def test_read_log_tail_returns_last_lines(tmp_path, monkeypatch):
    log_file = tmp_path / "app.log"
    log_file.write_text("\n".join(f"line-{i}" for i in range(1, 251)), encoding="utf-8")
    monkeypatch.setattr("hermes.ui.logs_viewer.app_log_path", lambda: log_file)

    lines, message = read_log_tail(max_lines=200)

    assert message is None
    assert len(lines) == 200
    assert lines[0] == "line-51"
    assert lines[-1] == "line-250"


def test_read_log_tail_empty_file(tmp_path, monkeypatch):
    log_file = tmp_path / "app.log"
    log_file.write_text("", encoding="utf-8")
    monkeypatch.setattr("hermes.ui.logs_viewer.app_log_path", lambda: log_file)

    lines, message = read_log_tail()

    assert lines == []
    assert message == "Log dosyasi bos."


def test_open_log_file_missing(tmp_path, monkeypatch):
    missing = tmp_path / "missing.log"
    monkeypatch.setattr("hermes.ui.logs_viewer.app_log_path", lambda: missing)

    ok, detail = open_log_file()

    assert ok is False
    assert "bulunamadi" in detail.lower()


def test_open_log_file_success(tmp_path, monkeypatch):
    log_file = tmp_path / "app.log"
    log_file.write_text("hello", encoding="utf-8")
    monkeypatch.setattr("hermes.ui.logs_viewer.app_log_path", lambda: log_file)

    with patch("hermes.ui.logs_viewer.os.startfile") as startfile:
        ok, detail = open_log_file()

    assert ok is True
    assert str(log_file) in detail
    startfile.assert_called_once_with(log_file)
