from __future__ import annotations

import sys


def play_wake_beep(*, frequency: int = 880, duration_ms: int = 120) -> bool:
    """Short beep when wake word is detected. Returns False if beep unavailable."""
    if sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(frequency, duration_ms)
            return True
        except Exception:
            return False

    try:
        print("\x07", end="", flush=True)
        return True
    except Exception:
        return False


def check_microphone_available() -> tuple[bool, str]:
    """
    Check whether microphone capture is likely available.
    Never raises — safe to call when PyAudio/speech libs are missing.
    """
    try:
        import speech_recognition as sr
    except ImportError:
        return (False, "speechrecognition paketi yuklu degil")

    try:
        import pyaudio  # noqa: F401
    except ImportError:
        return (False, "PyAudio yuklu degil — mikrofon icin: pip install PyAudio")

    try:
        mic = sr.Microphone()
    except OSError as exc:
        return False, f"Mikrofon bulunamadi: {exc}"
    except Exception as exc:
        return False, f"Mikrofon baslatilamadi: {exc}"

    try:
        with mic:
            pass
    except Exception as exc:
        return False, f"Mikrofon acilamadi: {exc}"

    return (True, "ok")
