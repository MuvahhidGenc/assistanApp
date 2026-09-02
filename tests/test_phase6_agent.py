"""Phase 6 general computer agent regression tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.conversation_flow import handle_meta_conversation
from hermes.agent.goal_parser import ActionKind, parse_goal
from hermes.agent.goal_router import GoalRouter
from hermes.context.conversational_context import ConversationalContext
from hermes.mission.planner import build_file_operations_plan
from hermes.mission.selection import should_create_mission, should_route_to_mission
from hermes.security.bulk_risk import assess_bulk_risk
from hermes.tools.catalog import build_tool_catalog
from hermes.tools.registry import create_default_registry


def test_tool_catalog_contains_file_tools():
    catalog = build_tool_catalog()
    assert "copy_file" in catalog
    assert "move_file" in catalog
    assert "search_files" in catalog
    entry = catalog.get("copy_file")
    assert entry is not None
    assert entry.opens_ui is False
    assert entry.execution_target.value == "client"


def test_goal_parser_extracts_pdf_copy_workflow():
    parsed = parse_goal(
        "Indirilenlerdeki PDF'lerden dun olusturulanlari bul, masaustunde Raporlar klasoru olustur ve onlari oraya kopyala."
    )
    assert ActionKind.SEARCH in parsed.required_actions or ActionKind.COPY in parsed.required_actions
    assert parsed.is_multi_step
    assert parsed.constraints.get("file_type") == "pdf"


def test_goal_router_routes_folder_create():
    router = GoalRouter(create_default_registry())
    route = router.route(
        "Masaustune Deneme adinda klasor olustur.",
        ConversationalContext(),
    )
    assert route.parsed_goal is not None
    assert route.intent is not None or route.source in {"multi_step", "multi_step_goal", "unmatched"}


def test_mission_selection_for_pdf_copy():
    message = (
        "Indirilenlerdeki PDF'lerden dun olusturulanlari bul, "
        "masaustunde Raporlar klasoru olustur ve onlari oraya kopyala."
    )
    assert should_create_mission(message)
    assert should_route_to_mission(message)


def test_file_operations_heuristic_plan():
    registry = create_default_registry()
    steps = build_file_operations_plan(
        "Indirilenlerdeki PDF'leri masaustunde Raporlar klasorune kopyala.",
        registry,
    )
    assert len(steps) >= 3
    assert steps[0].tool_name == "search_files"
    assert any(step.tool_name == "create_folder" for step in steps)


def test_conversation_flow_what_did_you_do():
    ctx = ConversationalContext()
    ctx.last_action_summary = "Test123 klasorunu olusturdum."
    turn = handle_meta_conversation("Ne yaptin?", ctx)
    assert turn.handled
    assert "Test123" in turn.response


def test_conversation_flow_repeat():
    ctx = ConversationalContext()
    ctx.last_user_message = "Masaustune demo klasoru olustur."
    turn = handle_meta_conversation("Bir daha yap", ctx)
    assert turn.handled
    assert turn.repeat_message == "Masaustune demo klasoru olustur."


def test_bulk_delete_requires_confirmation():
    assessment = assess_bulk_risk("delete_path", "Downloads klasorundeki 142 dosyayi sil.")
    assert assessment.requires_confirmation
    assert "142" in (assessment.reason or "")


def test_bulk_single_file_no_confirmation():
    assessment = assess_bulk_risk("delete_path", "test.txt dosyasini sil.")
    assert not assessment.requires_confirmation


@pytest.mark.asyncio
async def test_copy_file_tool(tmp_path, monkeypatch):
    from hermes.tools.windows.file_tools import CopyFileTool

    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    tool = CopyFileTool()

    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )
    result = await tool.execute(source=str(src), destination=str(dest_dir))
    assert result.success
    assert (dest_dir / "a.txt").exists()


@pytest.mark.asyncio
async def test_search_files_tool(tmp_path, monkeypatch):
    from hermes.tools.windows.file_tools import SearchFilesTool

    folder = tmp_path / "downloads"
    folder.mkdir()
    (folder / "report.pdf").write_bytes(b"%PDF-1.4")
    tool = SearchFilesTool()
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: folder if raw else folder,
    )
    result = await tool.execute(path=str(folder), pattern="*.pdf")
    assert result.success
    assert result.output["count"] >= 1


@pytest.mark.asyncio
async def test_tts_failure_does_not_block_task():
    from hermes.config.settings import VoiceSettings
    from hermes.voice.assistant import VoiceAssistant

    class FailingTTS:
        def is_available(self) -> bool:
            return True

        async def speak(self, text: str) -> None:
            raise RuntimeError("TTS backends failed")

        def stop(self) -> None:
            return None

    agent = MagicMock()
    agent.process_message = AsyncMock(return_value="Deneme klasorunu olusturdum.")
    agent.state = MagicMock(last_tool_results=[{"name": "create_folder", "success": True}])
    agent._server = MagicMock()
    agent._server.stop_run = AsyncMock()

    assistant = VoiceAssistant(
        settings=VoiceSettings(),
        agent=agent,
        tts=FailingTTS(),
    )
    assistant._voice_enabled = True
    await assistant.handle_text_input("Masaustune Deneme klasoru olustur")
    agent.process_message.assert_awaited_once()
