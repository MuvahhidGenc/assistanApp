from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import VoiceSettings
from hermes.utils.logging import get_logger
from hermes.voice.audio import play_wake_beep
from hermes.voice.spoken import brief_spoken_reply
from hermes.voice.stt import NullSpeechToText, SpeechToText, create_stt
from hermes.voice.tts import NullTextToSpeech, TextToSpeech, create_tts
from hermes.voice.wake_word import DEFAULT_WAKE_WORDS, detect_wake_word, is_stop_command

logger = get_logger(__name__)

OnUserMessage = Callable[[str], Awaitable[None] | None]


@dataclass
class VoiceAssistant:
    """
    Wake-word voice assistant.

    - Text input always works (via handle_text_input).
    - Microphone is optional; missing mic never crashes the app.
    - Listens only for wake words, not continuous commands.
    """

    settings: VoiceSettings
    agent: AgentOrchestrator
    on_status: OnUserMessage | None = None
    on_response: Callable[[str, str], Awaitable[None] | None] | None = None
    on_user_input: Callable[[str, str], Awaitable[None] | None] | None = None
    stt: SpeechToText | None = None
    tts: TextToSpeech | None = None

    _running: bool = field(default=False, init=False)
    _wake_task: asyncio.Task[None] | None = field(default=None, init=False)
    _active_request: asyncio.Task[str] | None = field(default=None, init=False)
    _cancelled: bool = field(default=False, init=False)
    _voice_enabled: bool = field(default=True, init=False)
    _voice_session_active: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.stt = self.stt or create_stt(language=self.settings.stt_language)
        self.tts = self.tts or create_tts(
            backend=self.settings.tts_backend,
            edge_voice=self.settings.edge_voice,
            rate=self.settings.tts_rate,
            pitch=self.settings.tts_pitch,
            elevenlabs_api_key=self.settings.elevenlabs_api_key,
            elevenlabs_voice_id=self.settings.elevenlabs_voice_id,
        )

    @property
    def wake_word_enabled(self) -> bool:
        return self.settings.wake_word_enabled

    @property
    def microphone_available(self) -> bool:
        return self.stt is not None and self.stt.is_available()

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
            self.settings = self.settings.model_copy(update={"wake_word_enabled": wake_word_enabled})
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
        elif self._running and self.settings.wake_word_enabled and not self.microphone_available:
            await self._notify_status(self._mic_unavailable_message())

    async def start(self) -> None:
        self._running = True
        await self._sync_wake_loop()

    async def stop(self) -> None:
        self._running = False
        await self.stop_active()
        if self._wake_task:
            self._wake_task.cancel()
            try:
                await self._wake_task
            except asyncio.CancelledError:
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

    async def _notify_response(self, response: str, *, source: str) -> None:
        if self.on_response:
            result = self.on_response(response, source)
            if result is not None:
                await result

    async def _notify_user_input(self, text: str, *, source: str) -> None:
        if self.on_user_input:
            result = self.on_user_input(text, source)
            if result is not None:
                await result

    async def handle_text_input(self, text: str) -> None:
        """Process typed user input — always available."""
        await self._handle_user_message(text.strip(), source="text", speak_brief=True)

    async def _handle_user_message(
        self,
        text: str,
        *,
        source: str,
        speak_brief: bool = False,
        speak_response: bool = False,
    ) -> None:
        if not text:
            return

        if is_stop_command(text):
            await self.stop_active()
            await self._notify_status("Durduruldu.")
            return

        await self.stop_active()
        self._cancelled = False
        await self._notify_user_input(text, source=source)

        async def _run() -> str:
            return await self.agent.process_message(text)

        self._active_request = asyncio.create_task(_run())
        try:
            response = await self._active_request
        except asyncio.CancelledError:
            await self._notify_status("Islem iptal edildi.")
            return
        finally:
            self._active_request = None

        if self._cancelled or not response.strip():
            return

        await self._notify_response(response.strip(), source=source)

        if self._voice_enabled and self.tts and self.tts.is_available():
            spoken = brief_spoken_reply(response.strip()) if speak_brief else response.strip()
            if speak_response or speak_brief:
                await self.tts.speak(spoken)

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
                await self.agent._server.stop_run(run_id)  # noqa: SLF001
            except Exception:
                pass

    async def _wake_word_loop(self) -> None:
        assert self.stt is not None
        while self._running:
            try:
                heard = await self.stt.listen(
                    timeout=self.settings.wake_listen_timeout_seconds,
                    phrase_limit=self.settings.wake_phrase_limit_seconds,
                )
            except asyncio.CancelledError:
                break
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
                continue

            await self._on_wake_detected(wake)

            command = remainder
            if not command and self.settings.continuous_listen:
                self._voice_session_active = True
                command = await self._listen_for_command()
                self._voice_session_active = False
            elif not command:
                command = await self._listen_for_command()

            if command:
                await self._handle_user_message(
                    command,
                    source="voice",
                    speak_response=True,
                    speak_brief=True,
                )

    async def _speak_prompt(self, text: str) -> None:
        if self._voice_enabled and self.tts and self.tts.is_available():
            await self.tts.speak(text)

    async def _on_wake_detected(self, wake_word: str) -> None:
        if self.settings.beep_on_wake:
            await asyncio.to_thread(play_wake_beep)
        if self.settings.show_listening_prompt:
            await self._notify_status(f"Sesli mod — Dinliyorum... ({wake_word})")
            await self._speak_prompt("Merhaba abi")

    async def _listen_for_command(self) -> str | None:
        assert self.stt is not None
        heard = await self.stt.listen(
            timeout=self.settings.command_listen_timeout_seconds,
            phrase_limit=self.settings.command_phrase_limit_seconds,
        )
        if not heard:
            await self._notify_status("Anlasilamadi.")
            return None
        if is_stop_command(heard):
            await self.stop_active()
            await self._notify_status("Durduruldu.")
            return None
        return heard.strip()


def build_voice_assistant(
    agent: AgentOrchestrator,
    settings: VoiceSettings,
    *,
    stt: SpeechToText | None = None,
    tts: TextToSpeech | None = None,
) -> VoiceAssistant:
    return VoiceAssistant(
        settings=settings,
        agent=agent,
        stt=stt,
        tts=tts,
    )
