from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.config.settings import VoiceSettings
from hermes.voice.assistant import VoiceAssistant
from hermes.voice.response_synthesizer import (
    TTSEvent,
    is_duplicate_speech,
    is_technical_status,
    sanitize_for_tts,
    should_speak_event,
    synthesize_task_completed,
    synthesize_task_started,
)


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.process_message = AsyncMock(return_value="Tamam.")
    agent.state = MagicMock(last_tool_results=[])
    return agent


class MockTTS:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def is_available(self) -> bool:
        return True

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        return None


def test_internal_status_not_spoken():
    assert is_technical_status("Kontrol ediyorum...") is True
    assert is_technical_status("Yerel tool calistiriliyor: write_file") is True
    assert is_technical_status("execution_target=client") is True
    assert is_technical_status(r"C:\Users\test\Desktop\Proje") is True


def test_sanitize_removes_paths_and_tools():
    cleaned = sanitize_for_tts(r"open_path C:\Users\test\a.txt verified=True")
    assert "open_path" not in cleaned.casefold()
    assert "C:\\" not in cleaned


def test_started_line_is_generic_and_does_not_classify_user_text():
    create = synthesize_task_started("Masaüstünde klasör oluştur.")
    browse = synthesize_task_started("Chrome'u aç.")
    converse = synthesize_task_started("Bugün nasılsın?")

    assert create == browse == converse == "İşlemi gerçekleştiriyorum."
    assert synthesize_task_started("") is None


def test_completed_line_uses_authoritative_response_not_user_intent():
    line = synthesize_task_completed(
        "Chrome'u aç.",
        "Klasör oluşturulamadı; hedef yol belirsiz.",
    )
    assert line == "Klasör oluşturulamadı; hedef yol belirsiz"


def test_completed_line_never_speaks_internal_path_or_tool_name():
    line = synthesize_task_completed(
        "anything",
        r"write_file C:\Users\test\secret.txt verified=True",
    )
    assert line is None or "write_file" not in line.casefold()
    assert line is None or "C:\\" not in line


@pytest.mark.parametrize("outcome", ["failed", "partial", "unsupported", "cancelled"])
def test_explicit_failed_outcome_is_not_spoken_as_success(outcome: str):
    line = synthesize_task_completed(
        "anything",
        "Görev tamamlandı.",
        outcome=outcome,
    )
    assert line == "İşi tamamlayamadım."


def test_question_response_is_spoken_without_reclassification():
    line = synthesize_task_completed(
        "Şunu aç.",
        "Hangi dosyayı açmamı istiyorsun?",
        outcome="question",
    )
    assert line == "Hangi dosyayı açmamı istiyorsun?"


def test_response_synthesizer_has_no_legacy_semantic_dependencies():
    from hermes.voice import response_synthesizer

    source = inspect.getsource(response_synthesizer)
    forbidden = (
        "GoalRouter",
        "ReferenceResolver",
        "local_intent",
        "file_intent",
        "_guess_tool_intent",
    )
    for token in forbidden:
        assert token not in source


def test_duplicate_speech_detection():
    assert is_duplicate_speech("Aynı", "Aynı") is True
    assert is_duplicate_speech("Başlıyor.", "Tamamlandı.") is False


def test_fast_task_skips_delayed_start():
    assert should_speak_event(TTSEvent.TASK_STARTED, elapsed_ms=400.0) is False
    assert should_speak_event(TTSEvent.TASK_COMPLETED, elapsed_ms=400.0) is True


@pytest.mark.asyncio
async def test_typed_message_speaks_authoritative_agent_response(mock_agent):
    mock_agent.process_message = AsyncMock(
        return_value="Klasör doğrulanarak oluşturuldu."
    )
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=VoiceSettings(wake_word_enabled=False),
        agent=mock_agent,
        stt=MagicMock(is_available=lambda: False),
        tts=tts,
    )

    await assistant.handle_text_input("Masaüstünde klasör oluştur.")

    assert tts.spoken == ["Klasör doğrulanarak oluşturuldu"]


@pytest.mark.asyncio
async def test_voice_flag_off_no_tts(mock_agent):
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=VoiceSettings(wake_word_enabled=False),
        agent=mock_agent,
        stt=MagicMock(is_available=lambda: False),
        tts=tts,
    )
    await assistant.configure_voice(voice_enabled=False)
    await assistant.handle_text_input("Chrome'u aç.")
    assert tts.spoken == []


@pytest.mark.asyncio
async def test_slow_operation_uses_generic_start_then_authoritative_response(
    mock_agent,
):
    async def slow(_text: str) -> str:
        import asyncio

        await asyncio.sleep(1.0)
        return "Chrome doğrulanarak açıldı."

    mock_agent.process_message = slow
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=VoiceSettings(wake_word_enabled=False),
        agent=mock_agent,
        stt=MagicMock(is_available=lambda: False),
        tts=tts,
    )

    await assistant.handle_text_input("Chrome'u aç.")

    assert tts.spoken[0] == "İşlemi gerçekleştiriyorum."
    assert tts.spoken[-1] == "Chrome doğrulanarak açıldı"
