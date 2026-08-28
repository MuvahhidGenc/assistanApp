from __future__ import annotations

from pathlib import Path

from hermes.config.paths import (
    app_data_dir,
    app_log_path,
    bundled_config_path,
    is_frozen,
    portable_config_path,
    resolve_config_path,
    user_config_path,
)


def test_app_data_paths():
    assert app_data_dir().name == "HermesClient"
    assert app_log_path().name == "app.log"
    assert "logs" in str(app_log_path())


def test_resolve_config_path_prefers_user(tmp_path, monkeypatch):
    user_cfg = tmp_path / "user" / "default.yaml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text("server:\n  url: http://example.test\n", encoding="utf-8")
    monkeypatch.setattr("hermes.config.paths.user_config_path", lambda: user_cfg)

    resolved = resolve_config_path()
    assert resolved == user_cfg


def test_resolve_config_path_explicit(tmp_path):
    cfg = tmp_path / "custom.yaml"
    cfg.write_text("server:\n  url: http://custom.test\n", encoding="utf-8")
    assert resolve_config_path(cfg) == cfg


def test_bundled_config_path_exists_in_repo():
    if not is_frozen():
        assert bundled_config_path().exists()


def test_portable_config_path_empty_when_not_frozen():
    assert portable_config_path() is None
