from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from hermes.config.credentials import resolve_api_key
from hermes.config.settings import AppSettings
from hermes.platform.startup import StartupStatus, get_startup_status
from hermes.server.client import HermesServerClient
from hermes.voice.audio import check_microphone_available
from hermes.voice.wake_word import DEFAULT_WAKE_WORDS


@dataclass
class VoiceStatusReport:
    microphone_available: bool
    reason: str
    wake_word_enabled: bool
    wake_words: list[str] = field(default_factory=list)


@dataclass
class StatusReport:
    config_path: str
    api_url: str
    model: str
    api_key_configured: bool
    health_status: str
    connection_ok: bool
    error: str | None
    voice: VoiceStatusReport
    startup: StartupStatus


def inspect_voice(settings: AppSettings) -> VoiceStatusReport:
    available, reason = check_microphone_available()
    return VoiceStatusReport(
        microphone_available=available,
        reason=reason,
        wake_word_enabled=settings.voice.wake_word_enabled,
        wake_words=list(settings.voice.wake_words or DEFAULT_WAKE_WORDS),
    )


async def build_status_report(
    settings: AppSettings,
    config_path: Path | None = None,
) -> StatusReport:
    api_key = resolve_api_key(settings.api_key.get_secret_value())
    voice = inspect_voice(settings)
    startup = get_startup_status()
    path = str(config_path) if config_path else ""
    report = StatusReport(
        config_path=path,
        api_url=settings.server.url,
        model=settings.server.model,
        api_key_configured=bool(api_key),
        health_status="",
        connection_ok=False,
        error=None,
        voice=voice,
        startup=startup,
    )
    if not api_key:
        report.error = "API key not configured"
        return report

    try:
        async with HermesServerClient(
            settings.server.url,
            api_key,
            model=settings.server.model,
            timeout=float(settings.server.timeout_seconds),
            verify_ssl=settings.server.verify_ssl,
        ) as client:
            health = await client.health()
        report.health_status = health.status
        report.connection_ok = health.status in ("ok", "healthy")
        if not report.connection_ok:
            report.error = f"Health: {health.status}"
    except Exception as exc:
        report.error = str(exc)
        report.connection_ok = False
    return report


def format_status_lines(report: StatusReport) -> list[str]:
    voice_state = "available" if report.voice.microphone_available else "unavailable"
    startup_state = "installed" if report.startup.installed else "not installed"
    return [
        f"Config: {report.config_path or '-'}",
        f"API URL: {report.api_url}",
        f"Model: {report.model}",
        f"API key: {'configured' if report.api_key_configured else 'missing'}",
        f"Health: {report.health_status or '-'}",
        f"Connection: {'ok' if report.connection_ok else 'failed'}",
        f"Microphone: {voice_state} ({report.voice.reason})",
        (
            f"Wake word: {'on' if report.voice.wake_word_enabled else 'off'} "
            f"({', '.join(report.voice.wake_words)})"
        ),
        f"Startup: {startup_state} ({report.startup.method})",
        *([f"Error: {report.error}"] if report.error else []),
    ]
