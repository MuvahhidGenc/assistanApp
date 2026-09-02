"""Phase 10.2.1 — browser OCR + PDF plan priority bugfix tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.general_task_planner import build_pdf_inspect_plan, is_browser_page_check_goal
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner, _goal_has_file_operations_intent
from hermes.mission.reality_verification import is_substantive_screen_text, normalize_visible_page_text
from hermes.mission.replan import analyze_failure_for_replan
from hermes.mission.store import MissionStore
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


def test_google_homepage_ocr_lines_are_substantive():
    lines = ["Google", "Search", "Gmail", "Images", "Sign in"]
    payload = {
        "text": "Google",
        "window_title": "Google - Google Chrome",
        "lines": lines,
        "ocr": True,
    }
    text, err = normalize_visible_page_text(payload)
    assert text
    assert not err
    assert "Gmail" in text or "Search" in text


def test_window_title_only_still_rejected():
    payload = {"text": "Google - Google Chrome", "window_title": "Google - Google Chrome", "lines": []}
    text, err = normalize_visible_page_text(payload)
    assert not text
    assert err


def test_pdf_inspect_not_file_operations_intent():
    goal = "İndirilenlerdeki PDFleri incele ve önemli olanları ayır."
    assert not _goal_has_file_operations_intent(goal)


@pytest.mark.asyncio
async def test_planner_prefers_pdf_classify_plan(registry):
    store = MissionStore()
    mission = store.create_mission("İndirilenlerdeki PDFleri incele ve önemli olanları ayır.")
    planner = MissionPlanner(AsyncMock(), registry)
    result = await planner.create_plan(mission)
    assert result.success
    kinds = [s.metadata.get("logical_kind") for s in result.steps if s.action == StepAction.LOGICAL]
    assert "classify_pdf_importance" in kinds
    assert "copy_search_matches" not in kinds


def test_browser_page_check_plan_focuses_browser(registry):
    assert is_browser_page_check_goal("Açtığın sayfayı kontrol et ve bana ne gördüğünü söyle.")
    from hermes.agent.general_task_planner import build_browser_page_check_plan

    plan = build_browser_page_check_plan(
        "Açtığın sayfayı kontrol et ve bana ne gördüğünü söyle.",
        registry,
    )
    read = next(s for s in plan if s.tool_name == "read_screen_text")
    assert read.tool_arguments.get("focus_browser") is True


def test_pdf_partial_unreadable_not_replan_loop():
    mission = Mission(mission_id="m1", user_goal="pdf", status=MissionStatus.RUNNING)
    failed = MissionStep(
        step_id="verify_pdf_goal",
        title="verify",
        action=StepAction.LOGICAL,
        metadata={"logical_kind": "verify_pdf_separation"},
        status=MissionStepStatus.FAILED,
        result_summary="3 PDF incelendi, onemli bulunamadi; hicbir dosya ayrilmadi.",
    )
    mission.steps = [
        MissionStep(step_id="scan", title="s", action=StepAction.TOOL, tool_name="search_files", status=MissionStepStatus.COMPLETED),
        failed,
    ]
    decision = analyze_failure_for_replan(mission, failed)
    assert not decision.should_replan


def test_substantive_multi_line_short_ui():
    assert is_substantive_screen_text(
        "Google",
        window_title="Google - Chrome",
        lines=["Google", "Gmail", "Images"],
    )


@pytest.mark.asyncio
async def test_ocr_browser_window_focuses_before_ocr():
    from hermes import vision

    fake_data = {"text": "Google Gmail", "lines": ["Google", "Gmail"], "ocr": True}
    with patch("hermes.tools.windows.input_backend.find_app_window_title", return_value="Google - Chrome"):
        with patch("hermes.tools.windows.input_backend.focus_window", return_value={"focused": True}) as focus_mock:
            with patch("hermes.vision.ocr_screen", return_value=fake_data) as ocr_mock:
                result = vision.ocr_browser_window("chrome")
    assert result["text"]
    focus_mock.assert_called_once()
    ocr_mock.assert_called_once()
    assert result.get("window_title") == "Google - Chrome"
