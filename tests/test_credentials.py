from unittest.mock import patch

from hermes.config.credentials import resolve_api_key, set_api_key


def test_resolve_api_key_prefers_env(monkeypatch):
    monkeypatch.setenv("HERMES_API_KEY", "env-key")
    assert resolve_api_key("inline-key") == "env-key"


def test_resolve_api_key_uses_inline_when_no_store(monkeypatch):
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    with patch("hermes.config.credentials.load_credentials", return_value={}):
        with patch("hermes.config.credentials._read_keyring", return_value=""):
            assert resolve_api_key("inline-key") == "inline-key"


def test_resolve_api_key_uses_keyring(monkeypatch):
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    with patch("hermes.config.credentials.load_credentials", return_value={}):
        with patch("hermes.config.credentials._read_keyring", return_value="ring-key"):
            assert resolve_api_key("") == "ring-key"


def test_set_api_key_writes_keyring(monkeypatch):
    with patch("hermes.config.credentials._write_keyring", return_value=True) as writer:
        set_api_key("secret")
    writer.assert_called_once_with("secret")
