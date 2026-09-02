from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.config.settings import VoiceSettings
from hermes.voice.assistant import VoiceAssistant
from hermes.voice.tts import create_tts
from hermes.voice.tts_trace import get_correlation_id


@pytest.mark.asyncio
async def test_client_tts_flow_events_with_mocked_playback():
    captured: list[tuple[str, dict]] = []

    def fake_audit(event: str, **fields):
        captured.append((event, fields))

    agent = MagicMock()
    agent.process_message = AsyncMock(
        return_value="Test123 klasörünü masaüstünde oluşturdum."
    )
    agent.state = MagicMock(
        last_tool_results=[{"name": "create_folder", "success": True}]
    )
    agent._server = MagicMock()
    agent._server.stop_run = AsyncMock()

    settings = VoiceSettings(enabled=True, tts_backend="elevenlabs")
    tts = create_tts(backend="elevenlabs", elevenlabs_api_key="")

    with patch("hermes.voice.tts._audit", side_effect=fake_audit):
        with patch("hermes.voice.tts._play_mp3_powershell"):
            with patch.object(
                __import__("hermes.voice.tts", fromlist=["EdgeTurkishTTS"]).EdgeTurkishTTS,
                "_synthesize_mp3",
                new_callable=AsyncMock,
            ) as synth:
                from pathlib import Path

                synth.return_value = Path("fake.mp3")
                assistant = VoiceAssistant(settings=settings, agent=agent, tts=tts)
                assistant._voice_enabled = True
                await assistant.handle_text_input(
                    "Masaüstüne test123 adlı bir klasör oluştur."
                )

    events = {name for name, _ in captured}
    assert "VOICE_ASSISTANT_ACTIVE" in events
    assert "SYNTHESIZER_CALLED" in events
    assert get_correlation_id() is None


@pytest.mark.asyncio
async def test_ssl_error_triggers_insecure_retry(monkeypatch, tmp_path):
    from hermes.voice.tts import EdgeTurkishTTS

    tts = EdgeTurkishTTS()
    monkeypatch.setattr(tts, "_available", True)
    calls: list[bool] = []

    async def fake_synth(text: str, *, insecure_ssl: bool = False):
        calls.append(insecure_ssl)
        if not insecure_ssl:
            raise RuntimeError(
                "edge TTS synthesis failed: SSLCertVerificationError certificate verify failed"
            )
        out = tmp_path / "ok.mp3"
        out.write_bytes(b"\xff\xfb" + b"\x00" * 128)
        return out

    monkeypatch.setattr(tts, "_synthesize_mp3", fake_synth)
    with patch("hermes.voice.tts._play_mp3_powershell"):
        await tts.speak("Merhaba")
    assert calls == [False, True]
