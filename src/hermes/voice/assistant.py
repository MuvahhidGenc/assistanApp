from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import VoiceSettings
from hermes.utils.logging import get_logger
from hermes.voice.stt import NullSpeechToText, SpeechToText, create_stt
from hermes.voice.tts import NullTextToSpeech, TextToSpeech, create_tts
from hermes.voice.wake_word import DEFAULT_WAKE_WORDS, detect_wake_word, is_stop_command

logger = get_logger(__name__)

OnUserMessage = Callable[[str], Awaitable[None] | None]


@dataclass
class VoiceAssistant:
    """
    Wake-word voice assistant.

        - Text input always works and is spoken when voice is enabled.
    - Microphone is optional; missing mic never crashes the app.
    - Listens only for wake words, not continuous commands.
    """

    settings: VoiceSettings
    agent: AgentOrchestrator
    on_status: OnUserMessage | None = None
    on_response: Callable[[str, str], Awaitable[None] | None] | None = None
    on_user_input: Callable[[str, str], Awaitable[None] | None] | None = None
    on_activity: Callable[[str], Awaitable[None] | None] | None = None
    on_command: Callable[[str], Awaitable[bool] | bool] | None = None
    stt: SpeechToText | None = None
    tts: TextToSpeech | None = None
    _running: bool = field(default=False, init=False)
    _wake_task: asyncio.Task[None] | None = field(default=None, init=False)
    _active_request: asyncio.Task[str] | None = field(default=None, init=False)
    _cancelled: bool = field(default=False, init=False)
    _voice_enabled: bool = field(default=True, init=False)
    _voice_session_active: bool = field(default=False, init=False)
    _last_spoken_text: str = field(default="", init=False)
    _last_spoken_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if self.stt is None:
            self.stt = create_stt(language=self.settings.stt_language)
        if self.tts is None:
            self.tts = create_tts(
                backend=self.settings.tts_backend,
                language=self.settings.tts_language,
                gender=self.settings.tts_gender,
                voice=self.settings.tts_voice,
                elevenlabs_voice_id=self.settings.elevenlabs_voice_id,
                elevenlabs_model=self.settings.elevenlabs_model,
            )

    @property
    def wake_word_enabled(self) -> bool:
        return self.settings.wake_word_enabled

    @property
    def microphone_available(self) -> bool:
        return self.stt.is_available()

    @property
    def wake_words(self) -> tuple[str, ...]:
        return tuple(self.settings.wake_words or DEFAULT_WAKE_WORDS)

    @property
    def voice_enabled(self) -> bool:
        return self._voice_enabled

    async def configure_voice(
        self,
        *,
        voice_enabled: bool | None = None,
        wake_word_enabled: bool | None = None,
    ) -> None:
        if voice_enabled is not None:
            self._voice_enabled = voice_enabled
        if wake_word_enabled is not None:
            self.settings = self.settings.model_copy(
                update={"wake_word_enabled": wake_word_enabled}
            )
        await self._sync_wake_loop()

    async def _sync_wake_loop(self) -> None:
        if self._wake_task:
            self._wake_task.cancel()
            try:
                await self._wake_task
            except asyncio.CancelledError:
                pass
            self._wake_task = None

        if (
            self._running
            and self._voice_enabled
            and self.settings.wake_word_enabled
            and self.microphone_available
        ):
            self._wake_task = asyncio.create_task(self._wake_word_loop())
            await self._set_activity("listening")
            return

        if self._running and self.settings.wake_word_enabled and not self.microphone_available:
            await self._notify_status(self._mic_unavailable_message())
            await self._set_activity("idle")

    async def start(self) -> None:
        self._running = True
        await self._sync_wake_loop()

    async def stop(self) -> None:
        self._running = False
        self._voice_session_active = False
        await self.stop_active()
        if self._wake_task:
            self._wake_task.cancel()
            try:
                await asyncio.wait_for(self._wake_task, timeout=3.0)
            except (asyncio.CancelledError, TimeoutError):
                pass
            self._wake_task = None

    def _mic_unavailable_message(self) -> str:
        reason = getattr(self.stt, "unavailable_reason", "Mikrofon yok")
        return (
            f"Mikrofon kullanilamiyor ({reason}). "
            "Metin girisi calismaya devam eder; wake word devre disi."
        )

    async def _notify_status(self, message: str) -> None:
        if self.on_status:
            result = self.on_status(message)
            if result is not None:
                await result

    async def _set_activity(self, activity: str) -> None:
        if self.on_activity:
            result = self.on_activity(activity)
            if result is not None:
                await result

    async def _notify_response(self, response: str, source: str) -> None:
        if self.on_response:
            result = self.on_response(response, source)
            if result is not None:
                await result

    async def _notify_user_input(self, text: str, source: str) -> None:
        if self.on_user_input:
            result = self.on_user_input(text, source)
            if result is not None:
                await result

    async def _speak_prompt(self, text: str, *, resume_activity: str = "listening") -> None:
        if not self._voice_enabled or not self.tts or not self.tts.is_available():
            return
        cleaned = text.strip()
        if not cleaned:
            return
        now = time.monotonic()
        if cleaned == self._last_spoken_text and (now - self._last_spoken_at) < 4.0:
            return
        self._last_spoken_text = cleaned
        self._last_spoken_at = now
        try:
            await self._set_activity("speaking")
            await self.tts.speak(cleaned)
        except Exception as exc:
            logger.warning("tts_prompt_error", error=str(exc))
        finally:
            await self._set_activity(resume_activity)

    async def handle_text_input(self, text: str) -> None:
        """Process typed user input — always available."""
        await self._handle_user_message(text.strip(), source="text", speak_response=True)

    async def _handle_user_message(
        self,
        text: str,
        source: str,
        speak_response: bool,
    ) -> None:
        if not text:
            return
        if is_stop_command(text):
            await self.stop_active()
            await self._notify_status("Durduruldu.")
            return

        await self.stop_active()
        self._cancelled = False
        if source == "voice":
            await self._notify_user_input(text, source)
        await self._set_activity("thinking")

        if speak_response and self._voice_enabled and self.tts and self.tts.is_available():
            try:
                from hermes.voice.spoken import spoken_quick_ack

                await self._set_activity("speaking")
                await self.tts.speak(spoken_quick_ack(text))
            except Exception as exc:
                logger.warning("tts_ack_error", error=str(exc))

        async def _run() -> str:
            await self._set_activity("thinking")
            return await self.agent.process_message(text)

        self._active_request = asyncio.create_task(_run())
        try:
            response = await self._active_request
        except asyncio.CancelledError:
            await self._notify_status("Islem iptal edildi.")
            self._active_request = None
            return
        finally:
            self._active_request = None

        if self._cancelled or not response.strip():
            return

        await self._notify_response(response.strip(), source=source)
        if speak_response and self._voice_enabled and self.tts and self.tts.is_available():
            try:
                from hermes.voice.spoken import brief_spoken_reply

                await self._set_activity("speaking")
                await self.tts.speak(brief_spoken_reply(response.strip()))
            except Exception as exc:
                logger.warning("tts_speak_error", error=str(exc))
        await self._set_activity("idle")

    async def stop_active(self) -> None:
        self._cancelled = True
        if self.tts:
            self.tts.stop()
        if self._active_request and not self._active_request.done():
            self._active_request.cancel()
            try:
                await self._active_request
            except asyncio.CancelledError:
                pass
            self._active_request = None

        run_id = self.agent.state.current_run_id
        if run_id:
            try:
                await self.agent._server.stop_run(run_id)
            except Exception:
                return

    async def _wake_word_loop(self) -> None:
        """Listen for wake words only — not continuous command parsing."""
        assert self.stt is not None
        while self._running:
            if self._voice_session_active:
                await asyncio.sleep(0.2)
                continue
            try:
                heard = await self.stt.listen(
                    timeout=self.settings.wake_listen_timeout_seconds,
                    phrase_limit=self.settings.wake_phrase_limit_seconds,
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("wake_listen_error", error=str(exc))
                await asyncio.sleep(0.5)
                continue

            if not heard or not self._running:
                await asyncio.sleep(0.05)
                continue

            if is_stop_command(heard):
                await self.stop_active()
                await self._notify_status("Durduruldu.")
                continue

            wake, remainder = detect_wake_word(heard, self.wake_words)
            if not wake:
                logger.debug("wake_not_detected", heard=heard[:120])
                continue

            await self._enter_voice_session(wake, remainder)

    async def _enter_voice_session(self, wake_word: str, initial_command: str = "") -> None:
        self._voice_session_active = True
        await self._set_activity("listening")
        await self._notify_status(f"Sesli mod — dinliyorum ({wake_word})")
        await self._speak_prompt("Dinliyorum abi.")

        first = initial_command.strip()
        if first:
            await self._process_voice_command(first)

        if not getattr(self.settings, "continuous_listen", True):
            self._voice_session_active = False
            await self._set_activity("idle")
            return

        pause = float(getattr(self.settings, "command_pause_seconds", 2.0) or 2.0)
        try:
            while self._running and self._voice_enabled and self._voice_session_active:
                await self._set_activity("listening")
                await self._notify_status("Konuşmanı bekliyorum...")
                try:
                    heard = await self.stt.listen(
                        timeout=self.settings.command_listen_timeout_seconds,
                        phrase_limit=self.settings.command_phrase_limit_seconds,
                        pause_seconds=pause,
                    )
                except asyncio.CancelledError:
                    raise
                if not self._running or not self._voice_enabled or not self._voice_session_active:
                    break
                if not heard:
                    await asyncio.sleep(0.15)
                    continue
                if is_stop_command(heard):
                    await self._notify_status("Sesli mod kapandı.")
                    await self._speak_prompt("Tamam abi.", resume_activity="idle")
                    break
                await self._process_voice_command(heard.strip())
        except asyncio.CancelledError:
            raise
        finally:
            self._voice_session_active = False
            await self._set_activity("idle")

    async def _process_voice_command(self, command: str) -> None:
        if not command:
            return
        if self.on_command:
            result = self.on_command(command)
            if asyncio.iscoroutine(result):
                handled = await result
            else:
                handled = bool(result)
            if handled:
                return
        await self._notify_status(f"Duydum: {command}")
        await self._handle_user_message(command, source="voice", speak_response=True)


def build_voice_assistant(
    agent: AgentOrchestrator,
    settings: VoiceSettings,
    stt: SpeechToText | None = None,
    tts: TextToSpeech | None = None,
    *,
    elevenlabs_api_key: str = "",
) -> VoiceAssistant:
    assistant = VoiceAssistant(settings=settings, agent=agent, stt=stt, tts=tts)
    from hermes.config.credentials import get_elevenlabs_api_key

    api_key = (elevenlabs_api_key or get_elevenlabs_api_key()).strip()
    if tts is None:
        assistant.tts = create_tts(
            backend=settings.tts_backend,
            language=settings.tts_language,
            gender=settings.tts_gender,
            voice=settings.tts_voice,
            elevenlabs_api_key=api_key,
            elevenlabs_voice_id=settings.elevenlabs_voice_id,
            elevenlabs_model=settings.elevenlabs_model,
        )
    return assistant
