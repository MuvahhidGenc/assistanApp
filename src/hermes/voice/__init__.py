"""Voice assistant: wake word, STT, TTS."""

from hermes.voice.assistant import VoiceAssistant, build_voice_assistant
from hermes.voice.wake_word import (
    DEFAULT_WAKE_WORDS,
    STOP_COMMANDS,
    detect_wake_word,
    is_stop_command,
)

__all__ = [
    "DEFAULT_WAKE_WORDS",
    "STOP_COMMANDS",
    "VoiceAssistant",
    "build_voice_assistant",
    "detect_wake_word",
    "is_stop_command",
]
