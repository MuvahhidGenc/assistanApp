from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.config.settings import VoiceSettings
from hermes.voice.assistant import VoiceAssistant, build_voice_assistant
from hermes.voice.stt import NullSpeechToText
from hermes.voice.tts import NullTextToSpeech
from hermes.voice.wake_word import detect_wake_word, is_stop_command, normalize_voice_text


class MockSTT:
    def __init__(self, responses: list[str | None]) -> None:
        self._responses = list(responses)
        self.listen_calls = 0

    def is_available(self) -> bool:
        return True

    @property
    def unavailable_reason(self) -> str:
        return "ok"

    async def listen(self, *, timeout: float, phrase_limit: float | None = None) -> str | None:
        self.listen_calls += 1
        if not self._responses:
            return None
        return self._responses.pop(0)


class MockTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.stopped = False

    def is_available(self) -> bool:
        return True

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def voice_settings() -> VoiceSettings:
    return VoiceSettings(wake_word_enabled=True)


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.process_message = AsyncMock(return_value="Merhaba abi.")
    agent.state = MagicMock(current_run_id=None)
    agent._server = MagicMock()
    agent._server.stop_run = AsyncMock()
    return agent


def test_normalize_voice_text():
    assert normalize_voice_text("  Abi  ") == "abi"


def test_detect_wake_word_at_start():
    wake, remainder = detect_wake_word("abi sistem bilgimi goster")
    assert wake == "abi"
    assert remainder == "sistem bilgimi goster"


def test_detect_wake_word_akhi():
    wake, remainder = detect_wake_word("akhi chrome ac")
    assert wake == "akhi"
    assert remainder == "chrome ac"


def test_detect_wake_word_dostum():
    wake, _ = detect_wake_word("dostum merhaba")
    assert wake == "dostum"


def test_no_wake_word_in_unrelated_text():
    wake, _ = detect_wake_word("sistem bilgimi goster")
    assert wake is None


def test_is_stop_command():
    assert is_stop_command("dur")
    assert is_stop_command("iptal et")
    assert is_stop_command("sus")
    assert not is_stop_command("devam et")


def test_voice_settings_wake_word_toggle():
    settings = VoiceSettings(wake_word_enabled=False)
    assert settings.wake_word_enabled is False


@pytest.mark.asyncio
async def test_text_input_always_works(mock_agent, voice_settings):
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings.model_copy(update={"wake_word_enabled": False}),
        agent=mock_agent,
        stt=NullSpeechToText("test"),
        tts=tts,
    )
    responses: list[str] = []

    async def on_response(text: str, source: str) -> None:
        responses.append(text)

    assistant.on_response = on_response
    await assistant.handle_text_input("merhaba")

    mock_agent.process_message.assert_called_once_with("merhaba")
    assert responses == ["Merhaba abi."]
    assert tts.spoken == []


@pytest.mark.asyncio
async def test_no_microphone_does_not_crash(mock_agent, voice_settings):
    assistant = VoiceAssistant(
        settings=voice_settings,
        agent=mock_agent,
        stt=NullSpeechToText("mikrofon yok"),
        tts=NullTextToSpeech(),
    )
    statuses: list[str] = []

    async def on_status(msg: str) -> None:
        statuses.append(msg)

    assistant.on_status = on_status
    await assistant.start()
    assert not assistant.microphone_available
    assert any("Mikrofon" in s for s in statuses)
    await assistant.handle_text_input("test")
    mock_agent.process_message.assert_called()
    await assistant.stop()


@pytest.mark.asyncio
async def test_wake_word_triggers_command_and_tts(mock_agent, voice_settings):
    stt = MockSTT(["abi disk bilgisi", None])
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings,
        agent=mock_agent,
        stt=stt,
        tts=tts,
    )
    statuses: list[str] = []

    async def on_status(msg: str) -> None:
        statuses.append(msg)

    assistant.on_response = AsyncMock()
    assistant.on_status = on_status

    with patch("hermes.voice.assistant.play_wake_beep", return_value=True):
        await assistant.start()
        await asyncio.sleep(0.05)
        await assistant.stop()

    mock_agent.process_message.assert_called_with("disk bilgisi")
    assert any("Dinliyorum" in s for s in statuses)
    assert tts.spoken == ["Merhaba abi."]


@pytest.mark.asyncio
async def test_wake_word_two_step_listen(mock_agent, voice_settings):
    stt = MockSTT(["abi", "chrome ac"])
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings,
        agent=mock_agent,
        stt=stt,
        tts=tts,
    )
    assistant.on_response = AsyncMock()

    with patch("hermes.voice.assistant.play_wake_beep", return_value=True):
        await assistant.start()
        await asyncio.sleep(0.05)
        await assistant.stop()

    mock_agent.process_message.assert_called_with("chrome ac")
    assert stt.listen_calls >= 2


@pytest.mark.asyncio
async def test_stop_command_cancels_active(mock_agent, voice_settings):
    tts = MockTTS()
    blocker = asyncio.Event()

    async def slow_message(_text: str) -> str:
        await blocker.wait()
        return "gec"

    mock_agent.process_message = slow_message
    assistant = VoiceAssistant(
        settings=voice_settings.model_copy(update={"wake_word_enabled": False}),
        agent=mock_agent,
        stt=NullSpeechToText("x"),
        tts=tts,
    )

    task = asyncio.create_task(assistant.handle_text_input("uzun islem"))
    await asyncio.sleep(0.05)
    await assistant.handle_text_input("dur")
    await asyncio.wait([task], timeout=2)
    assert tts.stopped
    assert task.done()


@pytest.mark.asyncio
async def test_non_wake_speech_ignored(mock_agent, voice_settings):
    stt = MockSTT(["sistem bilgimi goster", None])
    assistant = VoiceAssistant(
        settings=voice_settings,
        agent=mock_agent,
        stt=stt,
        tts=NullTextToSpeech(),
    )
    assistant.on_response = AsyncMock()

    await assistant.start()
    await asyncio.sleep(0.05)
    await assistant.stop()

    mock_agent.process_message.assert_not_called()


def test_build_voice_assistant(mock_agent, voice_settings):
    assistant = build_voice_assistant(mock_agent, voice_settings, stt=NullSpeechToText("x"))
    assert assistant.wake_words == ("abi", "akhi", "dostum")


@pytest.mark.asyncio
async def test_configure_voice_toggle(mock_agent, voice_settings):
    stt = MockSTT([None])
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings,
        agent=mock_agent,
        stt=stt,
        tts=tts,
    )
    await assistant.start()
    assert assistant.voice_enabled is True

    await assistant.configure_voice(voice_enabled=False)
    assert assistant.voice_enabled is False
    assert assistant._wake_task is None

    await assistant.configure_voice(voice_enabled=True)
    assert assistant.voice_enabled is True

    await assistant.stop()
