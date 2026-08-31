from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hermes.config.paths import (
    bundled_config_path,
    ensure_user_dirs,
    portable_config_path,
    user_config_path,
)
from hermes.config.settings import normalize_model

DEFAULT_SERVER_URL = "http://50.6.226.228:8642"
DEFAULT_MODEL = "hermes-agent"


def default_server_url() -> str:
    return DEFAULT_SERVER_URL


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def load_client_yaml() -> dict[str, Any]:
    for candidate in (
        user_config_path(),
        portable_config_path(),
        bundled_config_path(),
        Path("config/default.yaml"),
    ):
        if candidate is None:
            continue
        data = _read_yaml(candidate)
        if data:
            return data
    return {}


def apply_server_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    server = payload.setdefault("server", {})
    if not str(server.get("url") or "").strip():
        server["url"] = DEFAULT_SERVER_URL
    if not str(server.get("model") or "").strip():
        server["model"] = normalize_model(DEFAULT_MODEL)
    server["url"] = str(server["url"]).strip()
    return payload


def write_yaml(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
    return path


def ensure_client_config() -> Path:
    """Create or refresh user config so Hermes Server URL is always set."""
    ensure_user_dirs()
    path = user_config_path()
    payload = _read_yaml(path) if path.exists() else load_client_yaml()
    apply_server_defaults(payload)
    return write_yaml(path, payload)
