#!/usr/bin/env python3
"""Integration smoke: VoiceAssistant → synthesizer → TTS → playback (not direct tts.speak)."""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TEST_TEXT = "Merhaba Ömer, bu Hermes ses testidir."
USER_MESSAGE = "Masaüstüne test123 adlı bir klasör oluştur."
AGENT_RESPONSE = "Test123 klasörünü masaüstünde oluşturdum."

REQUIRED_EVENTS = (
    "TTS_REQUEST",
    "VOICE_ASSISTANT_ACTIVE",
    "SYNTHESIZER_CALLED",
    "BACKEND_SELECTED",
    "EDGE_SYNTHESIS_SUCCESS",
    "MP3_CREATED",
    "PLAYBACK_STARTED",
    "PLAYBACK_PROCESS_STARTED",
    "PLAYBACK_COMPLETED",
)


def _collect_events() -> tuple[list[dict[str, Any]], Any]:
    captured: list[dict[str, Any]] = []

    def _audit(event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        captured.append(row)

    from hermes.voice import tts as tts_mod

    original = tts_mod._audit
    tts_mod._audit = _audit  # type: ignore[assignment]
    return captured, original


def _restore_audit(original: Any) -> None:
    from hermes.voice import tts as tts_mod

    tts_mod._audit = original  # type: ignore[assignment]


async def run_flow(*, speak: bool = True, user_message: str = USER_MESSAGE) -> dict[str, Any]:
    from hermes.config.settings import VoiceSettings
    from hermes.platform.runtime import configure_ssl_certificates
    from hermes.voice.assistant import VoiceAssistant
    from hermes.voice.tts import create_tts
    from hermes.voice.tts_trace import reset_correlation_id, set_correlation_id
    from hermes.utils.logging import get_logger

    configure_ssl_certificates()
    logger = get_logger(__name__)
    captured, original_audit = _collect_events()
    correlation_id = uuid.uuid4().hex[:12]
    token = set_correlation_id(correlation_id)

    result: dict[str, Any] = {
        "correlation_id": correlation_id,
        "passed": False,
        "events_seen": [],
        "missing_events": list(REQUIRED_EVENTS),
        "spoken_lines": [],
        "playback_completed": False,
        "errors": [],
    }

    try:
        agent = MagicMock()
        agent.process_message = AsyncMock(return_value=AGENT_RESPONSE)
        agent.state = MagicMock(
            last_tool_results=[{"name": "create_folder", "success": True, "output": "ok"}]
        )
        agent._server = MagicMock()
        agent._server.stop_run = AsyncMock()

        settings = VoiceSettings(enabled=True, tts_backend="elevenlabs")
        tts = create_tts(
            backend=settings.tts_backend,
            language=settings.tts_language,
            gender=settings.tts_gender,
            voice=settings.tts_voice,
            elevenlabs_api_key="",
        )

        assistant = VoiceAssistant(settings=settings, agent=agent, tts=tts)
        assistant._voice_enabled = True

        if speak:
            await assistant.handle_text_input(user_message)
        else:
            from hermes.voice.response_synthesizer import synthesize_task_completed

            line = synthesize_task_completed(user_message, AGENT_RESPONSE, agent.state.last_tool_results)
            logger.info("SYNTHESIZER_CALLED", phase="completed", line=line, correlation_id=correlation_id)
            if line:
                await assistant._speak_prompt(line)

        events_seen = {row["event"] for row in captured}
        for row in captured:
            if row["event"] in {"PLAYBACK_COMPLETED", "PLAYBACK_SUCCESS"}:
                result["playback_completed"] = True

        result["events_seen"] = sorted(events_seen)
        result["missing_events"] = [e for e in REQUIRED_EVENTS if e not in events_seen]
        result["audit_log"] = captured
        result["passed"] = not result["missing_events"] and result["playback_completed"]
    except Exception as exc:
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        reset_correlation_id(token)
        _restore_audit(original_audit)

    return result


def _print_result(result: dict[str, Any]) -> None:
    lines = [
        "=== Hermes Client TTS Flow Integration ===",
        f"Correlation: {result.get('correlation_id')}",
        f"Playback completed: {result.get('playback_completed')}",
        f"Events seen: {', '.join(result.get('events_seen', []))}",
    ]
    missing = result.get("missing_events") or []
    if missing:
        lines.append(f"Missing events: {', '.join(missing)}")
    for err in result.get("errors") or []:
        lines.append(f"ERROR: {err}")
    lines.append(f"=== RESULT: {'PASS' if result.get('passed') else 'FAIL'} ===")
    print("\n".join(lines))


async def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    speak = "--no-speak" not in args
    user_message = USER_MESSAGE
    for index, arg in enumerate(args):
        if arg == "--text" and index + 1 < len(args):
            user_message = args[index + 1]

    result = await run_flow(speak=speak, user_message=user_message)
    _print_result(result)
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
