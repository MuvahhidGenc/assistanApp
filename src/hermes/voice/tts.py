from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from pathlib import Path
from typing import Protocol

from hermes.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_EDGE_VOICE = "tr-TR-AhmetNeural"
DEFAULT_ELEVENLABS_VOICE = "pNInz6obpgDQGcFmaJgB"
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class TextToSpeech(Protocol):
    def is_available(self) -> bool:
        pass

    def speak(self, text: str) -> None:
        pass

    def stop(self) -> None:
        pass


class NullTextToSpeech:
    def is_available(self) -> bool:
        return False

    def speak(self, text: str) -> None:
        return None

    def stop(self) -> None:
        return None


class FallbackTextToSpeech:
    """Try primary TTS, then secondary (e.g. ElevenLabs -> Edge)."""

    def __init__(self, primary: TextToSpeech, secondary: TextToSpeech) -> None:
        self._primary = primary
        self._secondary = secondary

    def is_available(self) -> bool:
        return self._primary.is_available() or self._secondary.is_available()

    def stop(self) -> None:
        self._primary.stop()
        self._secondary.stop()

    async def speak(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        for backend in (self._primary, self._secondary):
            if not backend.is_available():
                continue
            try:
                await backend.speak(cleaned)
                return
            except Exception as exc:
                logger.warning("tts_backend_failed", error=str(exc))
        logger.warning("tts_all_backends_failed", chars=len(cleaned))


def pick_edge_voice(preferred: str = "", gender: str = "male") -> str:
    voice = (preferred or DEFAULT_EDGE_VOICE).strip()
    if gender.casefold() == "male" and voice.casefold().endswith("emelneural"):
        return DEFAULT_EDGE_VOICE
    return voice or DEFAULT_EDGE_VOICE


def _run_coro(coro) -> None:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _play_mp3_powershell(path: str) -> bool:
    from hermes.platform.subprocess_win import run_hidden

    uri = Path(path).resolve().as_uri()
    script = (
        f"Add-Type -AssemblyName presentationCore; "
        f"$m = New-Object System.Windows.Media.MediaPlayer; "
        f"$m.Volume = 1.0; "
        f"$m.Open('{uri}'); $m.Play(); "
        f"Start-Sleep -Milliseconds 500; "
        f"for($i=0; $i -lt 200 -and -not $m.NaturalDuration.HasTimeSpan; $i++)"
        f"{{ Start-Sleep -Milliseconds 100 }}; "
        f"while($m.NaturalDuration.HasTimeSpan -and $m.Position -lt $m.NaturalDuration.TimeSpan)"
        f"{{ Start-Sleep -Milliseconds 120 }}; "
        f"$m.Stop(); $m.Close()"
    )
    try:
        proc = run_hidden(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            logger.warning(
                "tts_powershell_play_failed",
                code=proc.returncode,
                stderr=(proc.stderr or b"").decode("utf-8", errors="replace")[:300],
            )
        return proc.returncode == 0
    except Exception as exc:
        logger.warning("tts_powershell_play_failed", error=str(exc))
        return False


class EdgeTurkishTTS:
    """Microsoft Edge TTS — tr-TR-AhmetNeural fallback."""

    def __init__(
        self,
        *,
        language: str = "tr-TR",
        gender: str = "male",
        voice: str = DEFAULT_EDGE_VOICE,
    ) -> None:
        self._lock = threading.Lock()
        self._edge_voice = pick_edge_voice(voice, gender)
        self._available = self._probe_edge()

    def _probe_edge(self) -> bool:
        try:
            import edge_tts  # noqa: F401

            return True
        except Exception as exc:
            logger.warning("tts_edge_unavailable", error=str(exc))
            return False

    def is_available(self) -> bool:
        return self._available

    def stop(self) -> None:
        return None

    def _speak_sync(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or not self._available:
            return
        with self._lock:
            try:
                import edge_tts
            except Exception as exc:
                logger.warning("tts_edge_import_failed", error=str(exc))
                return

            handle, name = tempfile.mkstemp(suffix=".mp3")
            os.close(handle)
            tmp = Path(name)
            try:
                communicate = edge_tts.Communicate(
                    cleaned,
                    self._edge_voice,
                    rate="+4%",
                    pitch="+1Hz",
                )
                _run_coro(communicate.save(str(tmp)))
                if not tmp.exists() or tmp.stat().st_size < 32:
                    logger.warning("tts_edge_empty_mp3")
                    return
                if not _play_mp3_powershell(str(tmp)):
                    logger.warning("tts_playback_failed")
                    return
                logger.info("tts_spoke", backend="edge", voice=self._edge_voice, chars=len(cleaned))
            except Exception as exc:
                logger.warning("tts_edge_failed", error=str(exc))
            finally:
                tmp.unlink(missing_ok=True)

    async def speak(self, text: str) -> None:
        if not text.strip() or not self._available:
            return
        await asyncio.to_thread(self._speak_sync, text)


class ElevenLabsTTS:
    """ElevenLabs multilingual TTS."""

    def __init__(
        self,
        *,
        api_key: str = "",
        voice_id: str = DEFAULT_ELEVENLABS_VOICE,
        model_id: str = DEFAULT_ELEVENLABS_MODEL,
    ) -> None:
        self._lock = threading.Lock()
        self._api_key = (api_key or "").strip()
        self._voice_id = (voice_id or DEFAULT_ELEVENLABS_VOICE).strip()
        self._model_id = (model_id or DEFAULT_ELEVENLABS_MODEL).strip()
        self._available = bool(self._api_key)

    def is_available(self) -> bool:
        return self._available

    def stop(self) -> None:
        return None

    def _synthesize_mp3(self, text: str) -> Path | None:
        import httpx

        url = ELEVENLABS_TTS_URL.format(voice_id=self._voice_id)
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": text,
            "model_id": self._model_id,
            "voice_settings": {
                "stability": 0.42,
                "similarity_boost": 0.78,
                "style": 0.35,
                "use_speaker_boost": True,
            },
        }
        with httpx.Client(timeout=90.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            audio = response.content
        if len(audio) < 64:
            return None
        handle, name = tempfile.mkstemp(suffix=".mp3")
        os.close(handle)
        tmp = Path(name)
        tmp.write_bytes(audio)
        return tmp

    def _speak_sync(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or not self._available:
            return
        with self._lock:
            tmp: Path | None = None
            try:
                tmp = self._synthesize_mp3(cleaned)
                if tmp is None or not tmp.exists():
                    raise RuntimeError("empty mp3 from elevenlabs")
                if not _play_mp3_powershell(str(tmp)):
                    raise RuntimeError("mp3 playback failed")
                logger.info(
                    "tts_spoke",
                    backend="elevenlabs",
                    voice=self._voice_id,
                    chars=len(cleaned),
                )
            except Exception as exc:
                logger.warning("tts_elevenlabs_failed", error=str(exc))
                raise
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)

    async def speak(self, text: str) -> None:
        if not text.strip() or not self._available:
            return
        await asyncio.to_thread(self._speak_sync, text)


Pyttsx3TextToSpeech = EdgeTurkishTTS
WindowsTextToSpeech = EdgeTurkishTTS


def create_tts(
    *,
    backend: str = "edge",
    language: str = "tr-TR",
    gender: str = "male",
    voice: str = DEFAULT_EDGE_VOICE,
    elevenlabs_api_key: str = "",
    elevenlabs_voice_id: str = DEFAULT_ELEVENLABS_VOICE,
    elevenlabs_model: str = DEFAULT_ELEVENLABS_MODEL,
) -> TextToSpeech:
    edge = EdgeTurkishTTS(language=language, gender=gender, voice=voice)
    chosen = (backend or "edge").strip().casefold()
    if chosen == "elevenlabs":
        eleven = ElevenLabsTTS(
            api_key=elevenlabs_api_key,
            voice_id=elevenlabs_voice_id,
            model_id=elevenlabs_model,
        )
        if eleven.is_available() and edge.is_available():
            return FallbackTextToSpeech(eleven, edge)
        if eleven.is_available():
            return eleven
        logger.warning("tts_elevenlabs_unavailable", reason="missing_api_key")
    if edge.is_available():
        logger.info("tts_using_edge")
        return edge
    return NullTextToSpeech()
