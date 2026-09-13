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


def test_write_yaml_skips_identical_content(tmp_path):
    """Regression: config_client.write_yaml MUST NOT rewrite disk when the
    existing byte content is identical. This avoids Errno 13 PermissionError
    on Windows when Explorer / AV / a lingering reader holds a transient
    share-lock on ``default.yaml`` during production EXE bootstrap."""
    from hermes.config_client import write_yaml

    target = tmp_path / "cfg" / "default.yaml"
    payload = {"server": {"url": "http://x", "model": "y"}}

    p1 = write_yaml(target, payload)
    assert p1 == target
    first_bytes = target.read_bytes()
    first_mtime_ns = target.stat().st_mtime_ns

    import time
    time.sleep(0.02)

    p2 = write_yaml(target, dict(payload))
    assert p2 == target
    assert target.read_bytes() == first_bytes
    # Idempotency invariant: no write took place, so mtime MUST be unchanged.
    assert target.stat().st_mtime_ns == first_mtime_ns
    # No stray temp files left next to the target.
    leftovers = [p.name for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == []


def test_write_yaml_actually_updates_when_payload_changes(tmp_path):
    """Ensure idempotency skip does not mask legitimate updates."""
    from hermes.config_client import write_yaml

    target = tmp_path / "cfg" / "default.yaml"
    write_yaml(target, {"server": {"url": "http://a", "model": "m"}})
    text_a = target.read_text(encoding="utf-8")

    import time
    time.sleep(0.02)

    write_yaml(target, {"server": {"url": "http://b", "model": "m"}})
    text_b = target.read_text(encoding="utf-8")
    assert "http://a" not in text_b
    assert "http://b" in text_b
    assert text_a != text_b
    leftovers = [p.name for p in target.parent.iterdir() if p.name != target.name]
    assert leftovers == []


def test_ensure_client_config_idempotent_no_write_when_defaults_present(tmp_path, monkeypatch):
    """ensure_client_config must not touch %LOCALAPPDATA%/HermesClient/config
    when the file already contains the correct server defaults. Otherwise
    every tray EXE start rewrites the file and collides with transient
    Windows share-locks -> PermissionError(13)."""
    from hermes.config_client import ensure_client_config, DEFAULT_SERVER_URL, DEFAULT_MODEL

    user_cfg = tmp_path / "HermesClient" / "config" / "default.yaml"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    # Force paths module to re-derive app_data_dir from our patched env.
    import hermes.config.paths as paths_mod

    orig_app_data = paths_mod.app_data_dir
    try:
        paths_mod.app_data_dir = lambda: tmp_path / "HermesClient"
        assert user_cfg.parent == paths_mod.user_config_dir()

        user_cfg.parent.mkdir(parents=True, exist_ok=True)
        user_cfg.write_text(
            f"server:\n  url: {DEFAULT_SERVER_URL}\n  model: {DEFAULT_MODEL}\n",
            encoding="utf-8",
        )
        first_bytes = user_cfg.read_bytes()
        first_mtime_ns = user_cfg.stat().st_mtime_ns

        import time
        time.sleep(0.02)

        result = ensure_client_config()
        assert result == user_cfg
        assert user_cfg.read_bytes() == first_bytes
        assert user_cfg.stat().st_mtime_ns == first_mtime_ns
    finally:
        paths_mod.app_data_dir = orig_app_data

