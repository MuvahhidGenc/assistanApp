from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hermes.config.settings import AppSettings
from hermes.platform.startup import StartupMethod, StartupStatus
from hermes.server.models import HealthResponse
from hermes.status_report import (
    StatusReport,
    VoiceStatusReport,
    build_status_report,
    format_status_lines,
    inspect_voice,
)


@pytest.mark.asyncio
async def test_build_status_report_success():
    settings = AppSettings()
    settings.server.url = "http://example.test:8642"
    settings.server.model = "hermes-agent"

    with (
        patch("hermes.status_report.resolve_api_key", return_value="secret"),
        patch("hermes.status_report.HermesServerClient") as client_cls,
        patch("hermes.status_report.get_startup_status") as startup_status,
        patch("hermes.status_report.check_microphone_available", return_value=(False, "test")),
    ):
        startup_status.return_value = StartupStatus(installed=False, method=StartupMethod.NONE)
        instance = client_cls.return_value
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        instance.health = AsyncMock(return_value=HealthResponse(status="ok"))

        report = await build_status_report(settings, config_path=Path("config/default.yaml"))

    assert report.connection_ok is True
    assert report.api_url == "http://example.test:8642"
    assert report.api_key_configured is True
    assert report.voice.microphone_available is False


@pytest.mark.asyncio
async def test_build_status_report_missing_api_key():
    settings = AppSettings()
    settings.server.url = "http://example.test"

    with patch("hermes.status_report.resolve_api_key", return_value=""):
        report = await build_status_report(settings)

    assert not report.connection_ok
    assert report.error == "API key not configured"


def test_format_status_lines():
    report = StatusReport(
        config_path="config/default.yaml",
        api_url="http://example.test",
        model="hermes-agent",
        api_key_configured=True,
        health_status="ok",
        connection_ok=True,
        error=None,
        voice=VoiceStatusReport(
            microphone_available=False,
            reason="test",
            wake_word_enabled=True,
            wake_words=["abi"],
        ),
        startup=StartupStatus(installed=False, method=StartupMethod.NONE),
    )
    lines = format_status_lines(report)
    assert any("API URL" in line for line in lines)
    assert any("Microphone" in line for line in lines)
