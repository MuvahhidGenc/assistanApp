from __future__ import annotations

from pathlib import Path
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


@pytest.fixture(autouse=True)
def isolated_conversational_context(monkeypatch):
    from hermes.context.conversational_context import ConversationalContext

    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ConversationalContext(),
    )


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.process_message = AsyncMock(return_value="Tamam.")
    agent.state = MagicMock(current_run_id=None, last_tool_results=[])
    agent._server = MagicMock()
    agent._server.stop_run = AsyncMock()
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
    assert is_technical_status("Anladim, isleme basliyorum...") is True
    assert is_technical_status("Kontrol ediyorum...") is True
    assert is_technical_status("Yerel tool calistiriliyor: write_file") is True
    assert is_technical_status("execution_target=client") is True
    assert is_technical_status("C:\\Users\\test\\Desktop\\Proje") is True


def test_tool_name_not_in_tts_output():
    line = synthesize_task_completed(
        "test",
        "write_file basariyla calisti",
    )
    assert line is None or "write_file" not in line.casefold()


def test_path_not_in_tts_output():
    line = synthesize_task_completed(
        "klasor olustur",
        r"C:\Users\ADSM\Desktop\Proje2026 klasorunu olusturdum.",
    )
    assert line is not None
    assert "C:\\" not in line
    assert "Users" not in line
    assert "Proje2026" in line


def test_create_folder_started():
    line = synthesize_task_started("Masaustunde Proje2026 klasoru olustur.")
    assert line is not None
    assert "Proje2026" in line
    assert "oluştur" in line.casefold() or "olustur" in line.casefold()
    assert "create_folder" not in line.casefold()


def test_create_folder_completed():
    line = synthesize_task_completed(
        "Masaustunde Proje2026 klasoru olustur.",
        "Proje2026 klasorunu masaustunde olusturdum.",
    )
    assert line == "Tamam, Proje2026 klasörünü oluşturdum."


def test_create_file_started_and_completed():
    start = synthesize_task_started("Icine test.txt olustur")
    done = synthesize_task_completed(
        "Icine test.txt olustur",
        "Test dosyasini olusturdum.",
    )
    assert start is not None
    assert "oluştur" in start.casefold() or "olustur" in start.casefold()
    assert done == "Tamam, oluşturdum."


def test_open_file_started_and_completed():
    start = synthesize_task_started("Dosyayi ac")
    done = synthesize_task_completed("Dosyayi ac", "Dosyayi actim.")
    assert start == "Dosyayı açıyorum."
    assert done == "Dosyayı açtım."


def test_open_folder_started_and_completed():
    start = synthesize_task_started("Klasoru ac")
    done = synthesize_task_completed("Klasoru ac", "Klasoru actim.")
    assert start == "Klasörü açıyorum."
    assert done == "Açtım."


def test_open_app_started_and_completed():
    start = synthesize_task_started("Chrome'u ac")
    done = synthesize_task_completed("Chrome'u ac", "Chrome'u actim.")
    assert start == "Chrome'u açıyorum."
    assert done == "Chrome'u açtım."


def test_verification_failure_not_success():
    line = synthesize_task_completed(
        "Dosyayi ac",
        "Acma komutu gonderildi ancak pencerenin acildigini dogrulayamadim: C:\\x\\y.txt",
    )
    assert line is not None
    assert "doğrula" in line.casefold() or "dogrula" in line.casefold()
    assert "C:\\" not in line
    assert "olusturdum" not in line.casefold()


def test_incomplete_outcome_is_never_spoken_as_success():
    for outcome, text in (
        ("failed", "Gorev tamamlandi."),
        ("partial", "Gorev tamamlandi."),
        ("waiting", "Tamam, bitti."),
        ("question", "Gorev tamamlandi."),
        ("unsupported", "Gorev tamamlandi."),
        (None, "Mission kismen tamamlandi:\n- arama yapildi"),
        (None, "Gorev kismen tamamlandi; zorunlu bir adim gerceklesmedi."),
    ):
        line = synthesize_task_completed("dosyayi ac", text, outcome=outcome)
        spoken = (line or "").casefold()
        assert "tamam, bitti" not in spoken
        assert "tamam, hallettim" not in spoken


def test_completed_outcome_may_use_success_phrase():
    line = synthesize_task_completed(
        "isi bitir",
        "Gorev tamamlandi. Dosya acildi.",
        outcome="completed",
    )
    assert line == "Tamam, bitti."


def test_ambiguous_reference_spoken():
    line = synthesize_task_completed(
        "dosyayi ac",
        "Hangi dosyayi acmami istiyorsun?",
    )
    assert line is not None
    assert "?" in line or "anlayamad" in line.casefold()


def test_duplicate_speech_detection():
    assert is_duplicate_speech("Ayni", "Ayni") is True
    assert is_duplicate_speech("Dosyayi aciyorum.", "Dosyayi actim.") is False


def test_sanitize_removes_paths_and_tools():
    cleaned = sanitize_for_tts("open_path C:\\Users\\test\\a.txt verified=True")
    assert "open_path" not in cleaned.casefold()
    assert "C:\\" not in cleaned


def test_fast_task_skips_delayed_start():
    assert should_speak_event(TTSEvent.TASK_STARTED, elapsed_ms=400.0) is False
    assert should_speak_event(TTSEvent.TASK_COMPLETED, elapsed_ms=400.0) is True


def test_write_with_content_completed():
    line = synthesize_task_completed(
        "Icine notlar.txt olustur ve icine Merhaba yaz",
        "Notlar dosyasini olusturdum ve Merhaba yazdim.",
    )
    assert line == "Tamam, oluşturdum ve yazdım."


def test_content_modify_started():
    line = synthesize_task_started("Icerigini sadece TEST yap.")
    assert line == "Dosyanın içeriğini değiştiriyorum."


@pytest.mark.asyncio
async def test_typed_message_produces_final_tts(mock_agent):
    mock_agent.process_message = AsyncMock(
        return_value="Proje2026 klasorunu masaustunde olusturdum."
    )
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=VoiceSettings(wake_word_enabled=False),
        agent=mock_agent,
        stt=MagicMock(is_available=lambda: False),
        tts=tts,
    )
    await assistant.handle_text_input("Masaustunde Proje2026 klasoru olustur.")
    assert tts.spoken
    assert all("write_file" not in s.casefold() for s in tts.spoken)
    assert any("Proje2026" in s for s in tts.spoken)


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
    await assistant.handle_text_input("Chrome'u ac")
    assert tts.spoken == []


@pytest.mark.asyncio
async def test_fast_operation_single_tts_line(mock_agent):
    mock_agent.process_message = AsyncMock(return_value="Chrome'u actim.")
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=VoiceSettings(wake_word_enabled=False),
        agent=mock_agent,
        stt=MagicMock(is_available=lambda: False),
        tts=tts,
    )
    await assistant.handle_text_input("Chrome'u ac")
    assert len(tts.spoken) == 1
    assert tts.spoken[0] == "Chrome'u açtım."


@pytest.mark.asyncio
async def test_slow_operation_can_speak_start_and_complete(mock_agent):
    async def slow(_text: str) -> str:
        import asyncio

        await asyncio.sleep(1.0)
        return "Chrome'u actim."

    mock_agent.process_message = slow
    tts = MockTTS()
    assistant = VoiceAssistant(
        settings=VoiceSettings(wake_word_enabled=False),
        agent=mock_agent,
        stt=MagicMock(is_available=lambda: False),
        tts=tts,
    )
    await assistant.handle_text_input("Chrome'u ac")
    assert len(tts.spoken) >= 2
    assert "açıyorum" in tts.spoken[0].casefold() or "aciyorum" in tts.spoken[0].casefold()
    assert tts.spoken[-1] == "Chrome'u açtım."


def test_brief_spoken_reply_delegates_to_synthesizer():
    from hermes.voice.spoken import brief_spoken_reply

    line = brief_spoken_reply("Klasoru actim.", user_message="Klasoru ac")
    assert line == "Açtım"
