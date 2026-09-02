"""Phase 10.2 — runtime reliability and safe file organization tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.application_catalog import (
    has_explicit_filename,
    is_web_or_app_open_message,
    resolve_application,
    resolve_web_url,
)
from hermes.agent.general_task_planner import build_general_organize_plan, build_pdf_inspect_plan
from hermes.agent.goal_router import GoalRouter
from hermes.agent.local_intent import guess_local_action, match_local_intent
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.engine import MissionEngine
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner
from hermes.mission.reality_verification import (
    classify_pdf_by_filename,
    extract_pdf_text,
    safe_move_file,
    verify_move_result,
)
from hermes.mission.replan import analyze_failure_for_replan
from hermes.mission.step_context import run_copy_classified_pdfs, run_organize_files_by_type
from hermes.mission.store import MissionStore
from hermes.security.policy_engine import PolicyDecision
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


def test_safe_move_verifies_before_delete(tmp_path):
    src = tmp_path / "a.txt"
    dest = tmp_path / "sub" / "a.txt"
    src.write_text("hello", encoding="utf-8")
    result = safe_move_file(src, dest)
    assert result["ok"]
    assert dest.is_file()
    assert not src.is_file()
    assert verify_move_result(src, dest)["ok"]


def test_safe_move_keeps_source_on_failure(tmp_path):
    src = tmp_path / "a.txt"
    dest = tmp_path / "nope" / "a.txt"
    src.write_text("hello", encoding="utf-8")
    dest.parent.mkdir(parents=True)
    dest.write_text("blocker", encoding="utf-8")
    src.chmod(0o444)
    try:
        result = safe_move_file(src, dest)
        assert not result.get("ok")
        assert src.is_file()
    finally:
        src.chmod(0o644)


@pytest.mark.asyncio
async def test_organize_moves_not_copies(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    src = desktop / "rapor.pdf"
    src.write_bytes(b"%PDF-test")
    report = await run_organize_files_by_type([{"path": str(src), "name": "rapor.pdf"}], str(desktop))
    assert report["moved"] == 1
    assert (desktop / "PDF" / "rapor.pdf").is_file()
    assert not src.is_file()
    assert verify_move_result(src, desktop / "PDF" / "rapor.pdf")["ok"]


@pytest.mark.asyncio
async def test_organize_no_move_not_completed(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    report = await run_organize_files_by_type([], str(desktop))
    assert not report["ok"]
    assert report["moved"] == 0


def test_pdf_content_before_importance(tmp_path):
    pdf = tmp_path / "note.pdf"
    pdf.write_bytes(b"%PDF-1.4\n(hello world)\n")
    extracted = extract_pdf_text(pdf)
    assert extracted.ok
    assert not classify_pdf_by_filename("note.pdf")


@pytest.mark.asyncio
async def test_non_important_pdf_not_moved(tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    important = folder / "fatura.pdf"
    other = folder / "note.pdf"
    important.write_bytes(b"%PDF-1.4\n(fatura odeme)\n")
    other.write_bytes(b"%PDF-1.4\n(hello)\n")
    dest = folder / "Onemli_PDF"
    extracted_imp = extract_pdf_text(important)
    extracted_other = extract_pdf_text(other)
    from hermes.mission.reality_verification import classify_pdf_as_important

    matches = [
        {
            "path": str(important),
            "important": extracted_imp.ok and classify_pdf_as_important(extracted_imp.text, important.name),
        },
        {
            "path": str(other),
            "important": extracted_other.ok and classify_pdf_as_important(extracted_other.text, other.name),
        },
    ]
    report = await run_copy_classified_pdfs(matches, str(dest))
    assert report["moved"] == 1
    assert (dest / "fatura.pdf").is_file()
    assert not (dest / "note.pdf").exists()
    assert not important.is_file()
    assert other.is_file()


def test_unreadable_pdf_not_auto_important(tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 binary")
    extracted = extract_pdf_text(pdf)
    if not extracted.ok:
        from hermes.mission.reality_verification import classify_pdf_as_important

        assert not classify_pdf_as_important("", pdf.name)


def test_replan_uses_different_pdf_strategy():
    mission = Mission(mission_id="m1", user_goal="pdf", status=MissionStatus.RUNNING)
    mission.working_context["replan_history"] = [{"strategy": "pdf_regex_extract"}]
    mission.steps = [
        MissionStep(
            step_id="scan_pdfs",
            title="scan",
            action=StepAction.TOOL,
            tool_name="search_files",
            status=MissionStepStatus.COMPLETED,
        ),
        MissionStep(
            step_id="classify_pdf_importance",
            title="x",
            action=StepAction.LOGICAL,
            metadata={"logical_kind": "classify_pdf_importance"},
            status=MissionStepStatus.FAILED,
            result_summary="PDF icerigi okunamadi",
        ),
    ]
    failed = mission.steps[1]
    decision = analyze_failure_for_replan(mission, failed)
    assert decision.should_replan
    assert decision.new_strategy == "pdf_filename_heuristic"


def test_google_open_not_file_context():
    ctx = ConversationalContext()
    ctx.active_file = str(Path.home() / "Desktop" / "rapor.txt")
    ctx.last_created_file = ctx.active_file
    resolution = ReferenceResolver().resolve("Google'ı aç", ctx)
    assert resolution.intent is None
    assert not resolution.resolved_references.get("target_file")
    route = GoalRouter(create_default_registry()).route("Google'ı aç", ctx)
    assert route.intent is not None
    assert route.intent.request.name == "open_url"
    assert "google.com" in str(route.intent.request.arguments.get("url", ""))


def test_chrome_open_is_application():
    ctx = ConversationalContext()
    ctx.active_file = str(Path.home() / "Desktop" / "rapor.txt")
    route = GoalRouter(create_default_registry()).route("Chrome'u aç", ctx)
    assert route.intent is not None
    assert route.intent.request.name == "open_app"
    assert route.intent.request.arguments.get("app") == "chrome"


def test_explicit_file_open_not_overridden_by_context(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    sonuc = desktop / "sonuc.txt"
    sonuc.write_text("ok", encoding="utf-8")
    rapor = desktop / "rapor.txt"
    rapor.write_text("old", encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    ctx = ConversationalContext()
    ctx.active_file = str(rapor)
    resolution = ReferenceResolver().resolve("sonuc.txt'yi aç", ctx)
    assert resolution.intent is not None
    assert resolution.intent.request.name == "open_path"
    assert "sonuc.txt" in str(resolution.intent.request.arguments.get("path", ""))


def test_web_shortcut_helpers():
    assert resolve_web_url("Google'ı aç") == "https://www.google.com"
    assert resolve_application("Google'ı aç") is None
    assert resolve_application("Chrome'u aç") == "chrome"
    assert is_web_or_app_open_message("Google'ı aç")
    assert has_explicit_filename("sonuc.txt'yi aç")


def test_match_local_intent_google_is_url():
    intent = match_local_intent("Google'ı aç")
    assert intent is not None
    assert intent.request.name == "open_url"


def test_guess_local_action_prefers_google_over_active_file():
    ctx = ConversationalContext()
    ctx.active_file = str(Path.home() / "Desktop" / "rapor.txt")
    intent = guess_local_action("Google'ı aç", conv_ctx=ctx)
    assert intent is not None
    assert intent.request.name == "open_url"


def test_organize_plan_has_verification(registry):
    plan = build_general_organize_plan("Masaüstümü biraz düzenle.", registry)
    kinds = [s.metadata.get("logical_kind") for s in plan if s.action == StepAction.LOGICAL]
    assert "verify_desktop_organize" in kinds


def test_pdf_plan_classifies_before_move(registry):
    plan = build_pdf_inspect_plan("İndirilenlerdeki PDFleri incele ve önemli olanları ayır.", registry)
    kinds = [s.metadata.get("logical_kind") for s in plan if s.action == StepAction.LOGICAL]
    assert kinds.index("classify_pdf_importance") < kinds.index("copy_classified_pdfs")


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

    from hermes.tools.manifest import LocalToolRequest

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
