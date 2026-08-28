import pytest
from rich.console import Console

from hermes.agent.orchestrator import AgentPhase
from hermes.cli.session import CliChatSession


@pytest.mark.asyncio
async def test_cli_session_shows_readable_status_only():
    console = Console(record=True)
    session = CliChatSession(console, debug=False)

    await session.on_status(AgentPhase.UNDERSTANDING, "İstek anlaşılıyor...", {})
    await session.on_status(AgentPhase.COMPLETED, "Görev tamamlandı.", {})
    await session.on_status(AgentPhase.COMPLETED, "Görev tamamlandı.", {})

    output = console.export_text()
    assert "İstek anlaşılıyor..." in output
    assert "Görev tamamlandı." in output
    assert output.count("Görev tamamlandı.") == 1
    assert "{" not in output
    assert "understanding" not in output


@pytest.mark.asyncio
async def test_cli_session_debug_shows_json():
    console = Console(record=True)
    session = CliChatSession(console, debug=True)

    await session.on_status(AgentPhase.EXECUTING, "Tool çalışıyor...", {"tool": "ping"})

    output = console.export_text()
    assert "Tool çalışıyor..." in output
    assert "ping" in output
