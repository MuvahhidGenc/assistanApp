from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import VoiceSettings
from hermes.utils.logging import get_logger
from hermes.voice import tts as tts_pipeline
from hermes.voice.stt import NullSpeechToText, SpeechToText, create_stt
from hermes.voice.tts import NullTextToSpeech, TextToSpeech, create_tts
from hermes.voice.tts_trace import reset_correlation_id, set_correlation_id, trace_fields
from hermes.voice.wake_word import DEFAULT_WAKE_WORDS, detect_wake_word, is_stop_command

logger = get_logger(__name__)

_START_SPEECH_DELAY_SECONDS = 0.35

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
    _tts_error_notified: bool = field(default=False, init=False)
    _utterance_assembler: Any = field(default=None, init=False)

    def __post_init__(self) -> None:
        from hermes.voice.utterance import UtteranceAssembler

        self._utterance_assembler = UtteranceAssembler(hold_seconds=2.0)
        if self.stt is None:
            self.stt = create_stt(language=self.settings.stt_language)
        if self.tts is None:
            from hermes.config.credentials import get_elevenlabs_api_key

            self.tts = create_tts(
                backend=self.settings.tts_backend,
                language=self.settings.tts_language,
                gender=self.settings.tts_gender,
                voice=self.settings.tts_voice,
                elevenlabs_api_key=get_elevenlabs_api_key(),
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

    async def _notify_tts_error(self, message: str) -> None:
        """Log TTS failure only — never expose technical details in chat UI."""
        logger.error("tts_failuretts_pipeline._audit", detail=message)

    async def _speak_prompt(
        self,
        text: str,
        *,
        resume_activity: str = "listening",
        phase: str = "response",
    ) -> bool:
        if not self._voice_enabled:
            tts_pipeline._audit("TTS_SKIPPED", reason="voice_disabled", phase=phase)
            return False
        if not self.tts:
            logger.error("tts_failuretts_pipeline._audit", **trace_fields(detail="tts_engine_missing"))
            return False
        if not self.tts.is_available():
            logger.error("tts_failuretts_pipeline._audit", **trace_fields(detail="tts_engine_unavailable"))
            return False
        cleaned = text.strip()
        if not cleaned:
            tts_pipeline._audit("TTS_SKIPPED", reason="empty_text", phase=phase)
            return False
        now = time.monotonic()
        if cleaned == self._last_spoken_text and (now - self._last_spoken_at) < 4.0:
            tts_pipeline._audit("TTS_SKIPPED", reason="dedup", phase=phase, chars=len(cleaned))
            return True
        try:
            await self._set_activity("speaking")
            tts_pipeline._audit("TTS_SPEAK_BEGIN", phase=phase, chars=len(cleaned))
            await self.tts.speak(cleaned)
            self._last_spoken_text = cleaned
            self._last_spoken_at = time.monotonic()
            self._tts_error_notified = False
            logger.info("tts_prompt_spoke", **trace_fields(chars=len(cleaned), phase=phase))
            return True
        except Exception as exc:
            logger.error(
                "tts_prompt_error",
                **trace_fields(
                    error=str(exc),
                    exc_type=type(exc).__name__,
                    phase=phase,
                ),
            )
            return False
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

        correlation_id = uuid.uuid4().hex[:12]
        trace_token = set_correlation_id(correlation_id)
        started_at = time.monotonic()
        logger.info(
            "USER_MESSAGE",
            **trace_fields(source=source, chars=len(text), text_preview=text[:120]),
        )
        tts_pipeline._audit(
            "VOICE_ASSISTANT_ACTIVE",
            voice_enabled=self._voice_enabled,
            speak_response=speak_response,
            tts_class=type(self.tts).__name__ if self.tts else "none",
            tts_available=bool(self.tts and self.tts.is_available()),
        )

        try:
            await self.stop_active()
            self._cancelled = False
            if source == "voice":
                await self._notify_user_input(text, source)
            await self._set_activity("thinking")

            start_line: str | None = None
            start_task: asyncio.Task[None] | None = None
            start_spoken = False

            if speak_response and self._voice_enabled and self.tts and self.tts.is_available():
                try:
                    from hermes.voice.response_synthesizer import synthesize_task_started

                    start_line = synthesize_task_started(text)
                    tts_pipeline._audit(
                        "SYNTHESIZER_CALLED",
                        phase="started",
                        line=start_line or "",
                        will_delay=True,
                    )
                except Exception as exc:
                    logger.warning("tts_start_plan_error", **trace_fields(error=str(exc)))
                    start_line = None

            async def _delayed_start() -> None:
                nonlocal start_spoken
                delay = float(
                    getattr(self.settings, "start_speech_delay_seconds", _START_SPEECH_DELAY_SECONDS)
                    or _START_SPEECH_DELAY_SECONDS
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                if self._cancelled or start_spoken or not start_line:
                    return
                elapsed_ms = (time.monotonic() - started_at) * 1000.0
                if elapsed_ms < delay * 1000.0:
                    return
                start_spoken = True
                tts_pipeline._audit("SYNTHESIZER_CALLED", phase="started_speak", line=start_line)
                await self._speak_prompt(start_line, resume_activity="thinking", phase="started")

            if (
                start_line
                and speak_response
                and self._voice_enabled
                and self.tts
                and self.tts.is_available()
            ):
                start_task = asyncio.create_task(_delayed_start())

            async def _run() -> str:
                await self._set_activity("thinking")
                return await self.agent.process_message(text)

            self._active_request = asyncio.create_task(_run())
            try:
                response = await self._active_request
            except asyncio.CancelledError:
                if start_task and not start_task.done():
                    start_task.cancel()
                await self._notify_status("Islem iptal edildi.")
                self._active_request = None
                return
            finally:
                if start_task and not start_task.done():
                    start_task.cancel()
                    try:
                        await start_task
                    except asyncio.CancelledError:
                        pass
                self._active_request = None

            if self._cancelled or not response.strip():
                return

            await self._notify_response(response.strip(), source=source)
            if speak_response and self._voice_enabled and self.tts and self.tts.is_available():
                try:
                    from hermes.voice.response_synthesizer import (
                        is_duplicate_speech,
                        synthesize_task_completed,
                    )

                    tool_results = list(getattr(self.agent.state, "last_tool_results", []) or [])
                    metadata = getattr(self.agent.state, "metadata", None)
                    raw_outcome = (
                        metadata.get("turn_outcome") if isinstance(metadata, dict) else None
                    )
                    complete_line = synthesize_task_completed(
                        text,
                        response.strip(),
                        tool_results,
                        outcome=str(raw_outcome) if raw_outcome else None,
                    )
                    tts_pipeline._audit(
                        "SYNTHESIZER_CALLED",
                        phase="completed",
                        line=complete_line or "",
                    )
                    if complete_line and not is_duplicate_speech(start_line or "", complete_line):
                        await self._speak_prompt(
                            complete_line,
                            resume_activity="idle",
                            phase="completed",
                        )
                    elif not complete_line:
                        tts_pipeline._audit("TTS_SKIPPED", reason="empty_synthesizer_line", phase="completed")
                        await self._set_activity("idle")
                except Exception as exc:
                    logger.warning("tts_speak_error", **trace_fields(error=str(exc)))
                    await self._set_activity("idle")
            else:
                await self._set_activity("idle")
        finally:
            reset_correlation_id(trace_token)

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
        from hermes.voice.utterance import looks_incomplete_utterance

        self._voice_session_active = True
        if self._utterance_assembler is not None:
            self._utterance_assembler.clear()
        await self._set_activity("listening")
        await self._notify_status(f"Sesli mod — dinliyorum ({wake_word})")

        first = initial_command.strip()
        # Do not speak over the user when wake already carried a command fragment.
        if not first:
            await self._speak_prompt("Dinliyorum abi.")
        elif looks_incomplete_utterance(first):
            # Hold mid-phrase ("YouTube'u") — never dispatch as its own turn.
            if self._utterance_assembler is not None:
                self._utterance_assembler.push(first)
            logger.info("utterance_hold_wake_remainder", text=first[:120])
        else:
            await self._dispatch_final_utterance(first)

        if not getattr(self.settings, "continuous_listen", True):
            if self._utterance_assembler is not None and self._utterance_assembler.should_keep_listening():
                # Incomplete remainder without continuous listen — drop, don't agent.
                dropped = self._utterance_assembler.flush(force=True)
                logger.info("utterance_dropped_incomplete", text=(dropped or "")[:120])
            self._voice_session_active = False
            await self._set_activity("idle")
            return

        pause = float(getattr(self.settings, "command_pause_seconds", 1.4) or 1.4)
        empty_listens = 0
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
                    empty_listens += 1
                    holding = (
                        self._utterance_assembler is not None
                        and self._utterance_assembler.should_keep_listening()
                    )
                    if holding and empty_listens < 2:
                        await asyncio.sleep(0.1)
                        continue
                    if holding and self._utterance_assembler is not None:
                        # Still incomplete after silence — drop, do not create a turn.
                        dropped = self._utterance_assembler.flush(force=True)
                        logger.info(
                            "utterance_dropped_incomplete",
                            text=(dropped or "")[:120],
                        )
                    empty_listens = 0
                    await asyncio.sleep(0.15)
                    continue
                empty_listens = 0
                if is_stop_command(heard):
                    if self._utterance_assembler is not None:
                        self._utterance_assembler.clear()
                    await self._notify_status("Sesli mod kapandı.")
                    await self._speak_prompt("Tamam abi.", resume_activity="idle")
                    break
                ready = heard.strip()
                if self._utterance_assembler is not None:
                    ready = self._utterance_assembler.push(heard.strip())
                    if ready is None:
                        logger.info(
                            "utterance_hold_partial_final",
                            text=heard.strip()[:120],
                            pending=self._utterance_assembler.pending[:120],
                        )
                        continue
                await self._dispatch_final_utterance(ready)
        except asyncio.CancelledError:
            raise
        finally:
            self._voice_session_active = False
            if self._utterance_assembler is not None:
                self._utterance_assembler.clear()
            await self._set_activity("idle")

    async def _dispatch_final_utterance(self, command: str) -> None:
        """Only FINAL assembled utterances reach the agent."""
        await self._process_voice_command(command)

    async def _process_voice_command(self, command: str) -> None:
        if not command:
            return
        if getattr(self.settings, "speech_quality_gate", True):
            from hermes.voice.speech_quality import assess_speech_quality

            decision = assess_speech_quality(command)
            if not decision.accept:
                logger.info("speech_quality_reject", reason=decision.reason, text=command[:80])
                return
        if is_stop_command(command):
            await self.stop_active()
            await self._notify_status("Durduruldu.")
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
    from hermes.config.credentials import resolve_elevenlabs_api_key

    api_key, key_source = resolve_elevenlabs_api_key(config_value=elevenlabs_api_key)
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
        if not assistant.tts.is_available():
            logger.error(
                "voice_assistant_tts_unavailable",
                backend=settings.tts_backend,
                key_source=key_source,
            )
    return assistant
