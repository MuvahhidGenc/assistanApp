from __future__ import annotations

import asyncio
from typing import Protocol

from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class SpeechToText(Protocol):
    def is_available(self) -> bool: ...

    @property
    def unavailable_reason(self) -> str: ...

    async def listen(self, *, timeout: float, phrase_limit: float | None = None) -> str | None: ...


class NullSpeechToText:
    """Fallback when microphone/STT is unavailable."""

    def __init__(self, reason: str = "Mikrofon kullanilamiyor") -> None:
        self._reason = reason

    def is_available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    async def listen(self, *, timeout: float, phrase_limit: float | None = None) -> str | None:
        return None


class SpeechRecognitionSTT:
    """Optional STT via speech_recognition + Google Web Speech (free tier)."""

    def __init__(self, *, language: str = "tr-TR") -> None:
        self._language = language
        self._reason = ""
        self._recognizer = None
        self._microphone = None
        self._init_backend()

    def _init_backend(self) -> None:
        try:
            import speech_recognition as sr

            self._recognizer = sr.Recognizer()
            self._microphone = sr.Microphone()
            with self._microphone as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.3)
        except ImportError:
            self._reason = "speechrecognition paketi yuklu degil"
        except Exception as exc:
            self._reason = str(exc)

    def is_available(self) -> bool:
        return self._recognizer is not None and self._microphone is not None

    @property
    def unavailable_reason(self) -> str:
        return self._reason or "ok"

    def _listen_sync(self, *, timeout: float, phrase_limit: float | None) -> str | None:
        import speech_recognition as sr

        assert self._recognizer is not None
        assert self._microphone is not None

        with self._microphone as source:
            audio = self._recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=phrase_limit,
            )
        try:
            text = self._recognizer.recognize_google(audio, language=self._language)
            return str(text).strip()
        except sr.UnknownValueError:
            return None
        except sr.RequestError as exc:
            logger.warning("stt_request_error", error=str(exc))
            return None

    async def listen(self, *, timeout: float, phrase_limit: float | None = None) -> str | None:
        if not self.is_available():
            return None
        try:
            return await asyncio.to_thread(
                self._listen_sync,
                timeout=timeout,
                phrase_limit=phrase_limit,
            )
        except Exception as exc:
            logger.warning("stt_listen_error", error=str(exc))
            return None


def create_stt(*, language: str = "tr-TR") -> SpeechToText:
    from hermes.voice.audio import check_microphone_available

    available, reason = check_microphone_available()
    if not available:
        return NullSpeechToText(reason)
    stt = SpeechRecognitionSTT(language=language)
    if not stt.is_available():
        return NullSpeechToText(stt.unavailable_reason)
    return stt
