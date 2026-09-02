from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.local_intent import LocalIntent, summarize_local_result
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver, _normalize_existing_path
from hermes.mission.write_content import (
    extract_literal_write_content,
    is_placeholder_write_content,
    plan_composite_file_sequence,
    resolve_write_file_content,
)
from hermes.server.models import ToolResultPayload
from hermes.tools.manifest import LocalToolRequest

HERMES_CONTEXT_GOAL = (
    "Masaüstünde HermesContextTest klasörü oluştur. İçine test.txt dosyası oluştur "
    "ve içine Merhaba Dünya yaz."
)
HERMES_TEST2_GOAL = (
    "Masaüstünde HermesTest2 klasörü oluştur. İçine test.txt dosyası oluştur "
    "ve dosyanın içine TAM OLARAK `123456789` yaz. Başka hiçbir şey yazma."
)


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


def test_natural_language_content_extraction_merhaba_dunya():
    hint = extract_literal_write_content(HERMES_CONTEXT_GOAL)
    assert hint.literal_content == "Merhaba Dünya"
    assert hint.literal_source == "natural_inline"


def test_exact_literal_content_123456789():
    assert resolve_write_file_content(HERMES_TEST2_GOAL) == "123456789"


def test_meta_instruction_excluded_from_content():
    content = resolve_write_file_content(HERMES_TEST2_GOAL)
    assert "Başka hiçbir şey yazma" not in content
    assert "başka hiçbir şey yazma" not in (content or "").casefold()


def test_placeholder_content_forbidden_helper():
    assert is_placeholder_write_content("HERMES tarafından oluşturuldu.") is True
    assert is_placeholder_write_content("Merhaba Dünya") is False


def test_unresolved_content_returns_none_not_placeholder():
    assert resolve_write_file_content("Bir dosya olustur.") is None


def test_composite_plan_uses_merhaba_dunya_not_placeholder():
    steps = plan_composite_file_sequence(HERMES_CONTEXT_GOAL)
    assert len(steps) >= 1
    write = steps[-1]
    assert write.request.name == "write_file"
    assert write.request.arguments["path"].endswith("HermesContextTest\\test.txt")
    assert write.request.arguments["content"] == "Merhaba Dünya"
    assert not is_placeholder_write_content(write.request.arguments["content"])


def test_relative_open_resolves_active_file(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(active_file=str(file_path))
    result = ReferenceResolver().resolve("dosyayı aç", ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    assert result.intent.request.arguments["path"] == str(file_path.resolve())


def test_open_path_intent_requires_existing_file():
    ctx = ConversationalContext(active_file=r"C:\missing\file.txt")
    result = ReferenceResolver().resolve("dosyayi ac", ctx)
    assert result.ambiguous
    assert result.intent is None


def test_open_success_message_requires_verified_flag():
    intent = LocalIntent(
        LocalToolRequest("open_path", {"path": "x.txt"}),
        summary="Acilacak",
    )
    failed = ToolResultPayload(tool_call_id="1", success=False, error="Yol bulunamadi")
    text = summarize_local_result(intent, failed)
    assert "Acilamadi" in text or "Islem yapilamadi" in text
    assert "Acildi" not in text

    unverified = ToolResultPayload(
        tool_call_id="2",
        success=True,
        output={"path": "x.txt", "opened": True, "verified": False},
    )
    text2 = summarize_local_result(intent, unverified)
    assert "dogrulayamadim" in text2.casefold()


def test_last_created_file_open_resolution(tmp_path):
    first = tmp_path / "bilgisayar.txt"
    second = tmp_path / "ikinci.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(second),
        last_created_file=str(second),
        recent_files=[str(first), str(second)],
    )
    result = ReferenceResolver().resolve("Son olusturdugumuz dosyayi ac.", ctx)
    assert result.intent.request.arguments["path"] == str(second.resolve())


def test_same_folder_second_file_content():
    ctx = ConversationalContext(active_folder=r"C:\Users\test\Desktop\HermesContextTest")
    result = ReferenceResolver().resolve(
        "Ayni klasore ikinci.txt olustur ve icine 12345 yaz.", ctx
    )
    assert "ikinci.txt" in result.intent.request.arguments["path"]
    assert result.intent.request.arguments["content"] == "12345"


def test_second_file_becomes_active_file_on_write():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "write_file",
        {"path": r"C:\Users\test\Desktop\HermesContextTest\ikinci.txt", "size": 5, "verified": True},
        success=True,
    )
    assert ctx.active_file.endswith("ikinci.txt")
    assert ctx.last_created_file.endswith("ikinci.txt")


def test_relative_content_update_targets_active_file(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("old", encoding="utf-8")
    ctx = ConversationalContext(active_file=str(file_path))
    result = ReferenceResolver().resolve("İçeriğini sadece Afferin Dostum yap.", ctx)
    assert result.intent.request.arguments["path"] == str(file_path.resolve())
    assert result.intent.request.arguments["content"] == "Afferin Dostum"


def test_deleted_active_file_does_not_resolve(tmp_path):
    missing = tmp_path / "gone.txt"
    ctx = ConversationalContext(active_file=str(missing))
    assert _normalize_existing_path(str(missing)) is None
    result = ReferenceResolver().resolve("İçeriğini TEST yap.", ctx)
    assert result.ambiguous
    assert result.intent is None


@pytest.mark.asyncio
async def test_orchestrator_executes_open_path_tool(settings: AppSettings, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("hello", encoding="utf-8")
    server = MagicMock()
    server.create_run = AsyncMock()
    orchestrator = AgentOrchestrator(settings, server)
    calls: list[str] = []

    async def fake_execute(local_call, run_id="", skip_approval=False, *, user_message=""):
        calls.append(local_call.name)
        if local_call.name == "open_path":
            return ToolResultPayload(
                tool_call_id="1",
                success=True,
                output={"path": str(file_path), "opened": True, "verified": False},
            )
        if local_call.name == "list_windows":
            return ToolResultPayload(
                tool_call_id="2",
                success=True,
                output={"windows": [f"Notepad - {file_path.name}"]},
            )
        return ToolResultPayload(tool_call_id="x", success=False, error="unknown")

    orchestrator._execute_local_tool = AsyncMock(side_effect=fake_execute)  # noqa: SLF001

    ctx = ConversationalContext(active_file=str(file_path))
    ctx.save = MagicMock()
    from hermes.context import conversational_context as conv_mod

    original_load = conv_mod.ConversationalContext.load
    conv_mod.ConversationalContext.load = lambda: ctx

    try:
        result = await orchestrator.process_message("dosyayı aç")
    finally:
        conv_mod.ConversationalContext.load = original_load

    assert "open_path" in calls
    server.create_run.assert_not_called()
    assert "Dosyayi actim" in result or "actim" in result.casefold()


@pytest.mark.asyncio
async def test_orchestrator_blocks_placeholder_composite_write(settings: AppSettings, monkeypatch):
    server = MagicMock()
    server.create_run = AsyncMock()
    orchestrator = AgentOrchestrator(settings, server)
    orchestrator._execute_local_tool = AsyncMock()  # noqa: SLF001

    monkeypatch.setattr(
        "hermes.mission.write_content.plan_composite_file_sequence",
        lambda message: [],
    )
    monkeypatch.setattr(
        "hermes.mission.write_content.is_composite_file_mission",
        lambda message: True,
    )

    result = await orchestrator.process_message(HERMES_CONTEXT_GOAL)
    orchestrator._execute_local_tool.assert_not_called()
    assert "anlayamadim" in result.casefold()
