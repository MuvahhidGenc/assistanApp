from __future__ import annotations

import json
import os
from pathlib import Path

import keyring

from hermes.config.paths import app_data_dir

SERVICE_NAME = "hermes-client"
_CREDENTIALS_FILE = "credentials.json"


def _credentials_path() -> Path:
    return app_data_dir() / _CREDENTIALS_FILE


def load_credentials() -> dict[str, str]:
    path = _credentials_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_credentials(data: dict[str, str]) -> None:
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_api_key() -> str | None:
    return keyring.get_password(SERVICE_NAME, "api_key")


def set_api_key(api_key: str) -> None:
    _write_keyring(api_key)


def delete_api_key() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, "api_key")
    except keyring.errors.PasswordDeleteError:
        pass


def _read_keyring() -> str:
    return get_api_key() or ""


def _write_keyring(api_key: str) -> bool:
    keyring.set_password(SERVICE_NAME, "api_key", api_key)
    return True


def resolve_api_key(env_key: str) -> str:
    """Resolve API key: environment first, then stored credentials/keyring."""
    env_value = os.environ.get("HERMES_API_KEY", "").strip()
    if env_value:
        return env_value
    if env_key:
        return env_key
    stored = load_credentials().get("api_key") or load_credentials().get("elevenlabs_api_key")
    if stored:
        return stored
    ring = _read_keyring()
    if ring:
        return ring
    return ""
