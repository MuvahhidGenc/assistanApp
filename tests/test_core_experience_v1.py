"""Core experience: latency path, speech gate, youtube open without sleep."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.observability.turn_trace import TurnTrace, start_turn_trace, reset_turn_trace, turn_stage
from hermes.voice.speech_quality import assess_speech_quality


def test_turn_trace_stages():
    trace, token = start_turn_trace(path="test")
    try:
        with turn_stage("stt"):
            pass
        with turn_stage("execution"):
            pass
        assert "stt" in trace.stages_ms
        assert "execution" in trace.stages_ms
        assert trace.total_ms >= 0
        assert trace.trace_id
    finally:
        reset_turn_trace(token)


def test_speech_quality_rejects_noise():
    assert assess_speech_quality("").accept is False
    assert assess_speech_quality("...").accept is False
    assert assess_speech_quality("um").accept is False
    assert assess_speech_quality("YouTube'u aç").accept is True
    assert assess_speech_quality("tamam şimdi google a git").accept is True


def test_youtube_homepage_open_skips_playback_assist():
    from hermes.agent.local_intent import match_local_intent

    intent = match_local_intent("YouTube'u aç")
    assert intent is not None
    assert intent.request.name == "open_url"
    assert "youtube.com" in str(intent.request.arguments.get("url") or "").casefold()
    assert not intent.request.arguments.get("autoplay")


def test_open_url_tool_does_not_force_youtube_sleep():
    from hermes.tools.windows.computer_control import OpenUrlTool

    tool = OpenUrlTool()
    calls: list[str] = []

    async def fake_run(fn, *a, **k):
        calls.append(getattr(fn, "__name__", str(fn)))
        return {"url": "https://www.youtube.com", "opened": True}

    async def _run() -> None:
        with patch("hermes.tools.windows.computer_control.run_in_thread", side_effect=fake_run):
            result = await tool.execute(url="https://www.youtube.com")
        assert result.success
        assert "open_youtube_with_playback" not in calls
        assert "open_url" in calls

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_early_simple_local_sets_fast_path_trace():
    from hermes.agent.orchestrator import AgentOrchestrator
    from hermes.config.settings import AppSettings

    settings = AppSettings()
    orch = AgentOrchestrator(settings, MagicMock())
    orch._set_status = AsyncMock()
    orch._execute_local_tool = AsyncMock(
        return_value=MagicMock(
            success=True,
            output={"url": "https://www.youtube.com"},
            error=None,
            verified=True,
        )
    )
    orch._remember_tool_result = MagicMock()
    orch._update_conversational_context = MagicMock()
    orch._mission_store = MagicMock()
    orch._mission_store.load_active.return_value = None

    with patch("hermes.context.conversational_context.ConversationalContext.load") as load:
        ctx = MagicMock()
        ctx.reconcile_with_filesystem = MagicMock()
        load.return_value = ctx
        with patch("hermes.agent.conversation_flow.handle_meta_conversation") as meta:
            meta.return_value = MagicMock(handled=False)
            with patch("hermes.client.session_store.append_conversation_turn"):
                text = await orch.process_message("YouTube'u aç")

    assert text
    assert orch.state.metadata.get("turn_outcome") == "fast_local"
    assert orch.state.metadata.get("turn_trace", {}).get("path") == "fast_local"
    ctx.reconcile_with_filesystem.assert_not_called()
