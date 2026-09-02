from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from hermes.config.paths import app_data_dir, ensure_user_dirs

_KEYRING_SERVICE = "hermes-client"
_KEYRING_USERNAME = "api_key"

_CREDENTIAL_KEYS = (
    "elevenlabs_api_key",
    "hermes_api_key",
)

_ENV_ALIASES = {
    "elevenlabs_api_key": "ELEVENLABS_API_KEY",
    "hermes_api_key": "HERMES_API_KEY",
}


def credentials_path() -> Path:
    return app_data_dir() / "credentials.yaml"


def load_credentials() -> dict[str, str]:
    """Load secrets from user data dir (never from repo)."""
    ensure_user_dirs()
    path = credentials_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                data = raw
        except (OSError, yaml.YAMLError):
            pass

    out: dict[str, str] = {}
    for key in _CREDENTIAL_KEYS:
        env_name = _ENV_ALIASES.get(key, key.upper())
        value = str(data.get(key) or os.environ.get(env_name) or "").strip()
        if value:
            out[key] = value
    return out


def save_credentials(updates: dict[str, str]) -> None:
    ensure_user_dirs()
    path = credentials_path()
    current: dict[str, Any] = {}
    if path.exists():
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                current = raw
        except (OSError, yaml.YAMLError):
            current = {}
    for key, value in updates.items():
        if key in _CREDENTIAL_KEYS and str(value).strip():
            current[key] = str(value).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(current, handle, default_flow_style=False, allow_unicode=True)


def _read_keyring() -> str:
    try:
        import keyring

        stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        return str(stored or "").strip()
    except Exception:
        return ""


def _write_keyring(api_key: str) -> bool:
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, api_key)
        return True
    except Exception:
        return False


def resolve_api_key(config_value: str = "") -> str:
    """Resolve Hermes server API key from env, keyring, credentials file, or config."""
    env_key = os.environ.get("HERMES_API_KEY", "").strip()
    if env_key:
        return env_key

    file_key = load_credentials().get("hermes_api_key", "").strip()
    if file_key:
        return file_key

    inline = str(config_value or "").strip()
    if inline:
        return inline

    return _read_keyring()


def set_api_key(api_key: str) -> None:
    """Persist Hermes server API key to Windows Credential Manager or credentials file."""
    cleaned = str(api_key or "").strip()
    if not cleaned:
        return
    if _write_keyring(cleaned):
        return
    save_credentials({"hermes_api_key": cleaned})


def get_elevenlabs_api_key() -> str:
    key, _ = resolve_elevenlabs_api_key()
    return key


def resolve_elevenlabs_api_key(*, config_value: str = "") -> tuple[str, str]:
    """
    Resolve ElevenLabs API key from env, credentials file, or inline settings.

    Returns (key, source) where source is one of:
    environment | credentials_file | settings | missing
    """
    env_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if env_key:
        return env_key, "environment"
    file_key = load_credentials().get("elevenlabs_api_key", "").strip()
    if file_key:
        return file_key, "credentials_file"
    inline = str(config_value or "").strip()
    if inline:
        return inline, "settings"
    return "", "missing"


def elevenlabs_key_hint() -> str:
    path = credentials_path()
    return (
        "ElevenLabs API anahtari bulunamadi. "
        f"ELEVENLABS_API_KEY ortam degiskenini ayarlayin veya {path} dosyasina "
        "elevenlabs_api_key ekleyin."
    )
