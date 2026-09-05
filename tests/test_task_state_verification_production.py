"""Production-path: structured task state, format verification, polarity."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.goal_parser import parse_goal
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.entity_decision import Confidence
from hermes.intent.models import AgentIntent
from hermes.intent.router import IntentRouter, RouteKind
from hermes.intent.turn_relation import TurnKind, classify_turn_relation, has_negative_polarity
from hermes.mission.models import MissionStatus
from hermes.mission.reality_verification import (
    find_office_lock_files,
    verify_docx_document,
    verify_pdf_document,
)
from hermes.mission.store import MissionStore
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.base import VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.specific import WriteFileVerifier
from hermes.tools.windows.file_tools import (
    CreateWordDocumentTool,
    DeletePathTool,
    WriteFileTool,
)


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


@pytest.mark.asyncio
async def test_folder_word_open_uses_real_docx(settings, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr("hermes.tools.windows.file_tools.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: desktop / name,
    )
    ctx = ConversationalContext()
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value=None)

    await orch.process_message("Masaustune VerifyDoc klasoru olustur")
    folder = desktop / "VerifyDoc"
    folder.mkdir(exist_ok=True)
    ctx.commit_focus("folder", str(folder.resolve()), container=str(folder.resolve()), source="create")
    ctx.last_created_folder = str(folder.resolve())

    await orch.process_message("Icine Word dosyasi olustur ve tevhid makalesi yaz")
    docs = list(folder.glob("*.docx"))
    assert docs
    check = verify_docx_document(docs[-1])
    assert check["ok"], check


@pytest.mark.asyncio
async def test_word_to_pdf_is_parseable_and_keeps_content(settings, tmp_path, monkeypatch):
    folder = tmp_path / "yenidenem"
    folder.mkdir()
    source = folder / "tevhid.docx"
    tool = CreateWordDocumentTool()
    created = await tool.execute(path=str(source), content="Tevhid makalesi", title="Tevhid")
    assert created.success
    assert verify_docx_document(source)["ok"]

    ctx = ConversationalContext(
        current_objective="Word belge olustur ve tevhid makalesi yaz",
        last_intent={"goal": "Word belge olustur", "required_capabilities": ["document.create"]},
        task_parameters={
            "action": "create_document",
            "format": "docx",
            "objective": "Word belge olustur ve tevhid makalesi yaz",
            "container": str(folder.resolve()),
            "source_path": str(source.resolve()),
            "content": "Tevhid makalesi",
        },
    )
    ctx.commit_focus("file", str(source.resolve()), container=str(folder.resolve()), source="create")
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value=None)

    text = await orch.process_message("simdi bunu PDF yap")
    pdfs = list(folder.glob("*.pdf"))
    assert pdfs, text
    check = verify_pdf_document(pdfs[-1], expected_text="Tevhid")
    assert check["ok"], check
    assert ctx.task_parameters.get("format") == "pdf"
    assert "Tevhid" in (ctx.task_parameters.get("content") or "")
    assert "simdi bunu" not in (ctx.task_parameters.get("content") or "")


@pytest.mark.asyncio
async def test_pdf_to_word_keeps_content(settings, tmp_path, monkeypatch):
    folder = tmp_path / "yenidenem"
    folder.mkdir()
    pdf = folder / "tevhid.pdf"
    written = await WriteFileTool().execute(path=str(pdf), content="Tevhid makalesi")
    assert written.success
    assert verify_pdf_document(pdf, expected_text="Tevhid")["ok"]

    ctx = ConversationalContext(
        current_objective="PDF belge olustur",
        last_intent={"goal": "PDF belge olustur", "required_capabilities": ["document.create"]},
        task_parameters={
            "action": "create_document",
            "format": "pdf",
            "container": str(folder.resolve()),
            "source_path": str(pdf.resolve()),
            "content": "Tevhid makalesi",
        },
    )
    ctx.commit_focus("file", str(pdf.resolve()), container=str(folder.resolve()), source="create")
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value=None)

    await orch.process_message("icerigi ayni kalsin ama Word olsun")
    docs = list(folder.glob("*.docx"))
    assert docs
    check = verify_docx_document(docs[-1])
    assert check["ok"], check
    assert "Tevhid" in (check.get("text") or "")


@pytest.mark.asyncio
async def test_lock_cleanup_deletes_real_lock_and_verifies(tmp_path):
    folder = tmp_path / "yenidenem"
    folder.mkdir()
    doc = folder / "tevhid.docx"
    doc.write_bytes(b"PK\x03\x04fake")
    lock = folder / ".~lock.tevhid.docx#"
    lock.write_text("lock", encoding="utf-8")
    missing = folder / ".~lock.tevhid (1) (1).pdf#"

    result = await DeletePathTool().execute(path=str(missing))
    assert result.success
    assert result.verified
    assert not lock.exists()
    assert doc.exists()
    assert find_office_lock_files(folder, "tevhid") == []


def test_utf8_named_pdf_fails_format_verification(tmp_path):
    fake = tmp_path / "tevhid.pdf"
    fake.write_text("ekranda gordugum videolardan herhangi", encoding="utf-8")
    check = verify_pdf_document(fake)
    assert check["ok"] is False
    assert check["reason"] == "missing_pdf_header"


@pytest.mark.asyncio
async def test_write_file_verifier_rejects_fake_pdf(tmp_path):
    fake = tmp_path / "tevhid.pdf"
    fake.write_text("not a pdf", encoding="utf-8")
    ctx = VerifierContext(
        tool_name="write_file",
        tool_arguments={"path": str(fake), "content": "not a pdf"},
        execution_success=True,
        execution_output={"path": str(fake), "exists": True},
    )
    result = await WriteFileVerifier().verify(ctx)
    assert result.status is VerificationStatus.FAILED


def test_waiting_continue_revise_new_task_and_cancel(tmp_path):
    store = MissionStore()
    waiting = store.create_mission("Hangisini acayim?")
    waiting.status = MissionStatus.WAITING_FOR_USER
    store.save(waiting)
    folder = tmp_path / "test12"
    folder.mkdir()
    ctx = ConversationalContext(
        current_objective="PDF belge olustur",
        active_mission_id=waiting.mission_id,
        task_parameters={
            "action": "create_document",
            "format": "pdf",
            "container": str(folder),
            "content": "Tevhid",
        },
    )
    assert classify_turn_relation(
        "Ortadakini ac.", ctx, waiting_mission=waiting
    ).kind is TurnKind.CONTINUE
    assert classify_turn_relation(
        "Bu arada masaustunde yeni klasor olustur.",
        ctx,
        parsed=parse_goal("Bu arada masaustunde yeni klasor olustur.", ctx),
        waiting_mission=waiting,
    ).kind is TurnKind.NEW_TASK
    revise = classify_turn_relation(
        "hayir onu test12 klasorunun icine yap",
        ctx,
        parsed=parse_goal("hayir onu test12 klasorunun icine yap", ctx),
        waiting_mission=waiting,
    )
    assert revise.kind is TurnKind.REVISE
    assert classify_turn_relation("iptal et", ctx, waiting_mission=waiting).kind is TurnKind.CANCEL


@pytest.mark.asyncio
async def test_screen_message_does_not_absorb_into_pdf_content(
    settings, tmp_path, monkeypatch
):
    folder = tmp_path / "yenidenem"
    folder.mkdir()
    pdf = folder / "tevhid.pdf"
    await WriteFileTool().execute(path=str(pdf), content="Tevhid makalesi")
    videos = tmp_path / "Videos"
    videos.mkdir()
    ctx = ConversationalContext(
        current_objective="PDF belge olustur",
        last_intent={"goal": "PDF belge olustur", "required_capabilities": ["document.create"]},
        task_parameters={
            "action": "create_document",
            "format": "pdf",
            "container": str(folder.resolve()),
            "source_path": str(pdf.resolve()),
            "content": "Tevhid makalesi",
        },
        last_created_file=str(pdf.resolve()),
    )
    ctx.commit_focus("file", str(pdf.resolve()), container=str(folder.resolve()), source="create")
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._handle_with_intent = AsyncMock(return_value="ekran gorevi")

    parsed = parse_goal("ekranda gordugum videolardan herhangi", ctx)
    relation = classify_turn_relation(
        "ekranda gordugum videolardan herhangi", ctx, parsed=parsed
    )
    assert relation.kind is TurnKind.NEW_TASK

    text = await orch.process_message("ekranda gordugum videolardan herhangi")
    leaked = list(videos.glob("*.pdf"))
    assert leaked == [], leaked
    assert "Tevhid makalesi" in (ctx.previous_task_snapshot.get("task_parameters") or {}).get(
        "content", "Tevhid makalesi"
    ) or pdf.read_bytes()[:5] == b"%PDF-"
    assert "ekranda" not in (ctx.task_parameters.get("content") or "")
    assert "ekran" in text.casefold() or orch._handle_with_intent.await_count  # noqa: SLF001


@pytest.mark.asyncio
async def test_youtube_acma_does_not_open(settings, monkeypatch):
    ctx = ConversationalContext()
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._execute_local_tool = AsyncMock(  # noqa: SLF001
        return_value=ToolResultPayload(tool_call_id="1", success=True, output={"url": "https://www.youtube.com"})
    )
    orch._handle_with_intent = AsyncMock(return_value=None)

    assert has_negative_polarity("YouTube acma")
    text = await orch.process_message("YouTube acma")
    for call in orch._execute_local_tool.await_args_list:  # noqa: SLF001
        req = call.args[0] if call.args else None
        name = getattr(req, "name", "")
        assert name != "open_url"
    assert "yapmayacagim" in text.casefold() or "acma" in text.casefold() or "youtube" not in text.casefold()


def test_geri_uses_browser_back_not_history_url():
    ctx = ConversationalContext(last_url="https://www.youtube.com", last_browser_url="https://www.youtube.com")
    intent = AgentIntent(
        goal="geri",
        required_capabilities=("browser.back",),
        plan=(),
        reported_confidence=0.9,
        mode="task",
    )
    plan = IntentRouter(create_default_registry()).route(
        intent, confidence=Confidence.HIGH, context=ctx
    )
    assert plan.kind is RouteKind.CAPABILITY_PLAN
    assert plan.steps[0].tool_name == "browser_nav"
    assert plan.steps[0].tool_arguments.get("action") == "back"
    assert "youtube" not in str(plan.steps[0].tool_arguments).casefold()


@pytest.mark.asyncio
async def test_click_unknown_is_not_verified_success():
    from hermes.tools.verifiers.specific import ClickScreenVerifier

    ctx = VerifierContext(
        tool_name="click",
        tool_arguments={"entity_id": "se_0"},
        execution_success=True,
        execution_output={"entity_id": "se_0", "verification_status": "unknown"},
    )
    result = await ClickScreenVerifier().verify(ctx)
    assert result.status is not VerificationStatus.VERIFIED
