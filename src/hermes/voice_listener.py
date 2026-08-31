from __future__ import annotations

from hermes.voice.wake_word import DEFAULT_WAKE_WORDS, detect_wake_word, normalize_voice_text

WAKE_WORDS = DEFAULT_WAKE_WORDS


class VoiceListener:
    """Lightweight wake-word parser for voice input routing."""

    def __init__(self, wake_words: tuple[str, ...] | list[str] | None = None) -> None:
        self.wake_words = tuple(wake_words or WAKE_WORDS)

    def parse_wake(self, text: str) -> tuple[str | None, str]:
        wake, remainder = detect_wake_word(text, self.wake_words)
        return wake, remainder

    @staticmethod
    def normalize(text: str) -> str:
        return normalize_voice_text(text)
