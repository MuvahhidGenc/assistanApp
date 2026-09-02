"""Standalone TTS smoke test — no orchestrator, reports each pipeline stage."""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hermes.utils.logging import get_logger

logger = get_logger(__name__)

SMOKE_TEXT = "Merhaba, bu bir ses testidir."


@dataclass
class SmokeStage:
    name: str
    ok: bool
    detail: str = ""
    exception_type: str = ""
    exception_message: str = ""


@dataclass
class SmokeReport:
    text: str = SMOKE_TEXT
    backend_setting: str = ""
    elevenlabs_key_present: bool = False
    engine_class: str = ""
    engine_available: bool = False
    edge_import_ok: bool = False
    mp3_path: str = ""
    mp3_bytes: int = 0
    mp3_readable: bool = False
    playback_started: bool = False
    playback_completed: bool = False
    stages: list[SmokeStage] = field(default_factory=list)
    passed: bool = False

    def add(self, name: str, ok: bool, detail: str = "", exc: BaseException | None = None) -> None:
        self.stages.append(
            SmokeStage(
                name=name,
                ok=ok,
                detail=detail,
                exception_type=type(exc).__name__ if exc else "",
                exception_message=str(exc) if exc else "",
            )
        )


def _print_report(report: SmokeReport) -> None:
    lines = [
        "=== Hermes TTS Smoke Test ===",
        f"Text: {report.text}",
        f"Config backend: {report.backend_setting}",
        f"ElevenLabs key: {'yes' if report.elevenlabs_key_present else 'NO'}",
        f"Engine: {report.engine_class} (available={report.engine_available})",
        f"Edge import: {'OK' if report.edge_import_ok else 'FAIL'}",
        f"MP3 path: {report.mp3_path or '-'}",
        f"MP3 bytes: {report.mp3_bytes}",
        f"MP3 readable: {'yes' if report.mp3_readable else 'no'}",
        f"Playback started: {'yes' if report.playback_started else 'no'}",
        f"Playback completed: {'yes' if report.playback_completed else 'no'}",
        "--- Stages ---",
    ]
    for stage in report.stages:
        status = "PASS" if stage.ok else "FAIL"
        line = f"[{status}] {stage.name}"
        if stage.detail:
            line += f" — {stage.detail}"
        lines.append(line)
        if stage.exception_type:
            lines.append(f"       exception: {stage.exception_type}: {stage.exception_message}")
    lines.append(f"=== RESULT: {'PASS' if report.passed else 'FAIL'} ===")
    text = "\n".join(lines)
    print(text)

    try:
        from pathlib import Path

        log_path = Path(os.environ.get("LOCALAPPDATA", ".")) / "HermesClient" / "tts-smoke.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass


async def run_smoke_test(
    *,
    backend: str = "elevenlabs",
    text: str = SMOKE_TEXT,
    api_key: str = "",
    speak: bool = True,
) -> SmokeReport:
    from hermes.config.credentials import resolve_elevenlabs_api_key
    from hermes.voice.tts import EdgeTurkishTTS, create_tts

    report = SmokeReport(text=text.strip(), backend_setting=backend)
    resolved_key, _ = resolve_elevenlabs_api_key(config_value=api_key)
    report.elevenlabs_key_present = bool(resolved_key or api_key)

    try:
        import edge_tts  # noqa: F401

        report.edge_import_ok = True
        report.add("EDGE_ENGINE_AVAILABLE", True, "edge-tts import OK")
    except Exception as exc:
        report.add("EDGE_ENGINE_AVAILABLE", False, exc=exc)

    try:
        tts = create_tts(backend=backend, elevenlabs_api_key=api_key or resolved_key)
        report.engine_class = type(tts).__name__
        report.engine_available = tts.is_available()
        report.add(
            "TTS_BACKEND_SELECTED",
            report.engine_available,
            f"class={report.engine_class}",
        )
    except Exception as exc:
        report.add("TTS_BACKEND_SELECTED", False, exc=exc)
        return report

    if not speak or not report.engine_available:
        report.passed = report.engine_available and report.edge_import_ok
        return report

    report.add("TTS_REQUEST", True, f"chars={len(report.text)}")

    if not speak:
        report.passed = True
        return report

    try:
        await tts.speak(report.text)
        report.playback_started = True
        report.playback_completed = True
        report.add("PLAYBACK_SUCCESS", True)
        report.passed = True
    except Exception as exc:
        report.add("PLAYBACK_FAILED", False, exc=exc)
        logger.error(
            "tts_smoke_failed",
            error=str(exc),
            exc_type=type(exc).__name__,
            traceback=traceback.format_exc(),
        )

    edge = EdgeTurkishTTS()
    if edge.is_available() and not report.passed and not report.mp3_bytes:
        # Probe synthesis only if full speak failed early
        import os
        import tempfile

        import edge_tts

        handle, name = tempfile.mkstemp(suffix=".mp3")
        os.close(handle)
        tmp = Path(name)
        try:
            communicate = edge_tts.Communicate(report.text, edge._edge_voice)
            await communicate.save(str(tmp))
            report.mp3_path = str(tmp)
            report.mp3_bytes = tmp.stat().st_size if tmp.exists() else 0
            report.mp3_readable = report.mp3_bytes >= 32
            report.add(
                "EDGE_SYNTHESIS_PROBE",
                report.mp3_readable,
                f"bytes={report.mp3_bytes}",
            )
        except Exception as exc:
            report.add("EDGE_SYNTHESIS_PROBE", False, exc=exc)
        finally:
            tmp.unlink(missing_ok=True)

    return report


async def main(argv: list[str] | None = None) -> int:
    from hermes.platform.runtime import configure_ssl_certificates

    configure_ssl_certificates()
    from hermes.config.credentials import resolve_elevenlabs_api_key, elevenlabs_key_hint
    from hermes.config.paths import resolve_config_path
    from hermes.config.settings import AppSettings

    args = list(argv or sys.argv[1:])
    use_config = "--no-config" not in args
    text = SMOKE_TEXT
    for index, arg in enumerate(args):
        if arg == "--text" and index + 1 < len(args):
            text = args[index + 1]

    backend = "elevenlabs"
    api_key = ""
    if use_config:
        settings = AppSettings.load(resolve_config_path(Path("config/default.yaml")))
        backend = settings.voice.tts_backend
        api_key, key_source = resolve_elevenlabs_api_key(
            config_value=settings.elevenlabs_api_key.get_secret_value()
        )
        print(f"ElevenLabs key source: {key_source}")
        if not api_key:
            print(elevenlabs_key_hint())

    report = await run_smoke_test(backend=backend, text=text, api_key=api_key, speak=True)
    _print_report(report)
    return 0 if report.passed else 1
