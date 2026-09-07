from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.config.settings import VoiceSettings
from hermes.voice.assistant import VoiceAssistant, build_voice_assistant
from hermes.voice.stt import NullSpeechToText
from hermes.voice.tts import NullTextToSpeech, pick_edge_voice
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

    async def listen(
        self,
        timeout: float,
        *,
        phrase_limit: float | None = None,
        pause_seconds: float | None = None,
    ) -> str | None:
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
    return VoiceSettings(wake_word_enabled=True, continuous_listen=False)


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.process_message = AsyncMock(return_value="Merhaba abi.")
    agent.state = MagicMock(current_run_id=None)
    agent._server = MagicMock()
    agent._server.stop_run = AsyncMock()
    return agent


def test_brief_spoken_reply_always_short_for_long_text():
    from hermes.voice.spoken import brief_spoken_reply, looks_like_missing_tools

    long_ok = "DNS guncellendi: 8.8.8.8. Adapter Wi-Fi. Daha fazla detay sohbette duruyor."
    assert brief_spoken_reply(long_ok).startswith("Tamam, DNS ayarland")
    assert brief_spoken_reply("Anladım. Detaylar aşağıda.") == "Anladım"
    assert brief_spoken_reply("Merhaba abi.") == "Merhaba abi"
    assert len(brief_spoken_reply("x" * 200)) < 80
    assert looks_like_missing_tools("yerel arac yok")


def test_pick_edge_voice_uses_ahmet():
    assert pick_edge_voice("", "male") == "tr-TR-AhmetNeural"
    assert pick_edge_voice("tr-TR-EmelNeural", "male") == "tr-TR-AhmetNeural"


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
async def test_text_input_speaks_brief_reply(mock_agent, voice_settings):
    mock_agent.process_message = AsyncMock(
        return_value="Ekranda gorunen yazi:\n" + ("satir " * 40)
    )
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings.model_copy(update={"wake_word_enabled": False}),
        agent=mock_agent,
        stt=NullSpeechToText("test"),
        tts=tts,
    )
    await assistant.handle_text_input("ekrani oku")
    assert tts.spoken[-1] == "Ekrana baktım, detaylar sohbette."


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
    assert tts.spoken[-1] == "Merhaba abi"


@pytest.mark.asyncio
async def test_text_input_speaks_when_voice_flag_off(mock_agent, voice_settings):
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings.model_copy(update={"wake_word_enabled": False}),
        agent=mock_agent,
        stt=NullSpeechToText("test"),
        tts=tts,
    )
    await assistant.configure_voice(voice_enabled=False)
    await assistant.handle_text_input("merhaba")
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
async def test_voice_input_shown_via_callback(mock_agent, voice_settings):
    stt = MockSTT(["abi chrome ac", None])
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings,
        agent=mock_agent,
        stt=stt,
        tts=tts,
    )
    user_inputs: list[tuple[str, str]] = []
    assistant.on_user_input = AsyncMock(
        side_effect=lambda text, source: user_inputs.append((text, source))
    )
    assistant.on_response = AsyncMock()

    with patch.object(assistant, "_speak_prompt", new=AsyncMock()):
        await assistant.start()
        await asyncio.sleep(0.35)
        await assistant.stop()

    assert ("chrome ac", "voice") in user_inputs


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

    with patch.object(assistant, "_speak_prompt", new=AsyncMock()) as speak_mock:
        await assistant.start()
        await asyncio.sleep(0.35)
        await assistant.stop()

    mock_agent.process_message.assert_called_with("disk bilgisi")
    assert any("Sesli mod" in s or "Dinliyorum" in s for s in statuses)
    assert speak_mock.await_count >= 1


@pytest.mark.asyncio
async def test_wake_word_two_step_listen(mock_agent, voice_settings):
    stt = MockSTT(["abi", "chrome ac", None])
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=voice_settings.model_copy(
            update={
                "continuous_listen": True,
                "command_listen_timeout_seconds": 0.1,
            }
        ),
        agent=mock_agent,
        stt=stt,
        tts=tts,
    )
    assistant.on_response = AsyncMock()

    with patch.object(assistant, "_speak_prompt", new=AsyncMock()):
        await assistant.start()
        await asyncio.sleep(0.4)
        assistant._voice_session_active = False
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


# ---------------------------------------------------------------------------
# V3 contract: VoiceAssistant must not access V2-only agent state or
# server transport. The V3 runtime does not expose ``current_run_id``
# or ``agent._server``; voice cancellation goes through the local
# ``_active_request.cancel()`` path.
# ---------------------------------------------------------------------------


def test_voice_stop_active_does_not_touch_v2_agent_state():
    """V2 ``agent.state.current_run_id`` does not exist in V3. Voice
    must not try to read it; otherwise the runtime crashes on first
    cancellation.
    """
    import inspect

    from hermes.voice.assistant import VoiceAssistant

    src = inspect.getsource(VoiceAssistant.stop_active)
    assert "self.agent.state.current_run_id" not in src, (
        "stop_active must not read V2's current_run_id field; "
        "V3AgentState does not define it."
    )
    assert "self.agent._server.stop_run" not in src, (
        "stop_active must not call V2's server.stop_run; "
        "V3 owns the entire turn inside process_turn and "
        "cancelling the local task is sufficient."
    )


def test_voice_stop_active_cancels_active_request():
    """V3 cancellation model: ``_active_request.cancel()`` is the
    only required step.
    """
    import inspect

    from hermes.voice.assistant import VoiceAssistant

    src = inspect.getsource(VoiceAssistant.stop_active)
    assert "_active_request.cancel()" in src
