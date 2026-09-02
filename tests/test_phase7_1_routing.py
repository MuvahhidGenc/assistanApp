"""Phase 7.1 — Goal parser and reference resolver mission entry regression tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.goal_parser import ActionKind, parse_goal
from hermes.agent.mission_flow import (
    handle_mission_commands,
    is_confirmation_ack,
    is_independent_interrupt,
    is_mission_resume_message,
)
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver, match_named_file
from hermes.context.system_paths import extract_file_type_pattern, resolve_known_folder
from hermes.mission.models import MissionStatus
from hermes.mission.selection import should_route_to_mission
from hermes.mission.store import MissionStore
from hermes.tools.manifest import extract_url_hint, _looks_like_local_filename


@pytest.fixture
def mission_root(tmp_path, monkeypatch):
    root = tmp_path / "missions"
    index_path = root / "index.json"
    monkeypatch.setattr("hermes.mission.store.missions_dir", lambda: root)
    monkeypatch.setattr("hermes.mission.store.missions_index_path", lambda: index_path)
    monkeypatch.setattr(
        "hermes.mission.store.ensure_user_dirs",
        lambda: root.mkdir(parents=True, exist_ok=True),
    )
    return root


@pytest.mark.parametrize(
    "message",
    [
        "Indirilenler",
        "Indirilenlerden",
        "indirilenlerdeki",
        "Downloads",
    ],
)
def test_downloads_reference_variants(message: str):
    path = resolve_known_folder(message)
    assert path is not None
    assert path.name == "Downloads"
    assert path == (Path.home() / "Downloads").resolve()


@pytest.mark.parametrize(
    ("message", "pattern"),
    [
        ("PDF'leri", "*.pdf"),
        ("PDF dosyalarini", "*.pdf"),
        ("pdflerden", "*.pdf"),
        ("PDF'leri bul", "*.pdf"),
    ],
)
def test_pdf_pattern_variants(message: str, pattern: str):
    assert extract_file_type_pattern(message) == pattern


def test_pdf_copy_mission_routes():
    message = (
        "Indirilenler klasorundeki PDF dosyalarini bul ve "
        "masaustunde Raporlar klasoru olusturup oraya kopyala."
    )
    resolution = ReferenceResolver().resolve(message, ConversationalContext())
    assert not resolution.ambiguous
    assert resolution.intent is None
    assert should_route_to_mission(message)


def test_pdf_summary_mission_routes():
    message = (
        "Indirilenlerden PDFleri okuyup genel olarak rapor ozeti hazirla. "
        "Bu ozeti masaustunde Raporlar klasoru olusturup icine rapor.txt dosyasina yaz."
    )
    assert should_route_to_mission(message)
    parsed = parse_goal(message)
    assert parsed.requires_read
    assert parsed.requires_summary
    assert parsed.requires_write
    assert parsed.file_pattern == "*.pdf"
    assert parsed.output_filename == "rapor.txt"


def test_goal_parser_structured_pdf_workflow():
    message = (
        "Indirilenlerden PDF'leri bul, masaustunde Raporlar klasoru olustur ve rapor.txt yaz"
    )
    parsed = parse_goal(message)
    assert parsed.source_location
    assert Path(parsed.source_location).name == "Downloads"
    assert parsed.file_pattern == "*.pdf"
    assert parsed.entities.get("folder_name") == "Raporlar"
    assert ActionKind.SEARCH in parsed.required_actions or ActionKind.COPY in parsed.required_actions


def test_desktop_and_raporlar_separated():
    message = "masaustunde Raporlar klasoru olustur"
    parsed = parse_goal(message)
    assert parsed.entities.get("folder_name") == "Raporlar"
    assert "Raporlar" in parsed.destination or parsed.entities.get("folder_name") == "Raporlar"


@pytest.mark.parametrize("filename", ["final.txt", "rapor.txt"])
def test_local_filenames_not_urls(filename: str):
    assert extract_url_hint(filename) is None
    assert _looks_like_local_filename(filename)


def test_real_url_still_detected():
    assert extract_url_hint("https://example.com/page") == "https://example.com/page"
    assert extract_url_hint("www.google.com") == "https://www.google.com"


def test_filename_stem_does_not_false_match_content_text():
    ctx_files = [str(Path.home() / "Desktop" / "HermesPhase6" / "ucuncu.txt")]
    result = match_named_file(
        "Icerigini Hermes calisiyor olarak degistir",
        ctx_files,
        allow_stem=False,
    )
    assert not result.resolved_references.get("target_file")


def test_failed_mission_does_not_block_chrome():
    assert is_independent_interrupt("Chrome'u ac") is True


def test_tamam_only_resumes_waiting_mission(mission_root):
    store = MissionStore()
    ctx = ConversationalContext()
    waiting = store.create_mission("Onay bekleyen gorev")
    waiting.status = MissionStatus.WAITING_FOR_USER
    store.save(waiting)
    ctx.active_mission_id = waiting.mission_id

    assert is_confirmation_ack("tamam")
    cmd = handle_mission_commands("tamam", ctx, store)
    assert cmd.resume_mission_id == waiting.mission_id

    store.cancel_mission(waiting.mission_id)
    ctx.active_mission_id = None
    cmd2 = handle_mission_commands("tamam", ConversationalContext(), store)
    assert not cmd2.handled


def test_devam_et_is_resume_message():
    assert is_mission_resume_message("devam et")


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


@pytest.mark.asyncio
async def test_orchestrator_chrome_after_failed_mission(settings, mission_root, monkeypatch):
    store = MissionStore()
    ctx = ConversationalContext()
    failed = store.create_mission("Anlasilmayan gorev")
    failed.status = MissionStatus.FAILED
    store.save(failed)
    ctx.active_mission_id = failed.mission_id

    orchestrator = AgentOrchestrator(settings, MagicMock(), mission_store=store)
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr("hermes.context.conversational_context.ConversationalContext.save", lambda self: None)
    monkeypatch.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)

    async def fake_execute(intent, message, conv_ctx, **kwargs):
        return "Chrome acildi."

    orchestrator._execute_resolved_local_intent = fake_execute  # type: ignore[method-assign]

    text = await orchestrator.process_message("Chrome'u ac")
    assert "chrome" in text.casefold() or "acil" in text.casefold()


@pytest.mark.asyncio
async def test_pdf_mission_e2e_filesystem(tmp_path, monkeypatch):
    from hermes.tools.windows.file_tools import CopyFileTool, SearchFilesTool

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    pdf1 = downloads / "test1.pdf"
    pdf2 = downloads / "test2.pdf"
    pdf1.write_bytes(b"%PDF-1.4 test1")
    pdf2.write_bytes(b"%PDF-1.4 test2")

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    reports = desktop / "Raporlar"
    reports.mkdir()

    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw) if raw else downloads,
    )

    search = SearchFilesTool()
    search_result = await search.execute(path=str(downloads), pattern="*.pdf")
    assert search_result.success
    assert search_result.output["count"] >= 2

    for pdf in (pdf1, pdf2):
        copy = CopyFileTool()
        copy_result = await copy.execute(source=str(pdf), destination=str(reports))
        assert copy_result.success
        assert (reports / pdf.name).exists()

    summary_path = reports / "rapor.txt"
    summary_path.write_text("PDF ozet: test1 + test2", encoding="utf-8")
    assert summary_path.read_text(encoding="utf-8").startswith("PDF ozet")
