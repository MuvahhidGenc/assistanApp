from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.config.settings_store import (
    SettingsFormData,
    load_settings_form,
    save_settings_form,
    validate_api_key,
    validate_api_url,
    validate_settings_form,
)


def test_validate_api_url_rejects_empty():
    assert validate_api_url("") == "API URL bos olamaz."


def test_validate_api_url_rejects_invalid():
    assert validate_api_url("ftp://example.com") == "API URL http:// veya https:// ile baslamalidir."
    assert validate_api_url("http://") == "Gecersiz API URL."


def test_validate_api_url_accepts_http():
    assert validate_api_url("http://50.6.226.228:8642") is None
    assert validate_api_url("https://hermes.example.com") is None


def test_app_settings_load_reads_sessions(tmp_path):
    from hermes.config.settings import AppSettings

    cfg = tmp_path / "default.yaml"
    cfg.write_text(
        "\n".join(
            [
                "server:",
                "  url: http://50.6.226.228:8642",
                "sessions:",
                "  record_sessions: true",
                "  session_ttl_seconds: 1800",
                '  session_key: "hermes-pc-session"',
            ]
        ),
        encoding="utf-8",
    )
    settings = AppSettings.load(cfg)
    assert settings.sessions.record_sessions is True
    assert settings.sessions.session_ttl_seconds == 1800
    assert settings.sessions.session_key == "hermes-pc-session"


def test_validate_api_key_rejects_empty():
    assert validate_api_key("") == "API key/token bos olamaz."
    assert validate_api_key("   ") == "API key/token bos olamaz."


def test_validate_settings_form_collects_errors():
    data = SettingsFormData(
        api_url="bad-url",
        api_key="",
        model="",
        prefer_short_responses=True,
        voice_enabled=True,
        wake_word_enabled=True,
        notifications_enabled=True,
    )
    errors = validate_settings_form(data)
    assert len(errors) == 3


def test_upsert_user_config_server(tmp_path, monkeypatch):
    from hermes.config.settings_store import upsert_user_config_server

    user_cfg = tmp_path / "config" / "default.yaml"
    monkeypatch.setattr("hermes.config.settings_store.user_config_path", lambda: user_cfg)
    monkeypatch.setattr("hermes.config.settings_store.ensure_user_dirs", lambda: user_cfg.parent.mkdir(parents=True, exist_ok=True))

    path = upsert_user_config_server(url="http://127.0.0.1:8642", model=" new-model ")
    assert path == user_cfg
    text = user_cfg.read_text(encoding="utf-8")
    assert "new-model" in text
    assert "127.0.0.1:8642" in text


def test_save_and_load_settings_form(tmp_path, monkeypatch):
    user_cfg = tmp_path / "config" / "default.yaml"
    user_cfg.parent.mkdir(parents=True)
    monkeypatch.setattr("hermes.config.settings_store.user_config_path", lambda: user_cfg)
    monkeypatch.setattr("hermes.config.settings_store.ensure_user_dirs", lambda: user_cfg.parent.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr("hermes.config.settings_store.set_api_key", lambda key: None)
    monkeypatch.setattr("hermes.config.settings_store.resolve_api_key", lambda _: "stored-key")
    monkeypatch.delenv("HERMES_SERVER_URL", raising=False)
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    monkeypatch.setattr("hermes.config.paths.resolve_env_file", lambda: None)

    data = SettingsFormData(
        api_url="http://127.0.0.1:8642",
        api_key="secret-token",
        model="custom-model",
        prefer_short_responses=False,
        voice_enabled=False,
        wake_word_enabled=True,
        notifications_enabled=False,
    )
    saved = save_settings_form(data)
    assert saved == user_cfg
    assert user_cfg.exists()
    text = user_cfg.read_text(encoding="utf-8")
    assert "127.0.0.1:8642" in text
    assert "custom-model" in text
    assert "prefer_short_responses: false" in text
    assert "enabled: false" in text

    loaded = load_settings_form(user_cfg)
    assert loaded.api_url == "http://127.0.0.1:8642"
    assert loaded.api_key == "stored-key"
    assert loaded.model == "custom-model"
    assert loaded.prefer_short_responses is False
    assert loaded.voice_enabled is False
    assert loaded.notifications_enabled is False


def test_save_settings_form_raises_on_invalid():
    data = SettingsFormData(
        api_url="",
        api_key="x",
        model="m",
        prefer_short_responses=True,
        voice_enabled=True,
        wake_word_enabled=True,
        notifications_enabled=True,
    )
    with pytest.raises(ValueError, match="API URL"):
        save_settings_form(data)
