from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.ui.state import ConnectionStatus
from hermes.ui.worker import BackgroundWorker, WorkerCommand, WorkerCommandPayload


@pytest.mark.asyncio
async def test_worker_check_connection_connected():
    worker = BackgroundWorker()
    worker._app = MagicMock()
    worker._app.server.health = AsyncMock(return_value=MagicMock(status="ok"))
    worker._app.agent.initialize_session = AsyncMock(return_value="sess-1")
    worker._app.agent.state.session_notice = None
    events: list[str] = []
    worker.on_event = lambda e, p: events.append(e)

    await worker._check_connection()

    assert worker.state.connection == ConnectionStatus.CONNECTED
    worker._app.agent.initialize_session.assert_awaited()
    assert "state" in events


@pytest.mark.asyncio
async def test_worker_check_connection_error():
    worker = BackgroundWorker()
    worker._app = MagicMock()
    worker._app.server.health = AsyncMock(side_effect=RuntimeError("offline"))

    await worker._check_connection()

    assert worker.state.connection == ConnectionStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_worker_check_connection_hides_raw_connect_error():
    from hermes.server.client import HermesServerError

    worker = BackgroundWorker()
    worker._app = MagicMock()
    worker._app.server.health = AsyncMock(
        side_effect=HermesServerError("All connection attempts failed")
    )

    await worker._check_connection()

    assert worker.state.connection == ConnectionStatus.DISCONNECTED
    assert worker.state.status_text == "Sunucu bekleniyor..."


@pytest.mark.asyncio
async def test_worker_reconnect_reinitializes_session():
    worker = BackgroundWorker()
    worker.state.set_connection(ConnectionStatus.DISCONNECTED, detail="offline")
    worker._app = MagicMock()
    worker._app.server.health = AsyncMock(return_value=MagicMock(status="ok"))
    worker._app.agent.initialize_session = AsyncMock(return_value="sess-re")
    worker._app.agent.state.session_notice = None

    await worker._check_connection()

    assert worker.state.connection == ConnectionStatus.CONNECTED
    worker._app.agent.initialize_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_send_message():
    worker = BackgroundWorker()
    worker._voice = MagicMock()
    worker._voice.handle_text_input = AsyncMock()
    worker._notifications_enabled = False
    events: list[tuple[str, dict]] = []
    worker.on_event = lambda e, p: events.append((e, p))

    await worker._handle_command(
        WorkerCommandPayload(WorkerCommand.SEND_MESSAGE, {"text": "merhaba"})
    )

    worker._voice.handle_text_input.assert_awaited_once_with("merhaba")
    assert any(e == "message" for e, _ in events)


@pytest.mark.asyncio
async def test_worker_status_callback_accepts_v3_phase_vocabulary():
    from hermes.runtime.orchestrator import V3AgentPhase
    from hermes.ui.state import ActivityMode

    worker = BackgroundWorker()
    worker._app = MagicMock()
    worker._notifications_enabled = False
    worker.on_event = lambda e, p: None
    worker._wire_agent_callbacks()

    callback = worker._app.agent._on_status
    await callback(V3AgentPhase.REASONING, "Reasoning", {})

    assert worker.state.activity is ActivityMode.THINKING


@pytest.mark.asyncio
async def test_worker_maps_v3_observing_and_awaiting_user_phases():
    from hermes.runtime.orchestrator import V3AgentPhase
    from hermes.ui.state import ActivityMode

    worker = BackgroundWorker()
    worker._app = MagicMock()
    worker._notifications_enabled = False
    worker.on_event = lambda e, p: None
    worker._wire_agent_callbacks()
    callback = worker._app.agent._on_status

    await callback(V3AgentPhase.OBSERVING, "Observing", {})
    assert worker.state.activity is ActivityMode.EXECUTING

    await callback(V3AgentPhase.AWAITING_USER, "Which file?", {})
    assert worker.state.activity is ActivityMode.IDLE


@pytest.mark.asyncio
async def test_worker_resolve_approval_sets_future():
    worker = BackgroundWorker()
    loop = asyncio.get_running_loop()
    worker._loop = loop
    worker._approval_future = loop.create_future()
    worker.resolve_approval("evet")
    await asyncio.sleep(0.01)
    assert worker._approval_future.result() == "evet"


@pytest.mark.asyncio
async def test_worker_blocks_new_message_while_approval_pending():
    worker = BackgroundWorker()
    loop = asyncio.get_running_loop()
    worker._loop = loop
    worker._approval_future = loop.create_future()
    errors: list[str] = []
    worker.on_event = lambda e, p: errors.append(p.get("message", "")) if e == "error" else None

    worker.send_message("dns degistir google yap")

    assert worker._approval_is_pending()
    assert any("Onay bekleniyor" in msg for msg in errors)


@pytest.mark.asyncio
async def test_worker_set_voice_works_without_microphone():
    worker = BackgroundWorker()
    worker._voice = MagicMock()
    worker._voice.microphone_available = False
    worker._voice.voice_enabled = True
    worker._voice.configure_voice = AsyncMock()
    worker.on_event = lambda e, p: None

    await worker._handle_command(
        WorkerCommandPayload(WorkerCommand.SET_VOICE, {"enabled": True})
    )

    assert worker._voice.configure_voice.await_count >= 1


@pytest.mark.asyncio
async def test_worker_shutdown_stops_voice_and_app():
    worker = BackgroundWorker()
    worker._running = True
    worker._voice = MagicMock()
    worker._voice.stop = AsyncMock()
    worker._app = MagicMock()
    worker._app.shutdown = AsyncMock()
    worker._connection_task = asyncio.create_task(asyncio.sleep(60))
    voice = worker._voice
    app = worker._app

    await worker._shutdown_services()

    voice.stop.assert_awaited_once()
    app.shutdown.assert_awaited_once()
    assert worker._voice is None
    assert worker._app is None
    assert worker.state.connection == ConnectionStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_worker_reload_config_restarts_services():
    worker = BackgroundWorker()
    worker._running = True
    worker._voice = MagicMock()
    worker._voice.stop = AsyncMock()
    worker._voice.microphone_available = True
    worker._voice.voice_enabled = True
    worker._voice.wake_word_enabled = False
    worker._app = MagicMock()
    worker._app.shutdown = AsyncMock()
    worker._app.settings = MagicMock()
    worker._app.settings.ui.notifications_enabled = True
    worker._app.settings.voice.enabled = True
    worker._app.settings.voice.wake_word_enabled = True
    worker._app.settings.ui.connection_check_interval_seconds = 60
    worker._app.agent.initialize_session = AsyncMock()
    worker._app.server.health = AsyncMock(return_value=MagicMock(status="ok"))
    worker._voice.configure_voice = AsyncMock()
    worker._voice.start = AsyncMock()

    events: list[tuple[str, dict]] = []
    worker.on_event = lambda e, p: events.append((e, p))

    async def fake_start_services() -> bool:
        worker._voice = worker._voice or MagicMock()
        worker._app = worker._app or MagicMock()
        return True

    worker._start_services = fake_start_services  # type: ignore[method-assign]
    voice = worker._voice
    app = worker._app

    await worker._reload_config()

    voice.stop.assert_awaited_once()
    app.shutdown.assert_awaited_once()
    assert any(e == "config_reloaded" and p.get("ok") for e, p in events)


@pytest.mark.asyncio
async def test_worker_handle_reload_config_delegates():
    worker = BackgroundWorker()
    worker._reload_config = AsyncMock()

    await worker._handle_command(WorkerCommandPayload(WorkerCommand.RELOAD_CONFIG))

    worker._reload_config.assert_awaited_once()
