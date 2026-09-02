from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.agent.application_catalog import resolve_application
from hermes.agent.goal_router import GoalRouter
from hermes.agent.local_intent import _match_create_folder
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.folder_reference import extract_create_folder_target, resolve_create_folder_path
from hermes.context.reference_resolver import ReferenceResolver
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


CREATE_MSG = "Masaustune yeni bir klasor olustur, adina Deneme2026 koy."


def test_extract_deneme2026_folder_name():
    assert extract_create_folder_target(CREATE_MSG) == "Deneme2026"


def test_resolve_create_folder_path_desktop():
    path = resolve_create_folder_path(CREATE_MSG)
    assert path is not None
    assert path.replace("/", "\\").endswith("Desktop\\Deneme2026")


def test_match_create_folder_not_hermes():
    intent = _match_create_folder(CREATE_MSG, CREATE_MSG.casefold())
    assert intent is not None
    assert intent.request.name == "create_folder"
    assert "Deneme2026" in str(intent.request.arguments["path"])
    assert "Hermes" not in str(intent.request.arguments["path"])


def test_create_folder_ignores_active_hermes_context(resolver, tmp_path, monkeypatch):
    hermes = tmp_path / "Hermes"
    hermes.mkdir()
    ctx = ConversationalContext(
        active_folder=str(hermes),
        last_created_folder=str(hermes),
    )
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: tmp_path / "Desktop" / name,
    )
    desktop = tmp_path / "Desktop"
    desktop.mkdir(exist_ok=True)

    result = resolver.resolve(CREATE_MSG, ctx)
    assert result.intent is not None
    assert result.intent.request.name == "create_folder"
    assert "Deneme2026" in result.intent.request.arguments["path"]
    assert "Hermes" not in result.intent.request.arguments["path"]


def test_chrome_open_resolves_to_open_app(registry):
    assert resolve_application("Chrome'u ac") == "chrome"
    assert resolve_application("Google Chrome'u calistir") == "chrome"


def test_goal_router_chrome(registry):
    route = GoalRouter(registry).route("Chrome'u ac", ConversationalContext())
    assert route.intent is not None
    assert route.intent.request.name == "open_app"
    assert route.intent.request.arguments["app"] == "chrome"


def test_open_folder_vs_create_folder(resolver, tmp_path):
    folder = tmp_path / "Hermes"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder))
    open_result = resolver.resolve("klasoru ac", ctx)
    assert open_result.intent is not None
    assert open_result.intent.request.name == "open_path"

    create_result = resolver.resolve("Masaustunde TestKlasor olustur", ctx)
    assert create_result.intent is None or create_result.intent.request.name == "create_folder"


def test_no_hermes_default_when_name_missing():
    intent = _match_create_folder("klasor olustur", "klasor olustur")
    assert intent is None


@pytest.mark.asyncio
async def test_orchestrator_create_folder_executes_tool(
    settings: AppSettings, tmp_path, monkeypatch
):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    target = desktop / "Deneme2026"

    ctx = ConversationalContext(
        active_folder=str(tmp_path / "Hermes"),
        last_created_folder=str(tmp_path / "Hermes"),
    )

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)
    calls: list[str] = []

    async def fake_pc_process(self, command, run_id="", skip_approval=False, *, user_message=""):
        calls.append(command["name"])
        if command["name"] == "create_folder":
            Path(str(command["arguments"]["path"])).mkdir(parents=True, exist_ok=True)
            from hermes.server.models import ToolResultPayload

            return ToolResultPayload(
                tool_call_id="1",
                success=True,
                output={"path": str(command["arguments"]["path"])},
            )
        from hermes.server.models import ToolResultPayload

        return ToolResultPayload(tool_call_id="x", success=False, error="unexpected")

    monkeypatch.setattr("hermes.tools.pc_manager.PCManager.process", fake_pc_process)
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
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )

    text = await orchestrator.process_message(CREATE_MSG)

    server.create_run.assert_not_called()
    assert "create_folder" in calls
    assert target.is_dir()
    assert "Deneme2026" in text
    assert "Hermes" not in text
    assert orchestrator.state.metadata.get("local_tool_executed_this_turn") is True


def test_fake_success_detection():
    assert AgentOrchestrator._looks_like_fake_pc_success("Klasoru actim.") is True
    assert AgentOrchestrator._looks_like_fake_pc_success("Deneme2026 klasorunu masaustunde olusturdum.") is True
    assert AgentOrchestrator._looks_like_fake_pc_success("Hangi dosyayi acmami istiyorsun?") is False


def test_icine_continuation_file_create(resolver, tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder), last_created_folder=str(folder))
    result = resolver.resolve("Icine test.txt olustur ve icine Merhaba yaz.", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "write_file"
    assert result.intent.request.arguments["content"] == "Merhaba"
    assert result.intent.request.arguments["path"].endswith("test.txt")


def test_named_file_open_in_active_folder(resolver, tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    file_path = folder / "test.txt"
    file_path.write_text("Merhaba", encoding="utf-8")
    ctx = ConversationalContext(active_folder=str(folder), active_file=str(file_path))
    result = resolver.resolve("test.txt dosyasini ac.", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    assert Path(result.intent.request.arguments["path"]).name == "test.txt"
