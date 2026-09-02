"""Phase 10.2.3 runtime fixes — write verify path + PDF replan loop."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.general_task_planner import build_pdf_inspect_plan, is_pdf_inspect_goal
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner, build_file_operations_plan
from hermes.mission.replan import analyze_failure_for_replan
from hermes.mission.step_context import resolve_search_matches_fallback
from hermes.mission.store import MissionStore
from hermes.mission.engine import MissionEngine, _StepRunOutcome
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.specific import WriteFileVerifier
from hermes.tools.windows.file_tools import WriteFileTool


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.mark.asyncio
async def test_write_file_verifier_uses_actual_output_path(tmp_path):
    existing = tmp_path / "rapor.txt"
    existing.write_text("eski", encoding="utf-8")
    actual = tmp_path / "rapor (1).txt"
    content = "# Hermes Web Raporu\nURL: https://www.google.com\n" + ("x" * 40)
    actual.write_text(content, encoding="utf-8")

    verifier = WriteFileVerifier()
    ctx = VerifierContext(
        tool_name="write_file",
        tool_arguments={"path": str(existing), "content": content},
        execution_success=True,
        execution_output={"path": str(actual), "exists": True, "size": actual.stat().st_size},
    )
    result = await verifier.verify(ctx)
    assert result.status.value == "verified"
    assert result.details.get("path") == str(actual)


@pytest.mark.asyncio
async def test_write_file_tool_unique_path_content_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    existing = desktop / "rapor.txt"
    existing.write_text("eski icerik", encoding="utf-8")
    content = "Yeni rapor icerigi " + ("Gmail " * 20)
    tool = WriteFileTool()
    result = await tool.execute(
        path=str(desktop / "rapor.txt"),
        content=content,
        unique_if_exists=True,
    )
    assert result.success
    out = result.output or {}
    written = Path(str(out.get("path")))
    assert written.name == "rapor (1).txt"
    assert written.read_text(encoding="utf-8") == content
    assert existing.read_text(encoding="utf-8") == "eski icerik"


def test_resolve_search_matches_fallback_from_search_results():
    mission = Mission(mission_id="m1", user_goal="pdf", status=MissionStatus.RUNNING)
    mission.working_context["search_results"] = {
        "scan_pdfs": {
            "matched_files": ["/tmp/a.pdf", "/tmp/b.pdf"],
            "file_pattern": "*.pdf",
            "source_location": "/tmp",
            "result_count": 2,
        }
    }
    matches, pattern, step_id = resolve_search_matches_fallback(mission)
    assert len(matches) == 2
    assert pattern == "*.pdf"
    assert step_id == "scan_pdfs"


def test_replan_blocks_same_failed_tool_twice():
    mission = Mission(mission_id="m2", user_goal="pdf", status=MissionStatus.RUNNING)
    mission.working_context["replan_count"] = 1
    mission.working_context["last_failed_tool"] = "search_files"
    mission.steps = [
        MissionStep(
            step_id="done",
            title="done",
            action=StepAction.TOOL,
            tool_name="list_directory",
            status=MissionStepStatus.COMPLETED,
        ),
        MissionStep(step_id="scan_pdfs", title="s", action=StepAction.TOOL, tool_name="search_files"),
    ]
    failed = mission.steps[1]
    failed.status = MissionStepStatus.FAILED
    failed.result_summary = "timeout"
    decision = analyze_failure_for_replan(mission, failed)
    assert decision.should_replan is False
    assert "Ayni adim tekrar basarisiz" in decision.reason


def test_pdf_inspect_not_file_operations_plan(registry):
    goal = "Indirilenlerdeki PDFleri incele ve onemli olanlari ayir."
    assert is_pdf_inspect_goal(goal)
    assert build_file_operations_plan(goal, registry) == []
    plan = build_pdf_inspect_plan(goal, registry)
    assert plan
    assert plan[0].step_id == "scan_pdfs"
    assert any(s.metadata.get("logical_kind") == "classify_pdf_importance" for s in plan)


@pytest.mark.asyncio
async def test_classify_uses_search_fallback_when_verified_missing(tmp_path, registry):
    pdf_path = tmp_path / "fatura.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 minimal")

    store = MissionStore()
    mission = store.create_mission("PDF incele")
    mission.working_context["search_results"] = {
        "scan_pdfs": {
            "matched_files": [str(pdf_path)],
            "file_pattern": "*.pdf",
            "source_location": str(tmp_path),
            "result_count": 1,
        }
    }
    step = MissionStep(
        step_id="classify_pdf_importance",
        title="classify",
        action=StepAction.LOGICAL,
        depends_on=["scan_pdfs"],
        metadata={"logical_kind": "classify_pdf_importance"},
    )
    scan = MissionStep(
        step_id="scan_pdfs",
        title="scan",
        action=StepAction.TOOL,
        tool_name="search_files",
        status=MissionStepStatus.COMPLETED,
    )
    mission.steps = [scan, step]
    store.save(mission)

    executor = MagicMock()
    engine = MissionEngine(store, registry, executor)
    outcome = _StepRunOutcome()
    result = await engine._run_logical_step(mission, step, outcome)  # noqa: SLF001
    assert result.step_done
    assert step.status == MissionStepStatus.COMPLETED


@pytest.mark.asyncio
async def test_pdf_empty_downloads_no_replan_loop(tmp_path, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    store = MissionStore()
    mission = store.create_mission("Indirilenlerdeki PDFleri incele ve onemli olanlari ayir.")
    planner = MissionPlanner(AsyncMock(), registry)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "hermes.context.goal_resolution.extract_location_path",
            lambda _g: str(downloads),
        )
        plan = await planner.create_plan(mission)
    assert plan.source == "pdf_inspect_heuristic"

    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest, ToolResultPayload

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
                output={"path": str(downloads / "Onemli_PDF")},
            )
        return ToolResultPayload(tool_call_id=run_id, success=True, output={})

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    mission.steps = plan.steps
    mission.plan_validated = True
    mission.plan_source = plan.source
    store.save(mission)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "hermes.mission.step_context.scan_files_on_filesystem",
            lambda *a, **k: {"folder": str(downloads), "matches": [], "error": None},
        )
        result = await engine.run(mission.mission_id, planner)
    assert "replan limiti" not in (result.summary or "").casefold()
