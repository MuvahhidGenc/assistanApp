"""MUVAHHİD V4 — Audio amplitude read-only UI probe.

Goal (F12 spec): Provide a UI-level integration point so the MUVAHHİD AI Core
can react to real microphone amplitude. This module NEVER writes to the V4
store or any V3 runtime backend — it is a presentation-only read helper.

Fallback strategy (safe, no crashes):
  - If pyaudio / SpeechRecognition are unavailable or microphone permission is
    denied, `AudioProbe.available` returns False and `get_amplitude()` always
    returns 0.0. The caller can then use `SafeFallbackWaveform(seed)` to draw a
    calm idle waveform instead of pretending the amplitude is real.
  - All streams/threads are closed deterministically in destroy()/__del__.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any

try:  # pragma: no cover - audio libs optional at import time
    import pyaudio  # type: ignore
    _PYA_OK = True
except Exception:  # pragma: no cover
    pyaudio = None  # type: ignore
    _PYA_OK = False

try:  # pragma: no cover - optional (SpeechRecognition already used in voice.stt)
    import audioop  # type: ignore
    _AUDIOOP_OK = True
except Exception:  # pragma: no cover
    audioop = None  # type: ignore
    _AUDIOOP_OK = False


class AudioProbe:
    """Read-only real-time microphone RMS probe (UI helper).

    Usage:
        probe = AudioProbe()
        probe.start()
        amp = probe.get_amplitude()  # 0.0 .. 1.0
        probe.stop(); probe.destroy()

    The class is designed to be safe across widget lifecycles: every resource
    has a try/except teardown path. Even if the backend refuses to start the
    stream (no mic, permission, missing libs) the object continues to behave
    like a "zero amplitude" probe and never crashes the UI loop.
    """

    SAMPLE_RATE = 16000
    FRAMES_PER_BUFFER = 512
    TICK_HZ = 30

    def __init__(self) -> None:
        self.available = _PYA_OK
        self._pa: Any = None
        self._stream: Any = None
        self._amp = 0.0
        self._ewma = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._destroyed = False
        if self.available:
            try:
                self._pa = pyaudio.PyAudio()
            except Exception:
                self.available = False
                self._pa = None

    # ------------------------------------------------------------------ API

    def start(self) -> bool:  # pragma: no cover - platform specific
        """Open stream and start sampling. Returns True on success."""
        if not self.available or self._pa is None:
            return False
        if self._stream is not None:
            return True
        try:
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.SAMPLE_RATE,
                input=True,
                frames_per_buffer=self.FRAMES_PER_BUFFER,
            )
        except Exception:
            self.available = False
            self._stream = None
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MuvahhidAudioProbe", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:  # pragma: no cover
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and threading.current_thread() is not t:
            t.join(timeout=0.6)
        self._thread = None
        s = self._stream
        self._stream = None
        if s is not None:
            try:
                s.stop_stream()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    def get_amplitude(self) -> float:
        with self._lock:
            return max(0.0, min(1.0, self._ewma))

    # ------------------------------------------------------------- internal

    def _run(self) -> None:  # pragma: no cover - audio I/O loop
        interval = 1.0 / self.TICK_HZ
        next_t = time.monotonic()
        while not self._stop.is_set():
            try:
                raw = self._stream.read(self.FRAMES_PER_BUFFER, exception_on_overflow=False)
            except Exception:
                self._amp = 0.0
                break
            rms = 0.0
            if _AUDIOOP_OK and raw:
                try:
                    rms = audioop.rms(raw, 2) / 32768.0
                except Exception:
                    rms = 0.0
            # RMS is roughly 0..0.6 in loud speech; compress with power curve to 0..1
            norm = min(1.0, (rms * 3.0) ** 0.85)
            with self._lock:
                self._amp = float(norm)
                # EWMA (smoothing): alpha 0.35 per tick ~ 33ms
                self._ewma = 0.65 * self._ewma + 0.35 * self._amp
            next_t += interval
            sleep_s = max(0.0005, next_t - time.monotonic())
            # Stop can wake early
            if self._stop.wait(sleep_s):
                break

    # -------------------------------------------------------- lifecycle

    def destroy(self) -> None:  # pragma: no cover
        if self._destroyed:
            return
        self._destroyed = True
        try:
            self.stop()
        except Exception:
            pass
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

    def __del__(self) -> None:  # pragma: no cover
        try:
            self.destroy()
        except Exception:
            pass


def SafeFallbackWaveform(seed: float, bars: int = 180) -> list[float]:
    """Idle waveform (never pretends it's real audio).

    A smooth, calm pseudo-wave based on the seed value. Noisy, periodic, but
    visually "breathing". The caller should update seed every frame.
    """
    out: list[float] = []
    pi = math.pi
    for i in range(bars):
        frac = i / float(bars - 1) if bars > 1 else 0.5
        window = math.sin(pi * frac) ** 1.4
        v = (0.45 * math.sin(12.0 * frac + seed * 2.0)
             + 0.28 * math.sin(25.0 * frac - seed * 3.3)
             + 0.18 * math.sin(55.0 * frac + seed * 5.1))
        # bias small positive and scale to window
        amp = max(0.0, (0.5 + 0.5 * v) ** 1.7) * 0.32 * window
        out.append(amp)
    return out
