from __future__ import annotations

import asyncio
from typing import Protocol

from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class SpeechToText(Protocol):
    def is_available(self) -> bool:
        pass

    @property
    def unavailable_reason(self) -> str:
        pass

    async def listen(
        self,
        timeout: float,
        *,
        phrase_limit: float | None = None,
        pause_seconds: float | None = None,
    ) -> str | None:
        pass


class NullSpeechToText:
    """Fallback when microphone/STT is unavailable."""

    def __init__(self, reason: str = "Mikrofon kullanilamiyor") -> None:
        self._reason = reason

    def is_available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str:
        return self._reason

    async def listen(
        self,
        timeout: float,
        *,
        phrase_limit: float | None = None,
        pause_seconds: float | None = None,
    ) -> str | None:
        return None


class SpeechRecognitionSTT:
    """Optional STT via speech_recognition + Google Web Speech (free tier)."""

    def __init__(self, language: str = "tr-TR") -> None:
        self._language = language
        self._reason = ""
        self._recognizer = None
        self._microphone = None
        self._ambient_calibrated = False
        self._init_backend()

    def _init_backend(self) -> None:
        try:
            import speech_recognition as sr

            self._recognizer = sr.Recognizer()
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.energy_threshold = 300
            # ~1.2s end-of-speech: fast enough after stop, slow enough not to
            # cut "Not defterini aç" into "not defter".
            self._recognizer.pause_threshold = 1.2
            self._recognizer.non_speaking_duration = 0.5
            self._microphone = sr.Microphone()
            with self._microphone as source:
                self._recognizer.adjust_for_ambient_noise(source, duration=0.45)
            self._ambient_calibrated = True
        except ImportError:
            self._reason = "speechrecognition paketi yuklu degil"
        except Exception as exc:
            self._reason = str(exc)

    def is_available(self) -> bool:
        return self._recognizer is not None and self._microphone is not None

    @property
    def unavailable_reason(self) -> str:
        return self._reason or "ok"

    def _listen_sync(
        self,
        timeout: float,
        *,
        phrase_limit: float | None,
        pause_seconds: float | None,
    ) -> str | None:
        import speech_recognition as sr

        assert self._recognizer is not None
        assert self._microphone is not None

        old_pause = self._recognizer.pause_threshold
        if pause_seconds is not None:
            # Floor at 0.9s — aggressive endpoints were creating fake user turns.
            self._recognizer.pause_threshold = max(0.9, float(pause_seconds))
        try:
            with self._microphone as source:
                # Recalibrate only rarely — every listen costs ~350ms and drifts thresholds.
                if not self._ambient_calibrated:
                    self._recognizer.adjust_for_ambient_noise(source, duration=0.35)
                    self._ambient_calibrated = True
                audio = self._recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_limit,
                )
        finally:
            self._recognizer.pause_threshold = old_pause

        try:
            text = self._recognizer.recognize_google(audio, language=self._language)
            cleaned = str(text).strip()
            if cleaned:
                logger.info("stt_google_heard", text=cleaned[:240])
            return cleaned
        except sr.UnknownValueError:
            return None
        except sr.RequestError as exc:
            logger.warning("stt_request_error", error=str(exc))
            return None

    async def listen(
        self,
        timeout: float,
        *,
        phrase_limit: float | None = None,
        pause_seconds: float | None = None,
    ) -> str | None:
        if not self.is_available():
            return None
        try:
            return await asyncio.to_thread(
                self._listen_sync,
                timeout,
                phrase_limit=phrase_limit,
                pause_seconds=pause_seconds,
            )
        except Exception as exc:
            err = str(exc)
            if "timed out" in err.casefold():
                logger.debug("stt_listen_timeout", backend="google")
                return None
            logger.warning("stt_listen_error", error=err)
            return None


def create_stt(language: str = "tr-TR") -> SpeechToText:
    from hermes.voice_listener import create_voice_listener

    return create_voice_listener(language=language)
