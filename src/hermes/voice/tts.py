from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Any, Protocol

from hermes.utils.logging import get_logger
from hermes.voice.tts_trace import trace_fields

logger = get_logger(__name__)

DEFAULT_EDGE_VOICE = "tr-TR-AhmetNeural"
DEFAULT_ELEVENLABS_VOICE = "pNInz6obpgDQGcFmaJgB"
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


def _audit(event: str, **fields: Any) -> None:
    """Structured TTS pipeline audit — visible in application logs."""
    safe = trace_fields(**{key: value for key, value in fields.items() if value is not None})
    logger.info(event, **safe)


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

    async def speak(self, text: str) -> None:
        return None

    def stop(self) -> None:
        return None


class FallbackTextToSpeech:
    """Try primary TTS, then secondary (e.g. ElevenLabs -> Edge -> Windows SAPI)."""

    def __init__(self, *backends: TextToSpeech) -> None:
        self._backends = tuple(backends)
        self._speak_lock = asyncio.Lock()

    def is_available(self) -> bool:
        return any(backend.is_available() for backend in self._backends)

    def stop(self) -> None:
        for backend in self._backends:
            backend.stop()
        stop_all_playback()

    async def speak(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        async with self._speak_lock:
            _audit("TTS_REQUEST", chars=len(cleaned))
            errors: list[str] = []
            for backend in self._backends:
                backend_name = type(backend).__name__
                if not backend.is_available():
                    if backend_name == "ElevenLabsTTS":
                        _audit("ELEVENLABS_SKIPPED_NO_KEY")
                    elif backend_name == "WindowsSapiTTS":
                        _audit("WINDOWS_SAPI_SKIPPED_UNAVAILABLE")
                    else:
                        _audit("EDGE_SKIPPED_UNAVAILABLE")
                    continue
                try:
                    _audit("TTS_BACKEND_SELECTED", backend=backend_name)
                    _audit("BACKEND_SELECTED", backend=backend_name)
                    await backend.speak(cleaned)
                    _audit("PLAYBACK_COMPLETED", backend=backend_name, chars=len(cleaned))
                    return
                except Exception as exc:
                    err = f"{backend_name}={type(exc).__name__}: {exc}"
                    errors.append(err)
                    _audit(
                        "PLAYBACK_FAILED",
                        backend=backend_name,
                        exception_type=type(exc).__name__,
                        exception_message=str(exc),
                        traceback=traceback.format_exc()[-1200:],
                    )
            _audit(
                "TTS_ALL_BACKENDS_FAILED",
                chars=len(cleaned),
                errors=" | ".join(errors),
            )
            raise RuntimeError(errors[-1] if errors else "TTS backends failed")


def pick_edge_voice(preferred: str = "", gender: str = "male") -> str:
    voice = (preferred or DEFAULT_EDGE_VOICE).strip()
    if gender.casefold() == "male" and voice.casefold().endswith("emelneural"):
        return DEFAULT_EDGE_VOICE
    return voice or DEFAULT_EDGE_VOICE


class PlaybackError(RuntimeError):
    pass


_PLAYBACK_PROCS: list[Any] = []
_PLAYBACK_LOCK = threading.Lock()


def stop_all_playback() -> None:
    """Kill active PowerShell MediaPlayer processes (barge-in)."""
    with _PLAYBACK_LOCK:
        procs = list(_PLAYBACK_PROCS)
        _PLAYBACK_PROCS.clear()
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def _play_mp3_powershell(path: str) -> None:
    """Play MP3 via PowerShell System.Windows.Media.MediaPlayer. Raises PlaybackError."""
    import subprocess

    from hermes.platform.subprocess_win import hidden_creationflags, hidden_startupinfo

    mp3 = Path(path)
    if not mp3.exists():
        raise PlaybackError(f"AUDIO_FILE_MISSING path={path}")
    size = mp3.stat().st_size
    _audit("AUDIO_FILE_CREATED", path=str(mp3.resolve()), bytes=size)
    _audit("MP3_CREATED", path=str(mp3.resolve()), bytes=size)
    if size < 32:
        raise PlaybackError(f"AUDIO_FILE_EMPTY bytes={size}")

    uri = mp3.resolve().as_uri()
    thread_name = threading.current_thread().name
    _audit(
        "PLAYBACK_STARTED",
        path=str(mp3.resolve()),
        uri=uri,
        mechanism="powershell_mediaplayer",
        thread=thread_name,
    )
    script = (
        f"Add-Type -AssemblyName presentationCore; "
        f"$m = New-Object System.Windows.Media.MediaPlayer; "
        f"$m.Volume = 1.0; "
        f"$m.Open('{uri}'); $m.Play(); "
        f"Start-Sleep -Milliseconds 350; "
        f"for($i=0; $i -lt 200 -and -not $m.NaturalDuration.HasTimeSpan; $i++)"
        f"{{ Start-Sleep -Milliseconds 80 }}; "
        f"while($m.NaturalDuration.HasTimeSpan -and $m.Position -lt $m.NaturalDuration.TimeSpan)"
        f"{{ Start-Sleep -Milliseconds 100 }}; "
        f"$m.Stop(); $m.Close()"
    )
    cmd = [
        "powershell",
        "-NoProfile",
        "-WindowStyle",
        "Hidden",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    try:
        proc: Any | None = None
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=hidden_startupinfo(),
            creationflags=hidden_creationflags(),
        )
        with _PLAYBACK_LOCK:
            _PLAYBACK_PROCS.append(proc)
        _audit(
            "PLAYBACK_PROCESS_STARTED",
            mechanism="powershell_mediaplayer",
            pid=proc.pid,
            thread=thread_name,
        )
        stdout_b, stderr_b = proc.communicate(timeout=120)
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        if proc is not None:
            proc.kill()
        _audit(
            "PLAYBACK_FAILED",
            mechanism="powershell_mediaplayer",
            reason="timeout",
            thread=thread_name,
        )
        raise PlaybackError("powershell playback timed out") from exc
    except Exception as exc:
        _audit(
            "PLAYBACK_FAILED",
            mechanism="powershell_mediaplayer",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            thread=thread_name,
        )
        raise PlaybackError(str(exc)) from exc
    finally:
        with _PLAYBACK_LOCK:
            if proc is not None and proc in _PLAYBACK_PROCS:
                _PLAYBACK_PROCS.remove(proc)

    stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()
    stdout = (stdout_b or b"").decode("utf-8", errors="replace").strip()
    if returncode != 0:
        _audit(
            "PLAYBACK_FAILED",
            mechanism="powershell_mediaplayer",
            returncode=returncode,
            stderr=stderr[:500],
            stdout=stdout[:300],
            thread=thread_name,
        )
        raise PlaybackError(
            f"powershell rc={returncode} stderr={stderr[:300]} stdout={stdout[:200]}"
        )
    _audit(
        "PLAYBACK_COMPLETED",
        mechanism="powershell_mediaplayer",
        path=str(mp3.resolve()),
        thread=thread_name,
    )
    _audit("PLAYBACK_SUCCESS", mechanism="powershell_mediaplayer", path=str(mp3.resolve()))


def _ssl_verification_error(exc: BaseException) -> bool:
    text = str(exc).casefold()
    if "certificate_verify_failed" in text or "sslcertverificationerror" in text:
        return True
    if "clientconnectorcertificateerror" in type(exc).__name__.casefold():
        return True
    return False


class EdgeTurkishTTS:
    """Microsoft Edge TTS — tr-TR-AhmetNeural."""

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
            _audit(
                "EDGE_SKIPPED_UNAVAILABLE",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            return False

    def is_available(self) -> bool:
        return self._available

    def stop(self) -> None:
        stop_all_playback()

    async def _synthesize_mp3(self, text: str, *, insecure_ssl: bool = False) -> Path:
        import edge_tts

        if insecure_ssl:
            from hermes.platform.runtime import configure_edge_tts_ssl_insecure

            configure_edge_tts_ssl_insecure()
            _audit("EDGE_SSL_RETRY_INSECURE")

        handle, name = tempfile.mkstemp(suffix=".mp3")
        os.close(handle)
        tmp = Path(name)
        _audit("EDGE_SYNTHESIS_START", voice=self._edge_voice, chars=len(text))
        try:
            communicate = edge_tts.Communicate(
                text,
                self._edge_voice,
                rate="+4%",
                pitch="+1Hz",
            )
            await communicate.save(str(tmp))
            size = tmp.stat().st_size if tmp.exists() else 0
            if not tmp.exists() or size < 32:
                _audit("EDGE_SYNTHESIS_FAILED", bytes=size, reason="empty_mp3")
                raise RuntimeError(f"edge TTS produced empty mp3 (bytes={size})")
            _audit("EDGE_SYNTHESIS_SUCCESS", path=str(tmp), bytes=size)
            _audit("MP3_CREATED", path=str(tmp), bytes=size)
            return tmp
        except Exception as exc:
            _audit(
                "EDGE_SYNTHESIS_FAILED",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
                traceback=traceback.format_exc()[-1200:],
            )
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"edge TTS synthesis failed: {exc}") from exc

    async def speak(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        if not self._available:
            raise RuntimeError("edge TTS unavailable (edge-tts not importable)")
        with self._lock:
            tmp: Path | None = None
            try:
                _audit("TTS_REQUEST", backend="edge", chars=len(cleaned))
                _audit("EDGE_FALLBACK_SELECTED", voice=self._edge_voice)
                _audit("BACKEND_SELECTED", backend="EdgeTurkishTTS")
                try:
                    tmp = await self._synthesize_mp3(cleaned, insecure_ssl=False)
                except RuntimeError as exc:
                    if _ssl_verification_error(exc.__cause__ or exc):
                        tmp = await self._synthesize_mp3(cleaned, insecure_ssl=True)
                    else:
                        raise
                await asyncio.to_thread(_play_mp3_powershell, str(tmp))
                _audit("tts_spoke", backend="edge", voice=self._edge_voice, chars=len(cleaned))
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)


class WindowsSapiTTS:
    """Offline Windows speech via pyttsx3 — no network / SSL."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._available = self._probe()

    def _probe(self) -> bool:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.stop()
            return True
        except Exception as exc:
            _audit(
                "WINDOWS_SAPI_SKIPPED_UNAVAILABLE",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            return False

    def is_available(self) -> bool:
        return self._available

    def stop(self) -> None:
        stop_all_playback()

    def _speak_sync(self, text: str) -> None:
        import pyttsx3

        thread_name = threading.current_thread().name
        _audit("PLAYBACK_STARTED", mechanism="windows_sapi", thread=thread_name)
        _audit("PLAYBACK_PROCESS_STARTED", mechanism="windows_sapi", thread=thread_name)
        engine = pyttsx3.init()
        try:
            for voice in engine.getProperty("voices"):
                name = (voice.name or "").casefold()
                if "turk" in name or "tr-" in name:
                    engine.setProperty("voice", voice.id)
                    break
            engine.setProperty("rate", 165)
            engine.say(text)
            engine.runAndWait()
        finally:
            try:
                engine.stop()
            except Exception:
                pass
        _audit("PLAYBACK_COMPLETED", mechanism="windows_sapi", thread=thread_name)
        _audit("PLAYBACK_SUCCESS", mechanism="windows_sapi")

    async def speak(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or not self._available:
            raise RuntimeError("Windows SAPI unavailable")
        with self._lock:
            _audit("TTS_REQUEST", backend="windows_sapi", chars=len(cleaned))
            _audit("BACKEND_SELECTED", backend="WindowsSapiTTS")
            await asyncio.to_thread(self._speak_sync, cleaned)
            _audit("tts_spoke", backend="windows_sapi", chars=len(cleaned))


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
        stop_all_playback()

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
        _audit(
            "tts_elevenlabs_request",
            voice_id=self._voice_id,
            model=self._model_id,
            chars=len(text),
        )
        with httpx.Client(timeout=90.0) as client:
            response = client.post(url, json=payload, headers=headers)
            if response.status_code in (401, 403):
                _audit(
                    "tts_elevenlabs_auth_failed",
                    status_code=response.status_code,
                    detail=_elevenlabs_error_detail(response),
                )
                raise RuntimeError(
                    f"ElevenLabs API anahtari reddedildi (HTTP {response.status_code})."
                )
            if response.status_code == 429:
                _audit("tts_elevenlabs_rate_limited", status_code=429)
                raise RuntimeError("ElevenLabs istek limiti asildi (HTTP 429).")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _audit(
                    "tts_elevenlabs_http_error",
                    status_code=exc.response.status_code,
                    detail=_elevenlabs_error_detail(exc.response),
                )
                raise RuntimeError(
                    f"ElevenLabs TTS basarisiz (HTTP {exc.response.status_code})."
                ) from exc
            audio = response.content
        if len(audio) < 64:
            _audit("tts_elevenlabs_empty_audio", bytes=len(audio))
            return None
        handle, name = tempfile.mkstemp(suffix=".mp3")
        os.close(handle)
        tmp = Path(name)
        tmp.write_bytes(audio)
        _audit("tts_elevenlabs_audio_ready", bytes=len(audio))
        _audit("MP3_CREATED", path=str(tmp), bytes=len(audio))
        return tmp

    async def speak(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned or not self._available:
            return
        with self._lock:
            tmp: Path | None = None
            try:
                _audit("TTS_REQUEST", backend="elevenlabs", chars=len(cleaned))
                _audit("BACKEND_SELECTED", backend="ElevenLabsTTS")
                tmp = await asyncio.to_thread(self._synthesize_mp3, cleaned)
                if tmp is None or not tmp.exists():
                    raise RuntimeError("empty mp3 from elevenlabs")
                await asyncio.to_thread(_play_mp3_powershell, str(tmp))
                _audit(
                    "tts_spoke",
                    backend="elevenlabs",
                    voice=self._voice_id,
                    chars=len(cleaned),
                )
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)


def _elevenlabs_error_detail(response) -> str:
    try:
        body = response.text
    except Exception:
        return ""
    return (body or "")[:240]


def _chain_with_sapi(*backends: TextToSpeech) -> TextToSpeech:
    sapi = WindowsSapiTTS()
    chain = [b for b in backends if b.is_available()]
    if sapi.is_available():
        chain.append(sapi)
    if not chain:
        return NullTextToSpeech()
    if len(chain) == 1:
        return chain[0]
    return FallbackTextToSpeech(*chain)


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
    from hermes.config.credentials import elevenlabs_key_hint, resolve_elevenlabs_api_key

    edge = EdgeTurkishTTS(language=language, gender=gender, voice=voice)
    chosen = (backend or "edge").strip().casefold()
    resolved_key, key_source = resolve_elevenlabs_api_key(config_value=elevenlabs_api_key)
    api_key = (elevenlabs_api_key or resolved_key or "").strip()

    if chosen == "elevenlabs":
        if not api_key:
            _audit("ELEVENLABS_SKIPPED_NO_KEY", reason="elevenlabs_api_key_missing")
            if edge.is_available():
                _audit("EDGE_FALLBACK_SELECTED", voice=edge._edge_voice)
                _audit("TTS_BACKEND_SELECTED", backend="edge", elevenlabs_configured=False)
                return _chain_with_sapi(edge)
            return _chain_with_sapi()

        _audit(
            "elevenlabs_api_key_loaded",
            source=key_source if not elevenlabs_api_key else "parameter",
            key_length=len(api_key),
        )
        eleven = ElevenLabsTTS(
            api_key=api_key,
            voice_id=elevenlabs_voice_id,
            model_id=elevenlabs_model,
        )
        if edge.is_available():
            _audit(
                "TTS_BACKEND_SELECTED",
                backend="elevenlabs+edge_fallback",
                elevenlabs_configured=True,
            )
            return _chain_with_sapi(eleven, edge)
        _audit("TTS_BACKEND_SELECTED", backend="elevenlabs", edge_available=False)
        return _chain_with_sapi(eleven)

    if edge.is_available():
        _audit("TTS_BACKEND_SELECTED", backend="edge")
        return _chain_with_sapi(edge)
    _audit("TTS_BACKEND_SELECTED", backend="null", reason="edge_unavailable")
    return _chain_with_sapi()
