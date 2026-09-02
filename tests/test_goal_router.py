from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.goal_router import GoalRouter
from hermes.agent.orchestrator import AgentOrchestrator, AgentPhase
from hermes.agent.tool_intent import ToolIntentMatcher, match_tool_intent
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.context.system_paths import resolve_known_folder
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


def test_resolve_downloads_folder():
    path = resolve_known_folder("Indirilenler klasorune gir")
    assert path is not None
    assert path.name == "Downloads"


def test_tool_intent_chrome_open(registry):
    ctx = ConversationalContext()
    result = match_tool_intent("Chrome'u ac", ctx, registry=registry)
    assert result.intent is not None
    assert result.intent.request.name == "open_app"
    assert result.intent.request.arguments["app"] == "chrome"


def test_tool_intent_chrome_variants(registry):
    for message in ("Chrome'u aç", "Bilgisayarda Chrome'u aç", "Chrome ac"):
        result = match_tool_intent(message, ConversationalContext(), registry=registry)
        assert result.intent is not None, message
        assert result.intent.request.name == "open_app", message


def test_tool_intent_list_folder_with_context(registry, tmp_path):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder))
    result = match_tool_intent(
        "Bu klasordeki dosyalari listele",
        ctx,
        registry=registry,
    )
    assert result.intent is not None
    assert result.intent.request.name == "list_directory"
    assert Path(result.intent.request.arguments["path"]).resolve() == folder.resolve()


def test_tool_intent_rename_requires_new_name(registry, tmp_path):
    file_path = tmp_path / "a.txt"
    file_path.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(active_file=str(file_path))
    result = match_tool_intent("Bu dosyanin adini degistir", ctx, registry=registry)
    assert result.ambiguous is True
    assert "yeni ad" in result.clarification.casefold()


def test_reference_open_last_created_file(resolver, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("hello", encoding="utf-8")
    ctx = ConversationalContext(last_created_file=str(file_path))
    result = resolver.resolve("Son olusturdugumuz dosyayi ac", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    assert result.intent.request.arguments["path"] == str(file_path.resolve())


def test_reference_list_in_context_folder(resolver, tmp_path):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder))
    result = resolver.resolve("Bu klasordeki dosyalari goster", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "list_directory"


@pytest.mark.asyncio
async def test_orchestrator_chrome_open_no_server(registry, settings: AppSettings, monkeypatch):
    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server, registry=registry)
    status_messages: list[str] = []

    async def capture_status(phase, message, extra=None):
        status_messages.append(message)

    orchestrator._on_status = capture_status

    async def fake_pc_process(self, command, run_id="", skip_approval=False, *, user_message=""):
        assert command["name"] == "open_app"
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"app": command["arguments"]["app"], "started": True},
        )

    monkeypatch.setattr(
        "hermes.tools.pc_manager.PCManager.process",
        fake_pc_process,
    )
    ctx = ConversationalContext()
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.save",
        lambda self: None,
    )
    monkeypatch.setattr(
        "hermes.client.session_store.append_conversation_turn",
        lambda *a, **k: None,
    )

    text = await orchestrator.process_message("Chrome'u ac")

    server.create_run.assert_not_called()
    assert "Chrome" in text
    assert "open_path" not in " ".join(status_messages).casefold()
    assert not any("Yerel tool" in msg for msg in status_messages)


@pytest.mark.asyncio
async def test_orchestrator_open_folder_natural_status(settings: AppSettings, tmp_path, monkeypatch):
    folder = tmp_path / "Test"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder), last_created_folder=str(folder))

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)
    status_messages: list[str] = []

    async def capture_status(phase, message, extra=None):
        status_messages.append(message)

    orchestrator._on_status = capture_status

    async def fake_execute(local_call, run_id, *, user_message=""):
        if local_call.name == "open_path":
            return ToolResultPayload(
                tool_call_id="1",
                success=True,
                output={"path": str(folder), "opened": True, "is_directory": True},
            )
        if local_call.name == "list_windows":
            return ToolResultPayload(
                tool_call_id="2",
                success=True,
                output={"windows": ["Test - Explorer"]},
            )
        return ToolResultPayload(tool_call_id="x", success=False, error="unknown")

    orchestrator._execute_local_tool = fake_execute  # type: ignore[method-assign]

    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr("hermes.context.conversational_context.ConversationalContext.save", lambda self: None)
    monkeypatch.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)

    text = await orchestrator.process_message("klasoru ac")

    server.create_run.assert_not_called()
    assert "Klasoru actim" in text or "actim" in text.casefold()
    assert not any("Yerel tool" in msg for msg in status_messages)


def test_goal_router_skips_multi_step(registry):
    router = GoalRouter(registry)
    route = router.route(
        "Klasoru ac ve icindeki dosyayi kontrol et",
        ConversationalContext(),
    )
    assert route.intent is None
    assert route.source in {"multi_step", "unmatched"}


def test_no_fake_success_open_path_message():
    from hermes.agent.local_intent import LocalIntent, LocalToolRequest
    from hermes.agent.user_messages import format_open_path_message

    intent = LocalIntent(LocalToolRequest("open_path", {"path": "x"}), summary="x")
    result = ToolResultPayload(
        tool_call_id="1",
        success=True,
        output={"path": "x", "verified": False},
    )
    text = format_open_path_message(intent, result)
    assert "actim" not in text.casefold()
    assert "dogrulayamadim" in text.casefold()
