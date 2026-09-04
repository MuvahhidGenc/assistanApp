from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    LOW_RISK = "low_risk"
    NORMAL_MODIFICATION = "normal_modification"
    HIGH_RISK = "high_risk"

    @property
    def severity(self) -> int:
        return _RISK_SEVERITY[self]


_RISK_SEVERITY: dict[RiskLevel, int] = {
    RiskLevel.READ_ONLY: 0,
    RiskLevel.LOW_RISK: 1,
    RiskLevel.NORMAL_MODIFICATION: 2,
    RiskLevel.HIGH_RISK: 3,
}


class ServerSettings(BaseModel):
    url: str = ""
    model: str = "hermes-agent"
    timeout_seconds: int = 120
    verify_ssl: bool = True


DEFAULT_MODEL = "hermes-agent"


def normalize_model(model: str) -> str:
    text = model.strip()
    return text or DEFAULT_MODEL


class ClientSettings(BaseModel):
    name: str = "HERMES Windows Client"
    max_agent_steps: int = 50
    agent_step_timeout_seconds: int = 300
    debug: bool = False
    prefer_short_responses: bool = True


class UISettings(BaseModel):
    window_width: int = 1360
    window_height: int = 820
    connection_check_interval_seconds: int = 60
    notifications_enabled: bool = True
    auto_approve_local_tools: bool = True


class VoiceSettings(BaseModel):
    enabled: bool = True
    wake_word_enabled: bool = True
    wake_words: list[str] = Field(default_factory=lambda: ["abi", "akhi", "dostum"])
    wake_listen_timeout_seconds: float = 6.0
    wake_phrase_limit_seconds: float = 8.0
    command_listen_timeout_seconds: float = 12.0
    command_phrase_limit_seconds: float = 18.0
    command_pause_seconds: float = 2.0
    continuous_listen: bool = True
    show_listening_prompt: bool = True
    stt_language: str = "tr-TR"
    tts_backend: str = "elevenlabs"
    tts_language: str = "tr-TR"
    tts_gender: str = "male"
    tts_voice: str = "tr-TR-AhmetNeural"
    elevenlabs_voice_id: str = "pNInz6obpgDQGcFmaJgB"
    elevenlabs_model: str = "eleven_multilingual_v2"


class SecuritySettings(BaseModel):
    require_approval_for: list[RiskLevel] = Field(
        default_factory=lambda: [RiskLevel.NORMAL_MODIFICATION, RiskLevel.HIGH_RISK]
    )
    audit_log_path: str = Field(
        default_factory=lambda: os.path.join(
            os.environ.get("LOCALAPPDATA", "."), "HERMES", "audit.log"
        )
    )
    redact_patterns: list[str] = Field(
        default_factory=lambda: ["password", "token", "api_key", "secret"]
    )


class LoggingSettings(BaseModel):
    level: str = "INFO"
    format: str = "json"


class SessionSettings(BaseModel):
    record_sessions: bool = True
    session_ttl_seconds: int = 3600
    session_key: str = "hermes-pc-session"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    server: ServerSettings = Field(default_factory=ServerSettings)
    client: ClientSettings = Field(default_factory=ClientSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    ui: UISettings = Field(default_factory=UISettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    sessions: SessionSettings = Field(default_factory=SessionSettings)
    api_key: SecretStr = Field(default="", validation_alias="HERMES_API_KEY")
    elevenlabs_api_key: SecretStr = Field(default="", validation_alias="ELEVENLABS_API_KEY")

    @classmethod
    def load(cls, config_path: Path | None = None) -> AppSettings:
        from hermes.config.paths import resolve_config_path, resolve_env_file

        path = resolve_config_path(config_path)
        data: dict[str, Any] = {}
        if path.exists():
            with path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        env_file = resolve_env_file()
        if env_file:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)

        settings = cls()
        if data.get("server"):
            srv = data["server"]
            settings.server = ServerSettings(**srv)
            if "model" in srv:
                settings.server.model = srv["model"]
        if data.get("client"):
            settings.client = ClientSettings(**data["client"])
        if data.get("voice"):
            settings.voice = VoiceSettings(**data["voice"])
        if data.get("ui"):
            settings.ui = UISettings(**data["ui"])
        if data.get("security"):
            sec = data["security"]
            if "require_approval_for" in sec:
                sec["require_approval_for"] = [RiskLevel(v) for v in sec["require_approval_for"]]
            settings.security = SecuritySettings(**sec)
        if data.get("logging"):
            settings.logging = LoggingSettings(**data["logging"])
        if data.get("sessions"):
            settings.sessions = SessionSettings(**data["sessions"])

        env_url = os.environ.get("HERMES_SERVER_URL")
        if env_url:
            settings.server.url = env_url.strip()
        env_key = os.environ.get("HERMES_API_KEY")
        if env_key:
            settings.api_key = SecretStr(env_key.strip())
        from hermes.config.credentials import load_credentials

        creds = load_credentials()
        env_eleven = os.environ.get("ELEVENLABS_API_KEY") or creds.get("elevenlabs_api_key", "")
        if env_eleven:
            settings.elevenlabs_api_key = SecretStr(env_eleven.strip())
        env_model = os.environ.get("HERMES_MODEL")
        if env_model:
            settings.server.model = env_model.strip()
        env_timeout = os.environ.get("HERMES_TIMEOUT_SECONDS")
        if env_timeout:
            settings.server.timeout_seconds = int(env_timeout)
        env_log_level = os.environ.get("HERMES_LOG_LEVEL")
        if env_log_level:
            settings.logging.level = env_log_level
        env_debug = os.environ.get("HERMES_DEBUG", "").lower()
        if env_debug in ("1", "true", "yes", "on"):
            settings.client.debug = True
        env_wake = os.environ.get("HERMES_VOICE_WAKE_WORD_ENABLED", "").lower()
        if env_wake in ("0", "false", "no", "off"):
            settings.voice.wake_word_enabled = False
        elif env_wake in ("1", "true", "yes", "on"):
            settings.voice.wake_word_enabled = True

        settings.server.url = settings.server.url.strip()
        settings.server.model = normalize_model(settings.server.model)
        if settings.api_key.get_secret_value():
            settings.api_key = SecretStr(settings.api_key.get_secret_value().strip())
        return settings

    def validate_runtime(self) -> None:
        if not self.server.url:
            raise ValueError(
                "HERMES_SERVER_URL is not configured. Set it in .env, environment, or config/default.yaml."
            )
        if not self.api_key.get_secret_value():
            raise ValueError(
                "HERMES_API_KEY is not configured. Set it in .env, environment, or Windows Credential Manager."
            )
