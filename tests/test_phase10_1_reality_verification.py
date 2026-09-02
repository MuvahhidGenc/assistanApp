"""Phase 10.1 — reality verification and cross-tool execution tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.general_task_planner import (
    build_general_organize_plan,
    build_pdf_inspect_plan,
    is_browser_page_check_goal,
)
from hermes.context.conversational_context import ConversationalContext
from hermes.mission.engine import MissionEngine
from hermes.mission.goal_verification import finalize_mission_goal, record_goal_check
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner
from hermes.mission.reality_verification import (
    classify_pdf_as_important,
    compare_snapshots,
    extract_pdf_text,
    filesystem_snapshot,
    is_substantive_screen_text,
    normalize_visible_page_text,
    verify_file_exists,
    verify_move_result,
)
from hermes.mission.replan import analyze_failure_for_replan
from hermes.mission.step_context import run_copy_classified_pdfs, run_organize_files_by_type
from hermes.mission.store import MissionStore
from hermes.security.policy_engine import PolicyDecision
from hermes.server.models import ToolResultPayload
from hermes.tools.manifest import LocalToolRequest
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


def test_goal_finalize_requires_goal_check():
    mission = Mission(mission_id="m1", user_goal="test", status=MissionStatus.RUNNING)
    mission.working_context["goal_check"] = {
        "goal_achieved": False,
        "state_verified": True,
        "message": "Dosya tasinmadi",
    }
    result = finalize_mission_goal(mission, all_steps_done=True)
    assert result.status == "failed"
    assert not result.goal_achieved


def test_goal_finalize_completed_when_verified():
    mission = Mission(mission_id="m2", user_goal="test", status=MissionStatus.RUNNING)
    record_goal_check(mission, goal_achieved=True, state_verified=True, message="Tamam")
    result = finalize_mission_goal(mission, all_steps_done=True)
    assert result.status == "completed"
    assert result.goal_achieved


def test_filesystem_snapshot_and_compare(tmp_path):
    before_dir = tmp_path / "Desktop"
    before_dir.mkdir()
    (before_dir / "a.pdf").write_bytes(b"%PDF")
    before = filesystem_snapshot(before_dir)
    (before_dir / "a.pdf").unlink()
    sub = before_dir / "PDF"
    sub.mkdir()
    (sub / "a.pdf").write_bytes(b"%PDF")
    after = filesystem_snapshot(before_dir)
    diff = compare_snapshots(before, after)
    assert "a.pdf" in diff.removed


@pytest.mark.asyncio
async def test_organize_by_type_moves_files(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    src = desktop / "rapor.pdf"
    src.write_bytes(b"%PDF-test")
    matches = [{"path": str(src), "name": "rapor.pdf"}]
    report = await run_organize_files_by_type(matches, str(desktop))
    assert report["moved"] == 1
    assert (desktop / "PDF" / "rapor.pdf").is_file()
    assert not src.is_file()
    check = verify_move_result(src, desktop / "PDF" / "rapor.pdf")
    assert check["ok"]


@pytest.mark.asyncio
async def test_organize_no_move_not_ok(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    report = await run_organize_files_by_type([], str(desktop))
    assert not report["ok"]
    assert report["copied"] == 0


def test_pdf_importance_requires_content(tmp_path):
    pdf = tmp_path / "fatura.pdf"
    pdf.write_bytes(b"%PDF-1.4\n(fatura odeme)\n")
    extracted = extract_pdf_text(pdf)
    assert extracted.ok or extracted.text
    if extracted.ok:
        assert classify_pdf_as_important(extracted.text, pdf.name)


def test_non_important_pdf_not_classified(tmp_path):
    pdf = tmp_path / "not.txt.pdf"
    pdf.write_bytes(b"%PDF-1.4\n(hello)\n")
    extracted = extract_pdf_text(pdf)
    if extracted.ok:
        assert not classify_pdf_as_important("hello", "not.txt.pdf")


@pytest.mark.asyncio
async def test_copy_classified_only_important(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    important = desktop / "fatura.pdf"
    other = desktop / "note.pdf"
    important.write_bytes(b"%PDF-1.4\n(fatura)\n")
    other.write_bytes(b"%PDF-1.4\n(test)\n")
    dest = desktop / "Onemli_PDF"
    matches = [
        {"path": str(important), "important": True},
        {"path": str(other), "important": False},
    ]
    report = await run_copy_classified_pdfs(matches, str(dest))
    assert report["copied"] == 1
    assert (dest / "fatura.pdf").is_file()
    assert not (dest / "note.pdf").exists()


def test_window_title_not_page_content():
    assert not is_substantive_screen_text("Google - Chrome", window_title="Google - Chrome")
    text, err = normalize_visible_page_text({"text": "Google - Chrome", "window_title": "Google - Chrome"})
    assert not text
    assert err


def test_substantive_page_text():
    body = "Bu sayfada urun listesi ve fiyat tablosu yer aliyor. " * 3
    assert is_substantive_screen_text(body, window_title="Ornek Site - Chrome")
    text, err = normalize_visible_page_text({"text": body, "window_title": "Ornek Site"})
    assert text
    assert not err


def test_report_file_verification(tmp_path):
    report = tmp_path / "rapor.txt"
    report.write_text("# Hermes Web Raporu\n\n## Gorunen metin\nMerhaba dunya", encoding="utf-8")
    check = verify_file_exists(report, min_size=20)
    assert check["ok"]


def test_context_stores_screen_text_not_window():
    ctx = ConversationalContext()
    body = "Bu sayfada detayli urun aciklamalari ve fiyat bilgileri bulunuyor. " * 2
    ctx.update_from_tool(
        "read_screen_text",
        {"text": body, "window_title": "Ornek - Chrome", "verified": True, "ocr": True},
        success=True,
        verified=True,
    )
    assert ctx.last_browser_page_text
    assert any(e.entity_type == "screen_text" for e in ctx.recent_entities)
    assert not any(e.entity_type == "file" for e in ctx.recent_entities)


def test_replan_avoids_same_strategy():
    mission = Mission(mission_id="m3", user_goal="pdf", status=MissionStatus.RUNNING)
    mission.working_context["replan_history"] = [{"strategy": "pdf_regex_extract"}]
    failed = MissionStep(
        step_id="classify_pdf_importance",
        title="x",
        action=StepAction.LOGICAL,
        metadata={"logical_kind": "classify_pdf_importance"},
        status=MissionStepStatus.FAILED,
        result_summary="PDF icerigi okunamadi",
    )
    decision = analyze_failure_for_replan(mission, failed)
    assert decision.new_strategy != "pdf_regex_extract"


def test_organize_plan_has_goal_verification(registry):
    plan = build_general_organize_plan("Masaüstümü biraz düzenle.", registry)
    kinds = [s.metadata.get("logical_kind") for s in plan if s.action == StepAction.LOGICAL]
    assert "organize_files_by_type" in kinds
    assert "verify_desktop_organize" in kinds
    assert any(s.metadata.get("snapshot_key") == "before" for s in plan)


def test_pdf_plan_classifies_before_copy(registry):
    plan = build_pdf_inspect_plan("İndirilenlerdeki PDFleri incele ve önemli olanları ayır.", registry)
    kinds = [s.metadata.get("logical_kind") for s in plan if s.action == StepAction.LOGICAL]
    assert kinds.index("classify_pdf_importance") < kinds.index("copy_classified_pdfs")


def test_browser_page_check_goal():
    assert is_browser_page_check_goal("Açtığın sayfayı kontrol et ve bana ne gördüğünü söyle.")


@pytest.mark.asyncio
async def test_e2e_organize_no_move_fails_goal(registry, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    store = MissionStore()
    mission = store.create_mission("Masaüstümü biraz düzenle.")
    planner = MissionPlanner(AsyncMock(), registry)
    plan = await planner.create_plan(mission)
    assert plan.success

    async def fake_execute(local: LocalToolRequest, run_id: str):
        path = Path(str(local.arguments.get("path") or ""))
        if local.name == "list_directory":
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"path": str(path), "entries": [], "verified": True},
            )
        if local.name == "search_files":
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={
                    "folder": str(path),
                    "pattern": "*",
                    "count": 0,
                    "matches": [],
                    "verified": True,
                },
            )
        return ToolResultPayload(tool_call_id=run_id, success=False, error="unknown")

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)

    with patch.object(engine, "_observe_and_verify", new_callable=AsyncMock) as verify_mock:
        verify_mock.return_value = {
            "verification_status": "verified",
            "verification_method": "test",
            "verification_details": {},
            "verified_at": "now",
            "observation": {},
            "observation_record": None,
        }
        result = await engine.run(mission.mission_id, planner)

    assert result.handled
    assert not result.success
    summary = result.summary.casefold()
    assert any(token in summary for token in ("tasinmadi", "uygun", "bulamadim", "replan", "tamamlanamadi"))
