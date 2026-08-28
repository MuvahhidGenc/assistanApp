from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.orchestrator import AgentOrchestrator, AgentPhase
from hermes.config.settings import AppSettings
from hermes.server.models import Run, RunEvent, RunEventType, RunStatus, Session


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings.load()


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
        "Bilgisayar sistem bilgimi goster",
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

    assert server.create_run.call_count == 3
    first_tool = server.create_run.call_args_list[1].args[0]
    second_tool = server.create_run.call_args_list[2].args[0]
    assert "open_app" in first_tool or "chrome" in first_tool
    assert "open_url" in second_tool or "google.com" in second_tool
    assert "Chrome" in result or "google" in result.lower()


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
