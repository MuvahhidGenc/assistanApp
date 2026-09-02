"""Phase 10.2.2 — real runtime fixes for browser OCR and PDF replan."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.general_task_planner import build_browser_page_check_plan, build_pdf_inspect_plan
from hermes.mission.engine import MissionEngine
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner
from hermes.mission.reality_verification import normalize_visible_page_text
from hermes.mission.replan import analyze_failure_for_replan
from hermes.mission.store import MissionStore
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


def test_printwindow_payload_is_substantive():
    payload = {
        "text": "Google Search Gmail Images",
        "window_title": "Google - Google Chrome",
        "lines": ["Google", "Search", "Gmail", "Images"],
        "capture_method": "printwindow",
        "ocr": True,
    }
    text, err = normalize_visible_page_text(payload)
    assert text
    assert not err


def test_imagegrab_blank_payload_rejected():
    payload = {
        "text": "",
        "window_title": "Google - Google Chrome",
        "lines": [],
        "capture_method": "imagegrab_region",
    }
    text, err = normalize_visible_page_text(payload)
    assert not text
    assert err


def test_ocr_image_prefers_richer_candidate():
    from hermes import vision

    class FakeImage:
        pass

    with patch("hermes.vision.preprocess_for_ocr", return_value=FakeImage()):
        with patch("pytesseract.image_to_string", side_effect=["Google", "Google Gmail Images Sign in"]):
            text = vision.ocr_image(FakeImage())
    assert "Gmail" in text


@pytest.mark.asyncio
async def test_ocr_browser_window_uses_printwindow():
    from hermes import vision

    fake_capture = {
        "path": __file__,
        "width": 800,
        "height": 600,
        "format": "png",
        "capture_method": "printwindow",
        "printwindow_ok": True,
        "window_title": "Google - Google Chrome",
        "window_rect": (0, 0, 800, 600),
        "chrome_crop": 96,
    }
    fake_data = {"text": "Google Gmail Images", "lines": ["Google", "Gmail", "Images"], "ocr": True}
    with patch("hermes.tools.windows.input_backend.find_app_window_title", return_value="Google - Google Chrome"):
        with patch("hermes.tools.windows.input_backend.focus_window", return_value={"focused": True}):
            with patch("hermes.tools.windows.input_backend.capture_window_image", return_value=fake_capture):
                with patch("PIL.Image.open") as open_mock:
                    open_mock.return_value = MagicMock()
                    with patch("hermes.vision.ocr_image", return_value="Google Gmail Images"):
                        with patch("hermes.vision.ocr_word_boxes", return_value=[]):
                            result = vision.ocr_browser_window("chrome", title_hint="google")
    assert result["text"]
    assert result.get("capture_method") == "printwindow"


def test_pdf_empty_search_is_not_replan_loop():
    mission = Mission(mission_id="m-pdf-empty", user_goal="pdf inspect", status=MissionStatus.RUNNING)
    failed = MissionStep(
        step_id="classify_pdf_importance",
        title="classify",
        action=StepAction.LOGICAL,
        metadata={"logical_kind": "classify_pdf_importance"},
        status=MissionStepStatus.FAILED,
        result_summary="Indirilenler klasorunde PDF dosyasi bulamadim.",
    )
    mission.steps = [
        MissionStep(
            step_id="scan_pdfs",
            title="scan",
            action=StepAction.TOOL,
            tool_name="search_files",
            status=MissionStepStatus.COMPLETED,
        ),
        failed,
    ]
    decision = analyze_failure_for_replan(mission, failed)
    assert not decision.should_replan


@pytest.mark.asyncio
async def test_classify_auto_filename_fallback_partial(tmp_path, registry):
    """Unreadable PDFs should complete in-place without replan."""
    from hermes.mission.engine import _StepRunOutcome
    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.executor import ToolExecutor
    from hermes.security.approval_manager import ApprovalManager
    from hermes.security.policy_engine import AuditLogger, PolicyEngine
    from hermes.config.settings import RiskLevel

    pdf_path = tmp_path / "notes.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n% invalid for extractor")

    store = MissionStore()
    mission = store.create_mission("Indirilenlerdeki PDFleri incele ve onemli olanlari ayir.")
    mission.working_context["search_results"] = {
        "scan_pdfs": {
            "matched_files": [str(pdf_path)],
            "source_location": str(tmp_path),
            "file_pattern": "*.pdf",
            "result_count": 1,
        }
    }
    mission.working_context["step_outputs"] = {
        "scan_pdfs": {
            "tool_name": "search_files",
            "output": {
                "verified": True,
                "pattern": "*.pdf",
                "matches": [{"path": str(pdf_path), "name": pdf_path.name}],
                "count": 1,
            },
        }
    }
    step = MissionStep(
        step_id="classify_pdf_importance",
        title="PDF siniflandir",
        action=StepAction.LOGICAL,
        depends_on=["scan_pdfs"],
        metadata={"logical_kind": "classify_pdf_importance"},
    )
    scan_step = MissionStep(
        step_id="scan_pdfs",
        title="scan",
        action=StepAction.TOOL,
        tool_name="search_files",
        tool_arguments={"path": str(tmp_path), "pattern": "*.pdf"},
        status=MissionStepStatus.COMPLETED,
    )
    mission.steps = [scan_step, step]
    store.save(mission)

    executor = ToolExecutor(
        registry,
        PolicyEngine([RiskLevel.HIGH_RISK]),
        AuditLogger(str(tmp_path / "audit.log"), []),
        ApprovalManager(),
    )
    engine = MissionEngine(store, registry, executor)
    outcome = _StepRunOutcome()
    result = await engine._run_logical_step(mission, step, outcome)  # noqa: SLF001
    assert result.step_done
    assert step.status == MissionStepStatus.COMPLETED
    assert "incelendi" in (step.result_summary or "").casefold()


@pytest.mark.asyncio
async def test_pdf_inspect_mission_e2e_empty_downloads(tmp_path, registry):
    """End-to-end: empty PDF folder should finish with a clear partial message."""
    from unittest.mock import AsyncMock as AM

    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest
    from hermes.tools.manifest import ToolResultPayload

    downloads = tmp_path / "Downloads"
    downloads.mkdir()

    store = MissionStore()
    mission = store.create_mission("Indirilenlerdeki PDFleri incele ve onemli olanlari ayir.")
    planner = MissionPlanner(AM(), registry)

    with patch(
        "hermes.context.goal_resolution.extract_location_path",
        return_value=str(downloads),
    ):
        plan = await planner.create_plan(mission)
    assert plan.success
    assert plan.source == "pdf_inspect_heuristic"

    async def fake_execute(local: LocalToolRequest, run_id: str):
        if local.name == "search_files":
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={
                    "folder": str(downloads),
                    "pattern": "*.pdf",
                    "matches": [],
                    "count": 0,
                    "verified": True,
                },
            )
        if local.name == "create_folder":
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"path": str(downloads / "Onemli_PDF"), "already_existed": False},
            )
        return ToolResultPayload(tool_call_id=run_id, success=True, output={})

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    mission.steps = plan.steps
    mission.plan_validated = True
    mission.plan_source = plan.source
    store.save(mission)

    with patch(
        "hermes.mission.step_context.scan_files_on_filesystem",
        return_value={"folder": str(downloads), "matches": [], "error": None},
    ):
        result = await engine.run(mission.mission_id, planner)

    assert result.handled
    assert "replan limiti" not in (result.summary or "").casefold()
    assert any(
        token in (result.summary or "").casefold()
        for token in ("pdf", "bulamadim", "incelendi")
    )


def test_browser_page_check_passes_title_hint(registry):
    plan = build_browser_page_check_plan(
        "Actigin sayfayi kontrol et ve bana ne gordugunu soyle.",
        registry,
    )
    read = next(s for s in plan if s.tool_name == "read_screen_text")
    assert read.tool_arguments.get("focus_browser") is True


@pytest.mark.asyncio
async def test_browser_page_check_mission_e2e(registry):
    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest
    from hermes.tools.manifest import ToolResultPayload

    store = MissionStore()
    mission = store.create_mission("Actigin sayfayi kontrol et ve bana ne gordugunu soyle.")
    planner = MissionPlanner(AsyncMock(), registry)
    from hermes.agent.general_task_planner import build_browser_page_check_plan

    steps = build_browser_page_check_plan(mission.user_goal, registry)
    assert steps

    screen_payload = {
        "text": "Google Search Gmail Images Sign in",
        "lines": ["Google", "Search", "Gmail", "Images"],
        "window_title": "Google - Google Chrome",
        "capture_method": "printwindow",
        "ocr": True,
    }

    async def fake_execute(local: LocalToolRequest, run_id: str):
        if local.name == "read_screen_text":
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output=screen_payload,
            )
        return ToolResultPayload(tool_call_id=run_id, success=True, output={})

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    mission.steps = steps
    mission.plan_validated = True
    mission.plan_source = "browser_page_check"
    store.save(mission)

    result = await engine.run(mission.mission_id, planner)
    assert result.handled
    assert "not_substantive" not in (result.summary or "").casefold()
    assert result.success or "gorduklerim" in (result.summary or "").casefold()
