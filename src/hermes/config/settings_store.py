from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from hermes.config.credentials import resolve_api_key, set_api_key
from hermes.config.paths import (
    bundled_config_path,
    ensure_user_dirs,
    portable_config_path,
    user_config_path,
)
from hermes.config.settings import AppSettings, normalize_model


def normalize_env(value: str | None) -> str:
    if value:
        return value.strip()
    return ""


_URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)


@dataclass
class SettingsFormData:
    api_url: str
    api_key: str
    model: str
    prefer_short_responses: bool
    voice_enabled: bool
    wake_word_enabled: bool
    notifications_enabled: bool


def validate_api_url(url: str) -> str | None:
    text = url.strip()
    if not text:
        return "API URL bos olamaz."
    if not _URL_PATTERN.match(text):
        return "API URL http:// veya https:// ile baslamalidir."
    parsed = urlparse(text)
    if not parsed.netloc:
        return "Gecersiz API URL."
    return None


def validate_api_key(api_key: str) -> str | None:
    if not api_key.strip():
        return "API key/token bos olamaz."
    return None


def validate_model(model: str) -> str | None:
    if not model.strip():
        return "Model adi bos olamaz."
    return None


def validate_settings_form(data: SettingsFormData) -> list[str]:
    errors: list[str] = []
    url_error = validate_api_url(data.api_url)
    if url_error:
        errors.append(url_error)
    key_error = validate_api_key(data.api_key)
    if key_error:
        errors.append(key_error)
    model_error = validate_model(data.model)
    if model_error:
        errors.append(model_error)
    return errors


def _load_base_yaml() -> dict:
    for candidate in (
        user_config_path(),
        portable_config_path(),
        bundled_config_path(),
        Path("config/default.yaml"),
    ):
        if candidate is None:
            continue
        if not candidate.exists() or not candidate.is_file():
            continue
        with candidate.open(encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    return {}


def load_settings_form(config_path: Path | None = None) -> SettingsFormData:
    path = config_path or user_config_path()
    settings = AppSettings.load(path if path.exists() else None)
    api_key = resolve_api_key(settings.api_key.get_secret_value())
    voice_enabled = settings.voice.enabled
    return SettingsFormData(
        api_url=settings.server.url,
        api_key=api_key,
        model=settings.server.model,
        prefer_short_responses=settings.client.prefer_short_responses,
        voice_enabled=voice_enabled,
        wake_word_enabled=settings.voice.wake_word_enabled,
        notifications_enabled=settings.ui.notifications_enabled,
    )


def save_settings_form(data: SettingsFormData) -> Path:
    errors = validate_settings_form(data)
    if errors:
        raise ValueError("; ".join(errors))
    ensure_user_dirs()
    path = user_config_path()
    payload = _load_base_yaml()
    payload.setdefault("server", {})
    payload["server"]["url"] = data.api_url.strip()
    payload["server"]["model"] = normalize_model(data.model)
    payload.setdefault("client", {})
    payload["client"]["prefer_short_responses"] = data.prefer_short_responses
    payload.setdefault("voice", {})
    payload["voice"]["enabled"] = data.voice_enabled
    payload["voice"]["wake_word_enabled"] = data.wake_word_enabled
    payload.setdefault("ui", {})
    payload["ui"]["notifications_enabled"] = data.notifications_enabled
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
    set_api_key(data.api_key.strip())
    return path


def bootstrap_user_config() -> Path | None:
    """Create user default.yaml from bundled/portable template if missing."""
    from hermes.config_client import ensure_client_config

    path = user_config_path()
    existed = path.exists()
    ensure_client_config()
    return None if existed else path


def upsert_user_config_server(url: str, model: str) -> Path:
    """Persist server URL + model for tray/exe (not only .env)."""
    ensure_user_dirs()
    path = user_config_path()
    payload = _load_base_yaml()
    payload.setdefault("server", {})
    payload["server"]["url"] = url.strip()
    payload["server"]["model"] = normalize_model(model)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
    return path
