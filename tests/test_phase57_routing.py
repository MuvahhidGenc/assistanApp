from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.goal_resolution import parse_create_folder_goal, parse_open_folder_goal
from hermes.context.reference_resolver import ReferenceResolver
from hermes.server.models import ToolResultPayload


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


def test_adli_klasor_create_desktop(resolver, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: desktop / name,
    )

    message = "Masaustune Deneme2026 adli bir klasor olustur."
    goal = parse_create_folder_goal(message)
    assert goal is not None
    assert goal.name == "Deneme2026"
    assert goal.location == desktop

    result = resolver.resolve(message, ConversationalContext())
    assert result.intent is not None
    assert result.intent.request.name == "create_folder"
    assert result.intent.request.arguments["path"].endswith("Deneme2026")
    assert "Hangi isimle" not in (result.clarification or "")


def test_open_named_folder_overrides_hermes_context(resolver, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    deneme = desktop / "Deneme2026"
    hermes = desktop / "Hermes"
    desktop.mkdir()
    deneme.mkdir()
    hermes.mkdir()
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )

    ctx = ConversationalContext(active_folder=str(hermes), last_created_folder=str(hermes))
    result = resolver.resolve("Deneme2026 klasorunu ac.", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    opened = Path(result.intent.request.arguments["path"]).resolve()
    assert opened == deneme.resolve()
    assert opened != hermes.resolve()


def test_adinda_klasor_masaustunde_not_desktop_name(resolver, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )

    message = "deneme adinda bir klasor olustur masaustunde"
    goal = parse_create_folder_goal(message)
    assert goal is not None
    assert goal.name == "deneme"
    assert goal.name.casefold() != "desktop"
    path = goal.location / goal.name
    assert path == desktop / "deneme"


def test_adina_proje_koy(resolver, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )

    message = "Masaustunde yeni bir klasor olustur, adina Proje koy."
    result = resolver.resolve(message, ConversationalContext())
    assert result.intent.request.name == "create_folder"
    assert result.intent.request.arguments["path"].endswith("Proje")


def test_icine_empty_file_create(resolver, tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder), last_created_folder=str(folder))
    result = resolver.resolve("icine test.txt olustur", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "create_file"
    assert result.intent.request.arguments["path"].endswith("test.txt")
    assert not result.ambiguous


def test_icine_file_with_content(resolver, tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder), last_created_folder=str(folder))
    result = resolver.resolve("icine test.txt olustur ve icine Merhaba yaz", ctx)
    assert result.intent.request.arguments["content"] == "Merhaba"
    assert "Merhaba" not in Path(result.intent.request.arguments["path"]).name


def test_open_deneme_not_hermes(resolver, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    deneme = desktop / "Deneme"
    hermes = desktop / "Hermes"
    desktop.mkdir()
    deneme.mkdir()
    hermes.mkdir()
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )

    ctx = ConversationalContext(active_folder=str(hermes))
    open_goal = parse_open_folder_goal("Deneme klasorunu ac")
    assert open_goal is not None
    assert open_goal.path.resolve() == deneme.resolve()

    result = resolver.resolve("Deneme klasorunu ac", ctx)
    assert Path(result.intent.request.arguments["path"]).resolve() == deneme.resolve()


def test_create_folder_updates_context():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "create_folder",
        {"path": r"C:\Users\test\Desktop\Deneme2026", "verified": True},
        success=True,
    )
    assert ctx.active_folder.endswith("Deneme2026")
    assert ctx.last_created_folder.endswith("Deneme2026")


def test_write_file_updates_active_file():
    ctx = ConversationalContext(active_folder=r"C:\Users\test\Desktop\Deneme2026")
    ctx.update_from_tool(
        "write_file",
        {"path": r"C:\Users\test\Desktop\Deneme2026\test.txt", "size": 7, "verified": True},
        success=True,
    )
    assert ctx.active_file.endswith("test.txt")
    assert ctx.last_created_file.endswith("test.txt")
    assert ctx.active_folder.endswith("Deneme2026")


def test_desktop_never_used_as_folder_name():
    goal = parse_create_folder_goal("masaustunde Desktop adinda bir klasor olustur")
    assert goal is None or goal.name.casefold() != "desktop"


def test_explicit_target_beats_active_folder(resolver, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    target = desktop / "Deneme2026"
    hermes = desktop / "Hermes"
    desktop.mkdir()
    target.mkdir()
    hermes.mkdir()
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )

    ctx = ConversationalContext(active_folder=str(hermes))
    result = resolver.resolve("Deneme2026 klasorunu ac", ctx)
    assert Path(result.intent.request.arguments["path"]).resolve() == target.resolve()


@pytest.mark.asyncio
async def test_orchestrator_no_success_without_tool(settings: AppSettings, monkeypatch):
    server = MagicMock()
    server.create_run = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)
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

    text = await orchestrator.process_message("Masaustune Deneme2026 adli bir klasor olustur.")
    assert orchestrator.state.metadata.get("local_tool_executed_this_turn") is True
    assert "Hangi isimle" not in text


@pytest.mark.asyncio
async def test_create_folder_context_after_execution(settings: AppSettings, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    target = desktop / "Deneme2026"
    ctx = ConversationalContext(active_folder=str(tmp_path / "Hermes"))

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)

    async def fake_pc_process(self, command, run_id="", skip_approval=False, *, user_message="", verify=True):
        if command["name"] == "create_folder":
            Path(str(command["arguments"]["path"])).mkdir(parents=True, exist_ok=True)
            return ToolResultPayload(
                tool_call_id="1",
                success=True,
                output={"path": str(command["arguments"]["path"]), "verified": True},
            )
        return ToolResultPayload(tool_call_id="x", success=False, error="unexpected")

    monkeypatch.setattr("hermes.tools.pc_manager.PCManager.process", fake_pc_process)
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )

    saved: list[ConversationalContext] = []

    def capture_save(self):
        saved.append(
            ConversationalContext(
                active_folder=self.active_folder,
                last_created_folder=self.last_created_folder,
            )
        )

    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.save",
        capture_save,
    )
    monkeypatch.setattr(
        "hermes.client.session_store.append_conversation_turn",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: desktop,
    )

    await orchestrator.process_message("Masaustune Deneme2026 adli bir klasor olustur.")
    assert ctx.active_folder is not None
    assert "Deneme2026" in ctx.active_folder
    assert target.is_dir()
