from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hermes.config.credentials import resolve_api_key
from hermes.config.paths import resolve_config_path
from hermes.config.settings import AppSettings
from hermes.platform.startup import StartupStatus, get_startup_status
from hermes.server.client import HermesServerClient, HermesServerError, normalize_server_urls
from hermes.voice.audio import check_microphone_available


@dataclass
class VoiceStatusReport:
    microphone_available: bool
    reason: str
    wake_word_enabled: bool
    wake_words: list[str]


@dataclass
class StatusReport:
    config_path: str
    api_url: str
    model: str
    api_key_configured: bool
    health_status: str | None
    connection_ok: bool
    error: str | None
    voice: VoiceStatusReport
    startup: StartupStatus


def load_status_settings(config_path: Path | None = None) -> AppSettings:
    return AppSettings.load(config_path)


def inspect_voice(settings: AppSettings) -> VoiceStatusReport:
    available, reason = check_microphone_available()
    return VoiceStatusReport(
        microphone_available=available,
        reason=reason,
        wake_word_enabled=settings.voice.wake_word_enabled,
        wake_words=list(settings.voice.wake_words),
    )


async def build_status_report(
    settings: AppSettings,
    *,
    config_path: Path | None = None,
) -> StatusReport:
    resolved = resolve_config_path(config_path)
    api_key = resolve_api_key(settings.api_key.get_secret_value())
    voice = inspect_voice(settings)
    startup = get_startup_status()

    if not settings.server.url:
        return StatusReport(
            config_path=str(resolved),
            api_url="",
            model=settings.server.model,
            api_key_configured=bool(api_key),
            health_status=None,
            connection_ok=False,
            error="HERMES_SERVER_URL not configured",
            voice=voice,
            startup=startup,
        )

    if not api_key:
        return StatusReport(
            config_path=str(resolved),
            api_url=settings.server.url,
            model=settings.server.model,
            api_key_configured=False,
            health_status=None,
            connection_ok=False,
            error="API key not configured",
            voice=voice,
            startup=startup,
        )

    try:
        async with HermesServerClient(
            settings.server.url,
            api_key,
            model=settings.server.model,
            timeout=10,
            verify_ssl=settings.server.verify_ssl,
        ) as client:
            health = await client.health()
            ok = health.status in ("ok", "healthy")
            return StatusReport(
                config_path=str(resolved),
                api_url=settings.server.url,
                model=settings.server.model,
                api_key_configured=True,
                health_status=health.status,
                connection_ok=ok,
                error=None if ok else f"Unexpected health status: {health.status}",
                voice=voice,
                startup=startup,
            )
    except HermesServerError as exc:
        return StatusReport(
            config_path=str(resolved),
            api_url=settings.server.url,
            model=settings.server.model,
            api_key_configured=True,
            health_status=None,
            connection_ok=False,
            error=str(exc),
            voice=voice,
            startup=startup,
        )
    except Exception as exc:
        return StatusReport(
            config_path=str(resolved),
            api_url=settings.server.url,
            model=settings.server.model,
            api_key_configured=True,
            health_status=None,
            connection_ok=False,
            error=f"{type(exc).__name__}: {exc}",
            voice=voice,
            startup=startup,
        )


def format_status_lines(report: StatusReport) -> list[str]:
    root, v1 = normalize_server_urls(report.api_url) if report.api_url else ("", "")
    lines = [
        f"Config:       {report.config_path}",
        f"API URL:      {report.api_url or '(not set)'}",
    ]
    if report.api_url:
        lines.extend([f"Server root:  {root}", f"API base:     {v1}"])
    lines.extend(
        [
            f"Model:        {report.model}",
            f"API key:      {'configured' if report.api_key_configured else 'missing'}",
            f"Connection:   {'OK' if report.connection_ok else 'FAILED'}",
        ]
    )
    if report.health_status:
        lines.append(f"Health:       {report.health_status}")
    if report.error:
        lines.append(f"Error:        {report.error}")

    mic = "available" if report.voice.microphone_available else "unavailable"
    lines.extend(
        [
            f"Microphone:   {mic} ({report.voice.reason})",
            f"Wake word:    {'enabled' if report.voice.wake_word_enabled else 'disabled'}",
            f"Wake words:   {', '.join(report.voice.wake_words)}",
        ]
    )
    if report.startup.installed:
        lines.append(f"Startup:      {report.startup.method.value} ({report.startup.target})")
    else:
        lines.append("Startup:      not installed")
    return lines
