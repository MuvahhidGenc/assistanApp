from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.orchestrator import AgentOrchestrator, AgentPhase
from hermes.config.settings import AppSettings
from hermes.server.models import Run, RunEvent, RunEventType, RunStatus, Session


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings.load()


@pytest.mark.asyncio
async def test_process_message_local_dns_skips_server(settings: AppSettings):
    server = MagicMock()
    server.create_run = AsyncMock()
    orchestrator = AgentOrchestrator(settings, server)
    orchestrator._execute_local_tool = AsyncMock(  # noqa: SLF001
        return_value=MagicMock(success=True, output={"adapter": "Wi-Fi", "servers": ["8.8.8.8"]}, error=None)
    )
    result = await orchestrator.process_message("dns degistir google yap")
    server.create_run.assert_not_called()
    assert "DNS" in result
    orchestrator._execute_local_tool.assert_awaited()  # noqa: SLF001


@pytest.mark.asyncio
async def test_process_message_run_completed(settings: AppSettings):
    server = MagicMock()
    server.create_session = AsyncMock(return_value=Session(id="sess-1"))
    server.create_run = AsyncMock(
        return_value=Run(run_id="run_test", status=RunStatus.STARTED)
    )
    server.update_session = AsyncMock(return_value=Session(id="sess-1"))

    async def fake_stream(run_id: str):
        yield RunEvent(
            type=RunEventType.MESSAGE_DELTA,
            data={"delta": "Merhaba, "},
        )
        yield RunEvent(
            type=RunEventType.MESSAGE_DELTA,
            data={"delta": "test basarili."},
        )
        yield RunEvent(
            type=RunEventType.RUN_COMPLETED,
            data={"output": "Merhaba, test basarili."},
        )

    server.stream_events = fake_stream
    server.model = "hermes-agent"

    orchestrator = AgentOrchestrator(settings, server)
    phases: list[str] = []
    orchestrator._on_status = lambda phase, msg, extra: phases.append(phase.value)  # noqa: SLF001

    result = await orchestrator.process_message("test mesaji", session_id="sess-1")

    assert "test basarili" in result
    assert result.count("test basarili") == 1
    server.create_run.assert_called_once()
    call_kwargs = server.create_run.call_args.kwargs
    assert call_kwargs.get("instructions")
    assert "LOCAL_TOOL" in call_kwargs["instructions"]
    assert AgentPhase.COMPLETED.value in phases


@pytest.mark.asyncio
async def test_process_message_local_tool_e2e(settings: AppSettings):
    server = MagicMock()
    server.update_session = AsyncMock(return_value=Session(id="sess-local"))
    server.create_run = AsyncMock(
        side_effect=[
            Run(run_id="run_1", status=RunStatus.STARTED),
            Run(run_id="run_2", status=RunStatus.STARTED),
        ]
    )

    async def fake_stream(run_id: str):
        if run_id == "run_1":
            yield RunEvent(
                type=RunEventType.MESSAGE_DELTA,
                data={"delta": 'LOCAL_TOOL {"name": "get_system_info", "arguments": {}}'},
            )
            yield RunEvent(
                type=RunEventType.RUN_COMPLETED,
                data={"output": 'LOCAL_TOOL {"name": "get_system_info", "arguments": {}}'},
            )
            return

        yield RunEvent(
            type=RunEventType.MESSAGE_DELTA,
            data={"delta": "Bilgisayariniz Windows 11, 16 GB RAM."},
        )
        yield RunEvent(
            type=RunEventType.RUN_COMPLETED,
            data={"output": "Bilgisayariniz Windows 11, 16 GB RAM."},
        )

    server.stream_events = fake_stream

    orchestrator = AgentOrchestrator(settings, server)
    result = await orchestrator.process_message(
        "Makine ozelliklerimi ozetle",
        session_id="sess-local",
    )

    assert server.create_run.call_count == 2
    second_input = server.create_run.call_args_list[1].args[0]
    assert second_input.startswith("TOOL_RESULT")
    assert "get_system_info" in second_input
    assert "Windows 11" in result or "RAM" in result


@pytest.mark.asyncio
async def test_process_message_chrome_open_url_e2e(settings: AppSettings):
    server = MagicMock()
    server.update_session = AsyncMock(return_value=Session(id="sess-chrome"))
    server.create_run = AsyncMock(
        side_effect=[
            Run(run_id="run_1", status=RunStatus.STARTED),
            Run(run_id="run_2", status=RunStatus.STARTED),
            Run(run_id="run_3", status=RunStatus.STARTED),
        ]
    )

    async def fake_stream(run_id: str):
        if run_id == "run_1":
            yield RunEvent(
                type=RunEventType.RUN_COMPLETED,
                data={"output": 'LOCAL_TOOL {"name": "open_app", "arguments": {"app": "chrome"}}'},
            )
            return
        if run_id == "run_2":
            yield RunEvent(
                type=RunEventType.RUN_COMPLETED,
                data={
                    "output": 'LOCAL_TOOL {"name": "open_url", "arguments": {"url": "https://google.com"}}'
                },
            )
            return
        yield RunEvent(
            type=RunEventType.RUN_COMPLETED,
            data={"output": "Chrome acildi ve google.com yuklendi."},
        )

    server.stream_events = fake_stream

    orchestrator = AgentOrchestrator(settings, server)
    with (
        patch("hermes.tools.windows.input_backend.open_application", return_value={"app": "chrome", "pid": 1}),
        patch(
            "hermes.tools.windows.input_backend.open_url",
            return_value={"url": "https://google.com", "opened": True},
        ),
    ):
        result = await orchestrator.process_message(
            "Chrome ac ve google.com'a git",
            session_id="sess-chrome",
        )

    assert server.create_run.call_count == 0
    assert "Chrome" in result or "google" in result.lower() or "acildi" in result.lower()


@pytest.mark.asyncio
async def test_process_message_approval_flow(settings: AppSettings):
    server = MagicMock()
    server.create_session = AsyncMock(return_value=Session(id="sess-2"))
    server.create_run = AsyncMock(
        return_value=Run(run_id="run_appr", status=RunStatus.STARTED)
    )
    server.update_session = AsyncMock(return_value=Session(id="sess-2"))
    server.submit_approval = AsyncMock(return_value={"ok": True})
    server.stop_run = AsyncMock()

    async def fake_stream(run_id: str):
        yield RunEvent(
            type=RunEventType.APPROVAL_REQUIRED,
            data={"tool": "delete_file", "description": "Dosya silinsin mi?"},
        )
        yield RunEvent(
            type=RunEventType.RUN_COMPLETED,
            data={"output": "Tamamlandi."},
        )

    server.stream_events = fake_stream

    orchestrator = AgentOrchestrator(settings, server)
    orchestrator._on_approval_required = AsyncMock(return_value="iptal et")  # noqa: SLF001

    result = await orchestrator.process_message("dosyayi sil", session_id="sess-2")

    assert "Onay gerekli" in result
    server.submit_approval.assert_called_once()
    assert server.submit_approval.call_args.args[0].choice.value == "deny"


@pytest.mark.asyncio
async def test_process_message_issues_session_when_server_cannot_create(settings, tmp_path, monkeypatch):
    from hermes.client.session_store import get_client_id
    from hermes.server.client import HermesServerError
    from hermes.server.models import Session

    state_file = tmp_path / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )
    client_id = get_client_id()

    server = MagicMock()
    server.get_session = AsyncMock(side_effect=HermesServerError("missing", status_code=404))
    server.create_session = AsyncMock(return_value=Session(id=""))
    server.update_session = AsyncMock(return_value=None)
    server.create_run = AsyncMock(return_value=Run(run_id="run_sess", status=RunStatus.STARTED))

    async def fake_stream(run_id: str):
        yield RunEvent(
            type=RunEventType.RUN_COMPLETED,
            data={"output": "hatirladi"},
        )

    server.stream_events = fake_stream

    orchestrator = AgentOrchestrator(settings, server)
    result = await orchestrator.process_message("beni hatirla")

    assert "hatirladi" in result
    issued = server.create_run.call_args.kwargs.get("session_id")
    assert issued
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["client_id"] == client_id
    assert saved["session_id"] == issued


@pytest.mark.asyncio
async def test_initialize_session_keeps_stored_id_when_create_empty(settings, tmp_path, monkeypatch):
    from hermes.client.session_store import get_client_id, save_session_id
    from hermes.server.models import Session

    state_file = tmp_path / "hermes-state" / "client.json"
    client_id = get_client_id()
    save_session_id("stored-empty")

    server = MagicMock()
    server.create_session = AsyncMock(return_value=Session(id=""))
    server.update_session = AsyncMock(return_value=None)

    orchestrator = AgentOrchestrator(settings, server)
    session_id = await orchestrator.initialize_session()

    assert session_id == "stored-empty"
    created = server.create_session.call_args.args[0]
    assert created.id == "stored-empty"
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["client_id"] == client_id
    assert saved["session_id"] == "stored-empty"


@pytest.mark.asyncio
async def test_initialize_session_keeps_stored_id_when_offline(settings, tmp_path, monkeypatch):
    from hermes.client.session_store import get_client_id, save_session_id
    from hermes.server.client import HermesServerError

    state_file = tmp_path / "hermes-state" / "client.json"
    client_id = get_client_id()
    save_session_id("sess-offline")

    server = MagicMock()
    server.create_session = AsyncMock(
        side_effect=HermesServerError("All connection attempts failed")
    )
    server.update_session = AsyncMock()

    orchestrator = AgentOrchestrator(settings, server)
    session_id = await orchestrator.initialize_session()

    assert session_id == "sess-offline"
    assert orchestrator.state.session_renewed is True
    server.create_session.assert_called_once()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["client_id"] == client_id
    assert saved["session_id"] == "sess-offline"


@pytest.mark.asyncio
async def test_initialize_session_reregisters_stored_id(settings, tmp_path, monkeypatch):
    from hermes.client.session_store import get_client_id, save_session_id
    from hermes.server.models import Session

    state_file = tmp_path / "hermes-state" / "client.json"
    client_id = get_client_id()
    stored = "9d68d8f0-dead-beef-0000-000000000000"
    save_session_id(stored)

    server = MagicMock()
    server.create_session = AsyncMock(return_value=Session(id=stored))
    server.update_session = AsyncMock(return_value=Session(id=stored))

    orchestrator = AgentOrchestrator(settings, server)
    session_id = await orchestrator.initialize_session()

    assert session_id == stored
    assert orchestrator.state.session_renewed is False
    created = server.create_session.call_args.args[0]
    assert created.id == stored
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["client_id"] == client_id
    assert saved["session_id"] == stored


@pytest.mark.asyncio
async def test_initialize_session_renews_invalid_id(settings, tmp_path, monkeypatch):
    from hermes.client.session_store import get_client_id, save_session_id
    from hermes.server.client import HermesServerError

    state_file = tmp_path / "hermes-state" / "client.json"
    client_id = get_client_id()
    save_session_id("bad id")

    server = MagicMock()
    server.create_session = AsyncMock(
        side_effect=HermesServerError("Invalid session ID", status_code=400)
    )
    server.update_session = AsyncMock(return_value=None)

    orchestrator = AgentOrchestrator(settings, server)
    session_id = await orchestrator.initialize_session()

    assert session_id
    assert session_id != "bad id"
    assert orchestrator.state.session_renewed is True
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["client_id"] == client_id
    assert saved["session_id"] == session_id


@pytest.mark.asyncio
async def test_follow_up_message_includes_previous_tool_result(settings):
    server = MagicMock()
    server.update_session = AsyncMock(return_value=None)
    server.create_run = AsyncMock(
        side_effect=[
            Run(run_id="run_1", status=RunStatus.STARTED),
            Run(run_id="run_2", status=RunStatus.STARTED),
            Run(run_id="run_3", status=RunStatus.STARTED),
        ]
    )

    async def fake_stream(run_id: str):
        if run_id == "run_1":
            yield RunEvent(
                type=RunEventType.RUN_COMPLETED,
                data={"output": 'LOCAL_TOOL {"name": "open_url", "arguments": {"url": "https://youtu.be/abc"}}'},
            )
            return
        yield RunEvent(
            type=RunEventType.RUN_COMPLETED,
            data={"output": "Video acildi, ozet bekleniyor."},
        )

    server.stream_events = fake_stream
    orchestrator = AgentOrchestrator(settings, server)
    with patch(
        "hermes.tools.windows.input_backend.open_url",
        return_value={"url": "https://youtu.be/abc", "opened": True},
    ):
        first = await orchestrator.process_message(
            "youtube videosunu ac",
            session_id="sess-mem",
        )
        second = await orchestrator.process_message("ozet cek", session_id="sess-mem")

    assert "YouTube" in first or "acildi" in first.lower()
    assert server.create_run.call_count >= 1
    inputs = [call.args[0] for call in server.create_run.call_args_list]
    assert any("SON_YEREL_ISLEMLER" in text for text in inputs)
    assert any("ozet cek" in text for text in inputs)
    history = server.create_run.call_args_list[-1].kwargs.get("conversation_history") or []
    assert any(item.get("role") == "user" for item in history)
    assert second


@pytest.mark.asyncio
async def test_missing_local_tools_excuse_is_replaced(settings: AppSettings):
    server = MagicMock()
    server.update_session = AsyncMock(return_value=None)
    server.create_run = AsyncMock(return_value=Run(run_id="run_miss", status=RunStatus.STARTED))

    async def fake_stream(run_id: str):
        yield RunEvent(
            type=RunEventType.RUN_COMPLETED,
            data={"output": "I don't have access to local tools on this server."},
        )

    server.stream_events = fake_stream
    orchestrator = AgentOrchestrator(settings, server)
    orchestrator._execute_local_tool = AsyncMock(  # noqa: SLF001
        return_value=MagicMock(success=True, output={"text": "Baslik"}, error=None)
    )
    result = await orchestrator.process_message("ekrani kontrol et", session_id="sess-miss")
    assert "local tool" not in result.lower()
    orchestrator._execute_local_tool.assert_awaited()  # noqa: SLF001
    assert "yazi" in result.lower() or "ekran" in result.lower()
