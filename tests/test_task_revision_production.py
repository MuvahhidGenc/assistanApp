"""Production-path: current objective + revision, not a new copy mission."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.goal_parser import parse_goal
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.intent.models import AgentIntent
from hermes.intent.turn_relation import TurnKind, classify_turn_relation
from hermes.mission.models import MissionStatus
from hermes.mission.planner import build_file_operations_plan
from hermes.mission.store import MissionStore
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


def _patch_session(monkeypatch, ctx: ConversationalContext) -> None:
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


def _orchestrator(settings: AppSettings) -> AgentOrchestrator:
    return AgentOrchestrator(settings, MagicMock())


def _write_docx(path: Path, content: str) -> None:
    from docx import Document

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph(content)
    doc.save(str(path))


def _tracking_execute(written: list[str]):
    async def fake_execute(local_call, run_id, *, user_message=""):
        args = getattr(local_call, "arguments", None) or {}
        raw = str(args.get("path") or "")
        path = Path(raw)
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = str(args.get("content") or "TEVHID MAKALE")
            if path.suffix.casefold() == ".docx":
                _write_docx(path, content)
            else:
                path.write_text(content, encoding="utf-8")
            written.append(str(path.resolve()))
        elif raw:
            path.mkdir(parents=True, exist_ok=True)
            written.append(str(path.resolve()))
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"path": str(path.resolve()) if raw else "", "verified": True},
        )

    return fake_execute


def test_bare_pdf_is_not_a_copy_search_plan():
    registry = create_default_registry()
    steps = build_file_operations_plan("Hayir, Word degil PDF olsun.", registry)
    assert steps == []


def test_search_only_downloads_pdf_still_plans():
    registry = create_default_registry()
    steps = build_file_operations_plan(
        "Indirilenler klasorundeki PDF dosyalarini bul.",
        registry,
    )
    assert steps
    assert steps[0].tool_name == "search_files"


def test_structural_revision_keeps_objective_and_changes_format(tmp_path):
    folder = tmp_path / "test12"
    folder.mkdir()
    source = folder / "tevhid.docx"
    _write_docx(source, "Tevhid makalesi")
    ctx = ConversationalContext(
        current_objective="test12 icine Word dosyasi olustur ve tevhid makalesi yaz",
        last_intent={
            "goal": "test12 icine Word dosyasi olustur ve tevhid makalesi yaz",
            "required_capabilities": ["document.create"],
        },
        task_parameters={
            "action": "create_document",
            "format": "docx",
            "destination": str(folder.resolve()),
            "source_path": str(source.resolve()),
        },
    )
    ctx.commit_focus(
        "file",
        str(source.resolve()),
        container=str(folder.resolve()),
        source="create_word_document",
    )
    parsed = parse_goal("Hayir, Word degil PDF olsun.", ctx)
    relation = classify_turn_relation(
        "Hayir, Word degil PDF olsun.",
        ctx,
        parsed=parsed,
    )
    assert relation.kind is TurnKind.REVISE
    assert relation.parameters.format == "pdf"
    assert Path(relation.parameters.destination).resolve() == folder.resolve()


def test_waiting_continue_vs_new_task_vs_cancel(tmp_path):
    store = MissionStore()
    waiting = store.create_mission("Hangisini acayim?")
    waiting.status = MissionStatus.WAITING_FOR_USER
    store.save(waiting)
    ctx = ConversationalContext(
        current_objective="Hangisini acayim?",
        active_mission_id=waiting.mission_id,
    )
    cont = classify_turn_relation(
        "Ortadakini ac.",
        ctx,
        waiting_mission=waiting,
    )
    assert cont.kind is TurnKind.CONTINUE

    fresh = classify_turn_relation(
        "Bu arada masaustunde yeni klasor olustur.",
        ctx,
        parsed=parse_goal("Bu arada masaustunde yeni klasor olustur.", ctx),
        waiting_mission=waiting,
    )
    assert fresh.kind is TurnKind.NEW_TASK

    cancel = classify_turn_relation("iptal et", ctx, waiting_mission=waiting)
    assert cancel.kind is TurnKind.CANCEL


def test_llm_relation_wins_for_excel_without_new_regex(tmp_path):
    folder = tmp_path / "test12"
    folder.mkdir()
    ctx = ConversationalContext(
        current_objective="icine Word dosyasi olustur",
        last_intent={"goal": "icine Word dosyasi olustur", "required_capabilities": ["document.create"]},
        task_parameters={"action": "create_document", "destination": str(folder)},
    )
    intent = AgentIntent(
        goal="Ayni makaleyi Excel olarak olustur",
        relation="revise",
        revisions={"format": "xlsx"},
        required_capabilities=("document.create",),
        reported_confidence=0.9,
        mode="task",
    )
    relation = classify_turn_relation("Aslinda Excel yap.", ctx, intent=intent)
    assert relation.kind is TurnKind.REVISE
    assert relation.parameters.format == "xlsx"


@pytest.mark.asyncio
async def test_word_to_pdf_revision_uses_same_folder_and_content(
    settings: AppSettings, tmp_path, monkeypatch
):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    stale = desktop / "Deneme2026"
    stale.mkdir()
    old = stale / "test (2).txt"
    old.write_text("eski", encoding="utf-8")
    raporlar = desktop / "Raporlar"
    ctx = ConversationalContext(
        active_file=str(old.resolve()),
        last_created_file=str(old.resolve()),
        recent_files=[str(old.resolve())],
        active_folder=str(stale.resolve()),
    )
    ctx.commit_focus("file", str(old.resolve()), container=str(stale.resolve()), source="stale")
    _patch_session(monkeypatch, ctx)
    written: list[str] = []
    orch = _orchestrator(settings)
    orch._execute_local_tool = _tracking_execute(written)  # type: ignore[method-assign]
    orch._handle_with_intent = AsyncMock(return_value=None)  # noqa: SLF001

    folder = tmp_path / "test12"
    text1 = await orch.process_message(f"Masaustune {folder.name} adli klasor olustur")
    assert folder.name.lower() in text1.casefold() or any(
        Path(item).name == folder.name for item in written
    )
    # The real create_folder tool resolves to the user's Desktop; force the
    # production focus onto the isolated folder used by the rest of the test.
    folder.mkdir(exist_ok=True)
    ctx.commit_focus("folder", str(folder.resolve()), container=str(folder.resolve()), source="create_folder")
    ctx.last_created_folder = str(folder.resolve())
    ctx.revise_task_parameters({"destination": str(folder.resolve())})

    await orch.process_message("Icine Word dosyasi olusturup tevhid ile ilgili makale yaz")
    docx_paths = [Path(item) for item in written if item.lower().endswith(".docx")]
    assert docx_paths
    assert docx_paths[-1].parent.resolve() == folder.resolve()
    assert ctx.current_objective
    assert "Hayir" not in (ctx.current_objective or "")

    before_pdf = list(written)
    text3 = await orch.process_message("Hayir, Word degil PDF olsun.")
    pdf_paths = [Path(item) for item in written if item.lower().endswith(".pdf")]
    assert pdf_paths, text3
    pdf = pdf_paths[-1]
    assert pdf.parent.resolve() == folder.resolve()
    assert pdf.read_text(encoding="utf-8")
    assert "search_files" not in text3.casefold()
    assert "Raporlar" not in text3
    assert not raporlar.exists()
    assert old.read_text(encoding="utf-8") == "eski"
    assert str(old.resolve()) not in written[len(before_pdf) :]
    assert ctx.task_parameters.get("format") == "pdf"
    assert Path(ctx.task_parameters.get("destination") or "").resolve() == folder.resolve()
    assert ctx.focus_container() == str(folder.resolve())
    assert "Hayir" not in (ctx.current_objective or "")
    assert "PDF olsun" not in (ctx.current_objective or "")


@pytest.mark.asyncio
async def test_waiting_continue_resumes_same_mission(
    settings: AppSettings, monkeypatch
):
    ctx = ConversationalContext(current_objective="Hangisini acayim?")
    store = MissionStore()
    waiting = store.create_mission("Hangisini acayim?")
    waiting.status = MissionStatus.WAITING_FOR_USER
    store.save(waiting)
    ctx.active_mission_id = waiting.mission_id
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._mission_store = store
    orch._continue_mission = AsyncMock(return_value="ortadaki acildi")  # noqa: SLF001
    orch._handle_with_intent = AsyncMock(return_value=None)  # noqa: SLF001

    text = await orch.process_message("Ortadakini ac.")
    orch._continue_mission.assert_awaited_once()  # noqa: SLF001
    assert waiting.mission_id in str(orch._continue_mission.await_args)  # noqa: SLF001
    assert "ortadaki acildi" in text
    assert orch._handle_with_intent.await_count == 0  # noqa: SLF001


@pytest.mark.asyncio
async def test_create_inside_folder_is_new_task_not_format_revision(
    settings: AppSettings, tmp_path, monkeypatch
):
    folder = tmp_path / "HermesPhase6"
    folder.mkdir()
    ctx = ConversationalContext(
        current_objective="Masaustune HermesPhase6 klasoru olustur",
        last_created_folder=str(folder.resolve()),
        task_parameters={"action": "create_folder", "destination": str(folder.resolve())},
    )
    ctx.commit_focus(
        "folder",
        str(folder.resolve()),
        container=str(folder.resolve()),
        source="create_folder",
    )
    _patch_session(monkeypatch, ctx)
    written: list[str] = []
    orch = _orchestrator(settings)
    orch._execute_local_tool = _tracking_execute(written)  # type: ignore[method-assign]
    orch._handle_with_intent = AsyncMock(return_value=None)  # noqa: SLF001

    parsed = parse_goal("Icine notlar.txt olustur, Phase 6 test yaz.", ctx)
    relation = classify_turn_relation(
        "Icine notlar.txt olustur, Phase 6 test yaz.",
        ctx,
        parsed=parsed,
    )
    assert relation.kind is TurnKind.NEW_TASK

    text = await orch.process_message("Icine notlar.txt olustur, Phase 6 test yaz.")
    created = [Path(item) for item in written if item.lower().endswith(".txt")]
    assert created, text
    assert created[-1].parent.resolve() == folder.resolve()
    assert created[-1].name == "notlar.txt"


@pytest.mark.asyncio
async def test_waiting_new_task_does_not_bind_to_old_choice(
    settings: AppSettings, tmp_path, monkeypatch
):
    ctx = ConversationalContext(current_objective="Hangisini acayim?")
    store = MissionStore()
    waiting = store.create_mission("Hangisini acayim?")
    waiting.status = MissionStatus.WAITING_FOR_USER
    store.save(waiting)
    ctx.active_mission_id = waiting.mission_id
    _patch_session(monkeypatch, ctx)
    written: list[str] = []
    orch = _orchestrator(settings)
    orch._mission_store = store
    orch._execute_local_tool = _tracking_execute(written)  # type: ignore[method-assign]
    orch._continue_mission = AsyncMock(return_value="eski goreve devam")  # noqa: SLF001
    orch._handle_with_intent = AsyncMock(return_value=None)  # noqa: SLF001

    text = await orch.process_message("Masaustune YeniBekleme adli klasor olustur")
    orch._continue_mission.assert_not_awaited()  # noqa: SLF001
    leftover = store.load(waiting.mission_id)
    assert leftover is None or leftover.status != MissionStatus.WAITING_FOR_USER
    assert "eski goreve devam" not in text


@pytest.mark.asyncio
async def test_waiting_cancel_clears_objective(settings: AppSettings, monkeypatch):
    ctx = ConversationalContext(current_objective="Word dosyasi olustur")
    store = MissionStore()
    waiting = store.create_mission("Word dosyasi olustur")
    waiting.status = MissionStatus.WAITING_FOR_USER
    store.save(waiting)
    ctx.active_mission_id = waiting.mission_id
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._mission_store = store
    text = await orch.process_message("iptal et")
    assert "iptal" in text.casefold()
    assert ctx.current_objective is None
    loaded = store.load(waiting.mission_id)
    assert loaded is None or loaded.status == MissionStatus.CANCELLED


@pytest.mark.asyncio
async def test_file_focus_icine_then_format_revision(
    settings: AppSettings, tmp_path, monkeypatch
):
    folder = tmp_path / "test12"
    folder.mkdir()
    existing = folder / "not.txt"
    existing.write_text("x", encoding="utf-8")
    stale = tmp_path / "Deneme2026" / "test (2).txt"
    stale.parent.mkdir()
    stale.write_text("eski", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(existing.resolve()),
        last_created_file=str(stale.resolve()),
        recent_files=[str(stale.resolve()), str(existing.resolve())],
    )
    ctx.commit_focus(
        "file",
        str(existing.resolve()),
        container=str(folder.resolve()),
        source="write_file",
    )
    ctx.current_objective = "Icine Word dosyasi olusturup tevhid makalesi yaz"
    ctx.last_intent = {
        "goal": ctx.current_objective,
        "required_capabilities": ["document.create"],
    }
    ctx.revise_task_parameters(
        {
            "action": "create_document",
            "format": "docx",
            "destination": str(folder.resolve()),
            "source_path": str(existing.resolve()),
            "content": "Tevhid makalesi",
        }
    )
    _patch_session(monkeypatch, ctx)
    written: list[str] = []
    orch = _orchestrator(settings)
    orch._execute_local_tool = _tracking_execute(written)  # type: ignore[method-assign]
    orch._handle_with_intent = AsyncMock(return_value=None)  # noqa: SLF001

    text = await orch.process_message("Fikrimi degistirdim, PDF olsun.")
    pdfs = [Path(item) for item in written if item.lower().endswith(".pdf")]
    assert pdfs, text
    assert pdfs[-1].parent.resolve() == folder.resolve()
    assert "Tevhid" in pdfs[-1].read_text(encoding="utf-8")
    assert str(stale.resolve()) not in written
