from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.file_intent import (
    FileIntentKind,
    classify_file_intent,
    is_empty_file_create_message,
    resolve_file_create_target,
)
from hermes.context.reference_resolver import ReferenceResolver
from hermes.server.models import ToolResultPayload


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


def test_icine_test_txt_is_empty_create():
    message = "Icine test.txt olustur"
    assert is_empty_file_create_message(message) is True
    assert classify_file_intent(message) == FileIntentKind.CREATE_FILE


def test_icine_test_txt_with_content_is_write():
    message = "Icine test.txt olustur ve icine Merhaba yaz"
    assert is_empty_file_create_message(message) is False
    assert classify_file_intent(message) == FileIntentKind.WRITE_FILE


def test_resolver_empty_create_under_active_folder(resolver, tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder), last_created_folder=str(folder))
    result = resolver.resolve("Icine test.txt olustur", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "create_file"
    assert result.intent.request.arguments["path"].endswith("test.txt")
    assert "content" not in result.intent.request.arguments


def test_resolver_write_with_content(resolver, tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder))
    result = resolver.resolve("Icine ikinci.txt olustur ve icine 12345 yaz", ctx)
    assert result.intent.request.name == "write_file"
    assert result.intent.request.arguments["content"] == "12345"


def test_resolve_file_create_no_desktop_fallback(tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    target = resolve_file_create_target(
        "Icine test.txt olustur",
        active_folder=str(folder),
        last_created_folder=str(folder),
    )
    assert target is not None
    assert Path(target.path).parent.resolve() == folder.resolve()


def test_context_updates_after_create_file():
    ctx = ConversationalContext(active_folder=r"C:\Users\test\Desktop\Deneme2026")
    ctx.update_from_tool(
        "create_file",
        {"path": r"C:\Users\test\Desktop\Deneme2026\test.txt", "verified": True},
        success=True,
    )
    assert ctx.active_file.endswith("test.txt")
    assert ctx.last_created_file.endswith("test.txt")


def test_open_last_created_file(resolver, tmp_path):
    first = tmp_path / "a.txt"
    second = tmp_path / "test.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(first),
        last_created_file=str(second),
        recent_files=[str(first), str(second)],
    )
    result = resolver.resolve("Son olusturdugun dosyayi ac", ctx)
    assert result.intent is not None
    assert Path(result.intent.request.arguments["path"]).name == "test.txt"


def test_stale_a_txt_not_used_for_last_created(resolver, tmp_path):
    hermes = tmp_path / "Hermes"
    hermes.mkdir()
    stale = hermes / "a.txt"
    fresh = tmp_path / "Deneme2026" / "test.txt"
    fresh.parent.mkdir()
    stale.write_text("old", encoding="utf-8")
    fresh.write_text("", encoding="utf-8")
    ctx = ConversationalContext(
        active_folder=str(hermes),
        active_file=str(stale),
        last_created_file=str(fresh),
    )
    result = resolver.resolve("Son olusturdugun dosyayi ac.", ctx)
    assert Path(result.intent.request.arguments["path"]).resolve() == fresh.resolve()


def test_context_persistence_roundtrip(tmp_path, monkeypatch):
    state_file = tmp_path / "client.json"
    monkeypatch.setattr(
        "hermes.context.conversational_context.load_client_state",
        lambda: {},
    )
    saved: dict = {}

    def _save(state):
        saved.update(state)

    monkeypatch.setattr(
        "hermes.context.conversational_context.save_client_state",
        _save,
    )

    ctx = ConversationalContext()
    ctx.update_from_tool(
        "create_file",
        {"path": str(tmp_path / "Deneme2026" / "test.txt"), "verified": True},
        success=True,
    )
    ctx.save()
    loaded = ConversationalContext.from_dict(saved["conversational_context"])
    assert loaded.last_created_file.endswith("test.txt")


@pytest.mark.asyncio
async def test_orchestrator_empty_create_executes(settings: AppSettings, tmp_path, monkeypatch):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder), last_created_folder=str(folder))

    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)
    calls: list[str] = []

    async def fake_pc_process(self, command, run_id="", skip_approval=False, *, user_message=""):
        calls.append(command["name"])
        target = Path(str(command["arguments"]["path"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(command["arguments"].get("content") or ""), encoding="utf-8")
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"path": str(target), "size": target.stat().st_size},
        )

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

    text = await orchestrator.process_message("Icine test.txt olustur")

    assert "write_file" in calls
    assert "Dosyaya yazilacak icerigi anlayamadim" not in text
    assert (folder / "test.txt").is_file()
    assert ctx.last_created_file.endswith("test.txt")
    assert orchestrator.state.metadata.get("local_tool_executed_this_turn") is True


def test_single_and_multi_step_share_resolver(resolver, tmp_path):
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder))
    single = resolver.resolve("Icine test.txt olustur", ctx)
    multi = resolver.resolve("Icine ikinci.txt olustur ve icine 12345 yaz", ctx)
    assert single.intent.request.name == "create_file"
    assert multi.intent.request.name == "write_file"
    assert Path(single.intent.request.arguments["path"]).parent == Path(
        multi.intent.request.arguments["path"]
    ).parent


def test_content_modify_test_word_does_not_match_test_txt_stem(resolver, tmp_path):
    """Step 7: 'TEST' content must not resolve to test.txt when ikinci.txt is active."""
    folder = tmp_path / "Deneme2026"
    folder.mkdir()
    test_file = folder / "test.txt"
    ikinci_file = folder / "ikinci.txt"
    test_file.write_text("", encoding="utf-8")
    ikinci_file.write_text("12345", encoding="utf-8")
    ctx = ConversationalContext(
        active_folder=str(folder),
        active_file=str(ikinci_file),
        last_created_file=str(ikinci_file),
        last_modified_file=str(ikinci_file),
        recent_files=[str(ikinci_file), str(test_file)],
    )
    result = resolver.resolve("Icerigini sadece TEST yap.", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "write_file"
    assert Path(result.intent.request.arguments["path"]).resolve() == ikinci_file.resolve()
    assert result.intent.request.arguments["content"] == "TEST"
