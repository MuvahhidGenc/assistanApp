from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class TextToSpeech(Protocol):
    def is_available(self) -> bool: ...

    async def speak(self, text: str) -> None: ...

    def stop(self) -> None: ...


class NullTextToSpeech:
    def is_available(self) -> bool:
        return False

    async def speak(self, text: str) -> None:
        return

    def stop(self) -> None:
        return


def pick_edge_voice(current: str, gender: str = "") -> str:
    if gender.lower() == "male":
        return "tr-TR-AhmetNeural"
    if current and "Emel" not in current:
        return current
    return "tr-TR-AhmetNeural"


def _play_mp3_powershell(path: str) -> bool:
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(New-Object Media.SoundPlayer '{path}').PlaySync()",
            ],
            check=False,
            capture_output=True,
            timeout=120,
        )
        return True
    except Exception:
        return False


class EdgeTTSSpeech:
    def __init__(self, voice: str = "tr-TR-AhmetNeural", rate: str = "+0%", pitch: str = "+0Hz") -> None:
        self._voice = pick_edge_voice(voice)
        self._rate = rate
        self._pitch = pitch

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401

            return True
        except ImportError:
            return False

    def stop(self) -> None:
        return

    async def speak(self, text: str) -> None:
        if not text.strip():
            return
        await asyncio.to_thread(self._speak_sync, text.strip())

    def _speak_sync(self, text: str) -> None:
        import edge_tts

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, self._voice, rate=self._rate, pitch=self._pitch)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                path = tmp.name
            await communicate.save(path)
            _play_mp3_powershell(path)
            Path(path).unlink(missing_ok=True)

        asyncio.run(_run())


class ElevenLabsTTS:
    def __init__(self, api_key: str, voice_id: str = "") -> None:
        self._api_key = api_key.strip()
        self._voice_id = voice_id.strip() or "21m00Tcm4TlvDq8ikWAM"

    def is_available(self) -> bool:
        return bool(self._api_key)

    def stop(self) -> None:
        return

    async def speak(self, text: str) -> None:
        if not self.is_available() or not text.strip():
            return
        await asyncio.to_thread(self._speak_sync, text.strip())

    def _speak_sync(self, text: str) -> None:
        import httpx

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}"
        with httpx.Client(timeout=60) as client:
            response = client.post(
                url,
                json={"text": text, "model_id": "eleven_multilingual_v2"},
                headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
            )
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp.write(response.content)
                path = tmp.name
        _play_mp3_powershell(path)
        Path(path).unlink(missing_ok=True)


class Pyttsx3TextToSpeech:
    """Windows SAPI TTS via pyttsx3."""

    def __init__(self) -> None:
        import threading

        self._engine = None
        self._lock = threading.Lock()
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            import pyttsx3

            self._engine = pyttsx3.init()
        except Exception as exc:
            logger.warning("tts_init_failed", error=str(exc))
            self._engine = None

    def is_available(self) -> bool:
        return self._engine is not None

    def stop(self) -> None:
        if not self._engine:
            return
        with self._lock:
            try:
                self._engine.stop()
            except Exception:
                pass

    def _speak_sync(self, text: str) -> None:
        if not self._engine:
            return
        with self._lock:
            self._engine.stop()
            self._engine.say(text)
            self._engine.runAndWait()

    async def speak(self, text: str) -> None:
        if not text.strip() or not self._engine:
            return
        await asyncio.to_thread(self._speak_sync, text.strip())


def create_tts(
    backend: str = "edge",
    *,
    edge_voice: str = "tr-TR-AhmetNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
    elevenlabs_api_key: str = "",
    elevenlabs_voice_id: str = "",
) -> TextToSpeech:
    if backend == "elevenlabs" and elevenlabs_api_key:
        tts = ElevenLabsTTS(elevenlabs_api_key, elevenlabs_voice_id)
        if tts.is_available():
            return tts

    edge = EdgeTTSSpeech(edge_voice, rate, pitch)
    if edge.is_available():
        return edge

    pyttsx = Pyttsx3TextToSpeech()
    if pyttsx.is_available():
        return pyttsx
    return NullTextToSpeech()
