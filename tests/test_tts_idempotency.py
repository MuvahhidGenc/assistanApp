from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.local_intent import LocalIntent
from hermes.agent.user_messages import format_open_app_message, format_open_path_message
from hermes.server.models import ToolResultPayload
from hermes.tools.manifest import LocalToolRequest
from hermes.tools.windows.file_tools import WriteFileTool
from hermes.tools.windows.pc_actions import CreateFolderTool, OpenPathTool
from hermes.tools.windows.computer_control import OpenAppTool
from hermes.voice.tts import EdgeTurkishTTS, create_tts


def test_create_tts_without_elevenlabs_key_uses_edge_directly():
    from hermes.voice.tts import EdgeTurkishTTS, FallbackTextToSpeech

    tts = create_tts(backend="elevenlabs", elevenlabs_api_key="")
    assert isinstance(tts, (EdgeTurkishTTS, FallbackTextToSpeech))
    assert tts.is_available()


@pytest.mark.asyncio
async def test_edge_tts_generates_mp3_and_plays(monkeypatch, tmp_path):
    tts = EdgeTurkishTTS()
    monkeypatch.setattr(tts, "_available", True)

    class FakeCommunicate:
        def __init__(self, text, voice, rate="", pitch=""):
            self.text = text

        async def save(self, path: str) -> None:
            Path(path).write_bytes(b"\xff\xfb" + b"\x00" * 128)

    monkeypatch.setitem(__import__("sys").modules, "edge_tts", MagicMock(Communicate=FakeCommunicate))
    with patch("hermes.voice.tts._play_mp3_powershell"):
        await tts.speak("Merhaba, ses testi.")


@pytest.mark.asyncio
async def test_edge_playback_failure_raises():
    tts = EdgeTurkishTTS()
    with patch.object(tts, "_available", True):
        with patch.object(tts, "_synthesize_mp3", return_value=Path("fake.mp3")):
            with patch(
                "hermes.voice.tts._play_mp3_powershell",
                side_effect=RuntimeError("playback failed"),
            ):
                with pytest.raises(RuntimeError, match="playback failed"):
                    await tts.speak("Merhaba")


@pytest.mark.asyncio
async def test_tts_failure_does_not_surface_in_ui_status():
    from hermes.config.settings import VoiceSettings
    from hermes.voice.assistant import VoiceAssistant

    class FailingTTS:
        def is_available(self) -> bool:
            return True

        async def speak(self, text: str) -> None:
            raise RuntimeError("TTS backends failed")

        def stop(self) -> None:
            return None

    agent = MagicMock()
    agent.process_message = AsyncMock(return_value="It klasorunu olusturdum.")
    agent.state = MagicMock(last_tool_results=[{"name": "create_folder", "success": True}])

    statuses: list[str] = []

    async def capture_status(message: str) -> None:
        statuses.append(message)

    assistant = VoiceAssistant(
        settings=VoiceSettings(),
        agent=agent,
        tts=FailingTTS(),
    )
    assistant.on_status = capture_status
    assistant._voice_enabled = True
    await assistant.handle_text_input("Masaustune It diye bir klasor olustur")
    assert not any("TTS backends failed" in item for item in statuses)
    assert not any("Sesli yanit verilemedi" in item for item in statuses)


@pytest.mark.asyncio
async def test_typed_message_triggers_tts_speak():
    from hermes.config.settings import VoiceSettings
    from hermes.voice.assistant import VoiceAssistant

    class FakeTTS:
        spoken: list[str] = []

        def is_available(self) -> bool:
            return True

        async def speak(self, text: str) -> None:
            FakeTTS.spoken.append(text)

        def stop(self) -> None:
            return None

    agent = MagicMock()
    agent.process_message = AsyncMock(return_value="Chrome'u actim.")
    agent.state = MagicMock(last_tool_results=[{"name": "open_app", "success": True}])

    assistant = VoiceAssistant(
        settings=VoiceSettings(),
        agent=agent,
        tts=FakeTTS(),
    )
    assistant._voice_enabled = True
    await assistant.handle_text_input("Chrome'u ac")
    assert FakeTTS.spoken


def test_open_app_reuses_existing_chrome_window():
    from hermes.tools.windows import input_backend as ib

    with patch.object(ib, "_resolve_app_path", return_value="C:\\chrome.exe"):
        with patch.object(ib, "find_app_window_title", return_value="Google Chrome"):
            with patch.object(ib, "focus_window", return_value={"focused": True}) as focus:
                with patch.object(ib.subprocess, "Popen") as popen:
                    result = ib.open_application("chrome", user_message="Chrome'u ac")
    assert result["reused"] is True
    assert result["verified"] is True
    popen.assert_not_called()
    focus.assert_called_once()


def test_open_app_reuses_existing_notepad_window():
    from hermes.tools.windows import input_backend as ib

    with patch.object(ib, "_resolve_app_path", return_value="notepad.exe"):
        with patch.object(ib, "find_app_window_title", return_value="Untitled - Notepad"):
            with patch.object(ib, "focus_window", return_value={"focused": True}):
                with patch.object(ib.subprocess, "Popen") as popen:
                    result = ib.open_application("notepad")
    assert result["reused"] is True
    popen.assert_not_called()


def test_open_path_reuses_existing_explorer(tmp_path):
    from hermes.tools.windows import input_backend as ib

    folder = tmp_path / "Test2026"
    folder.mkdir()
    with patch.object(ib, "find_explorer_window_for_folder", return_value="Test2026 - File Explorer"):
        with patch.object(ib, "focus_window", return_value={"focused": True}):
            with patch.object(ib.os, "startfile") as startfile:
                result = ib.open_path_on_windows(folder)
    assert result["reused"] is True
    assert result["verified"] is True
    startfile.assert_not_called()


def test_new_chrome_window_bypasses_reuse():
    from hermes.tools.windows import input_backend as ib

    with patch.object(ib, "_resolve_app_path", return_value="C:\\chrome.exe"):
        with patch.object(ib, "find_app_window_title", return_value="Google Chrome"):
            with patch.object(ib.subprocess, "Popen") as popen:
                popen.return_value = MagicMock(pid=123)
                with patch.object(ib, "find_app_window_title", side_effect=["Google Chrome", "Google Chrome"]):
                    result = ib.open_application("chrome", user_message="yeni Chrome penceresi ac")
    assert result["reused"] is False
    popen.assert_called_once()


@pytest.mark.asyncio
async def test_write_file_does_not_open_explorer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: tmp_path / "test.txt",
    )
    target = tmp_path / "test.txt"
    tool = WriteFileTool()
    with patch("os.startfile") as startfile:
        result = await tool.execute(path="test.txt", content="Merhaba")
    assert result.success
    assert target.read_text(encoding="utf-8") == "Merhaba"
    startfile.assert_not_called()


@pytest.mark.asyncio
async def test_write_file_does_not_open_notepad(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: tmp_path / "test.txt",
    )
    tool = WriteFileTool()
    with patch("hermes.tools.windows.input_backend.open_application") as open_app:
        result = await tool.execute(path="test.txt", content="Merhaba")
    assert result.success
    open_app.assert_not_called()


@pytest.mark.asyncio
async def test_open_path_opens_file_with_startfile(tmp_path):
    target = tmp_path / "test.txt"
    target.write_text("hello", encoding="utf-8")
    tool = OpenPathTool()
    with patch(
        "hermes.tools.windows.pc_actions.run_in_thread",
        side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    ):
        with patch(
            "hermes.tools.windows.input_backend.open_path_on_windows",
            return_value={"path": str(target), "opened": True, "verified": True, "is_directory": False},
        ) as open_path:
            result = await tool.execute(path=str(target))
    assert result.success
    open_path.assert_called_once()


def test_success_message_only_after_verified_open_path():
    intent = LocalIntent(LocalToolRequest("open_path", {"path": "C:\\x"}), summary="x")
    unverified = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": "C:\\x", "verified": False, "verification_note": "Acma komutu gonderildi ancak pencerenin acildigini dogrulayamadim: C:\\x"},
    )
    text = format_open_path_message(intent, unverified)
    assert "actim" not in text.casefold()

    verified = ToolResultPayload(
        tool_call_id="2",
        success=True,
        output={"path": "C:\\x", "verified": True, "is_directory": True},
    )
    text_ok = format_open_path_message(intent, verified)
    assert "actim" in text_ok.casefold()


def test_tts_sanitizer_blocks_internal_tokens():
    from hermes.voice.response_synthesizer import sanitize_for_tts, synthesize_task_completed

    cleaned = sanitize_for_tts("open_path C:\\Users\\test verified=True")
    assert "open_path" not in cleaned.casefold()
    assert "c:\\users" not in cleaned.casefold()

    line = synthesize_task_completed(
        "Chrome ac",
        "LOCAL_TOOL write_file C:\\secret\\a.txt",
        [{"name": "write_file", "success": True}],
    )
    assert line is None or "write_file" not in (line or "").casefold()


def test_repeat_open_app_message_shows_already_open():
    intent = LocalIntent(LocalToolRequest("open_app", {"app": "chrome"}), summary="chrome")
    result = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"app": "chrome", "reused": True, "verified": True},
    )
    text = format_open_app_message(intent, result)
    assert "zaten acikti" in text.casefold()


@pytest.mark.asyncio
async def test_create_folder_skips_when_already_exists(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    folder = desktop / "Test2026"
    folder.mkdir(parents=True)
    monkeypatch.setattr("hermes.tools.windows.pc_actions.Path.home", lambda: tmp_path)
    tool = CreateFolderTool()
    result = await tool.execute(path="Test2026", user_message="Test2026 klasoru olustur")
    assert result.success
    assert result.output.get("already_existed") is True


@pytest.mark.asyncio
async def test_write_file_skips_when_same_content(tmp_path, monkeypatch):
    target = tmp_path / "test.txt"
    target.write_text("Merhaba", encoding="utf-8")
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: target,
    )
    tool = WriteFileTool()
    result = await tool.execute(path="test.txt", content="Merhaba")
    assert result.success
    assert result.output.get("unchanged") is True
