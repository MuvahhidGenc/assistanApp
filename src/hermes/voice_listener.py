from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from hermes.utils.logging import get_logger
from hermes.voice.stt import NullSpeechToText, SpeechRecognitionSTT
from hermes.voice.wake_word import DEFAULT_WAKE_WORDS, detect_wake_word

logger = get_logger(__name__)

WAKE_WORDS = DEFAULT_WAKE_WORDS


class VoiceListener:
    """Google STT first; Whisper fallback."""

    def __init__(
        self,
        language: str = "tr-TR",
        wake_words: tuple[str, ...] = WAKE_WORDS,
        *,
        prefer_google: bool = True,
    ) -> None:
        self.language = language
        self.wake_words = wake_words
        self._prefer_google = prefer_google
        self._reason = ""
        self._whisper: Any = None
        self._whisper_tried = False
        self._google = SpeechRecognitionSTT(language=self.language)
        if not self._google.is_available():
            self._reason = self._google.unavailable_reason
            self._google = None

    def is_available(self) -> bool:
        return self._google is not None or self._whisper_available()

    def _whisper_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401

            return True
        except Exception:
            return False

    @property
    def unavailable_reason(self) -> str:
        return self._reason or "ok"

    def parse_wake(self, text: str) -> tuple[str | None, str]:
        return detect_wake_word(text, self.wake_words)

    def _load_whisper(self) -> Any:
        if self._whisper_tried:
            return self._whisper
        self._whisper_tried = True
        try:
            from faster_whisper import WhisperModel

            self._whisper = WhisperModel("base", device="cpu", compute_type="int8")
            logger.info("voice_listener_backend", backend="faster-whisper-base")
        except Exception as exc:
            logger.info("voice_listener_whisper_skip", error=str(exc))
            self._whisper = None
        return self._whisper

    def _listen_whisper(
        self,
        timeout: float,
        phrase_limit: float | None,
        pause_seconds: float | None,
    ) -> str | None:
        import speech_recognition as sr

        model = self._load_whisper()
        if model is None:
            return None
        recognizer = sr.Recognizer()
        recognizer.dynamic_energy_threshold = True
        if pause_seconds is not None:
            recognizer.pause_threshold = max(0.8, float(pause_seconds))
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.35)
            audio = recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=phrase_limit,
            )
        fd, name = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        tmp = Path(name)
        try:
            tmp.write_bytes(audio.get_wav_data())
            segments, _info = model.transcribe(str(tmp), language="tr", beam_size=3)
            text = " ".join(segment.text for segment in segments).strip()
            return text or None
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    async def listen(
        self,
        timeout: float,
        *,
        phrase_limit: float | None = None,
        pause_seconds: float | None = None,
    ) -> str | None:
        backends: list[str] = []
        if self._prefer_google and self._google is not None:
            backends.append("google")
        if self._whisper_available():
            backends.append("whisper")

        for backend in backends:
            try:
                if backend == "google":
                    heard = await self._google.listen(
                        timeout,
                        phrase_limit=phrase_limit,
                        pause_seconds=pause_seconds,
                    )
                else:
                    heard = await asyncio.to_thread(
                        self._listen_whisper,
                        timeout,
                        phrase_limit,
                        pause_seconds,
                    )
                if heard:
                    logger.info("stt_heard", backend=backend, text=heard[:240])
                    return heard
            except Exception as exc:
                err = str(exc)
                if "timed out" in err.casefold():
                    logger.debug("stt_listen_timeout", backend=backend)
                else:
                    logger.warning("stt_listen_error", backend=backend, error=err)
        return None


def create_voice_listener(language: str = "tr-TR") -> VoiceListener | NullSpeechToText:
    from hermes.voice.audio import check_microphone_available

    available, reason = check_microphone_available()
    if not available:
        return NullSpeechToText(reason)
    listener = VoiceListener(language=language)
    if listener.is_available():
        return listener
    return NullSpeechToText(listener.unavailable_reason)
