"""Phase 10.2.3 — PDF inspect runtime: no replan loop, MOVE + terminal partial."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.general_task_planner import _resolve_source_path, build_pdf_inspect_plan
from hermes.context.system_paths import detect_known_folder_alias, resolve_known_folder
from hermes.mission.engine import MissionEngine
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner
from hermes.mission.replan import analyze_failure_for_replan, is_pdf_inspect_mission
from hermes.mission.store import MissionStore
from hermes.tools.registry import create_default_registry


TURKISH_PDF_GOAL = "İndirilenlerdeki PDFleri incele ve önemli olanları ayır."


@pytest.fixture
def registry():
    return create_default_registry()


def test_turkish_capital_i_resolves_downloads():
    assert detect_known_folder_alias(TURKISH_PDF_GOAL) == "Downloads"
    resolved = resolve_known_folder(TURKISH_PDF_GOAL)
    assert resolved is not None
    assert resolved.name == "Downloads"
    source = _resolve_source_path(TURKISH_PDF_GOAL)
    assert source.endswith("Downloads")


def test_pdf_inspect_mission_never_replans(registry):
    mission = Mission(
        mission_id="m-pdf",
        user_goal="Indirilenlerdeki PDFleri incele",
        status=MissionStatus.RUNNING,
        plan_source="pdf_inspect_heuristic",
    )
    mission.steps = [
        MissionStep(
            step_id="scan_pdfs",
            title="scan",
            action=StepAction.TOOL,
            tool_name="search_files",
            status=MissionStepStatus.COMPLETED,
        ),
        MissionStep(
            step_id="copy_important_pdfs",
            title="copy",
            action=StepAction.LOGICAL,
            metadata={"logical_kind": "copy_classified_pdfs"},
            status=MissionStepStatus.FAILED,
            result_summary="Onemli PDF dosyalari kopyalanamadi",
        ),
    ]
    assert is_pdf_inspect_mission(mission)
    decision = analyze_failure_for_replan(mission, mission.steps[1])
    assert not decision.should_replan


@pytest.mark.asyncio
async def test_pdf_inspect_e2e_important_pdf_move_no_replan(tmp_path, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    dest = downloads / "Onemli dosyalar"

    invoice = downloads / "fatura_ocak.pdf"
    invoice.write_bytes(b"%PDF-1.4\n(fatura)\n")
    noise = downloads / "notes.pdf"
    noise.write_bytes(b"%PDF-1.4\n(random notes)\n")

    store = MissionStore()
    mission = store.create_mission("Indirilenlerdeki PDFleri incele ve onemli olanlari ayir.")
    planner = MissionPlanner(AsyncMock(), registry)

    with patch(
        "hermes.context.goal_resolution.extract_location_path",
        return_value=str(downloads),
    ):
        plan = await planner.create_plan(mission)
    assert plan.source == "pdf_inspect_heuristic"
    assert str(dest) in str(plan.steps[2].tool_arguments.get("path"))

    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest, ToolResultPayload

    async def fake_execute(local: LocalToolRequest, run_id: str):
        tool = registry.get(local.name)
        result = await tool.execute(**dict(local.arguments))
        return ToolResultPayload(
            tool_call_id=run_id,
            success=bool(result.success),
            output=result.output,
            error=result.error,
        )

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    mission.steps = plan.steps
    mission.plan_validated = True
    mission.plan_source = plan.source
    store.save(mission)

    result = await engine.run(mission.mission_id, planner)

    assert "replan limiti" not in (result.summary or "").casefold()
    assert int(mission.working_context.get("replan_count") or 0) == 0
    loaded = store.load(mission.mission_id)
    assert loaded is not None
    assert loaded.working_context.get("pdf_examined_count", 0) >= 1
    natural = str(loaded.working_context.get("natural_summary") or result.summary or "")
    assert any(token in natural.casefold() for token in ("incelendi", "onemli", "ayirildi", "bulunamadi"))


@pytest.mark.asyncio
async def test_pdf_inspect_skips_existing_folder(tmp_path, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    dest = downloads / "Onemli dosyalar"
    dest.mkdir()
    pdf_path = downloads / "sozlesme.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n(sozlesme)\n")

    store = MissionStore()
    mission = store.create_mission("Indirilenlerdeki PDFleri incele ve onemli olanlari ayir.")
    steps = build_pdf_inspect_plan(mission.user_goal, registry)
    with patch(
        "hermes.context.goal_resolution.extract_location_path",
        return_value=str(downloads),
    ):
        steps = build_pdf_inspect_plan(mission.user_goal, registry)

    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest, ToolResultPayload

    create_calls = 0

    async def fake_execute(local: LocalToolRequest, run_id: str):
        nonlocal create_calls
        if local.name == "create_folder":
            create_calls += 1
        tool = registry.get(local.name)
        result = await tool.execute(**dict(local.arguments))
        return ToolResultPayload(
            tool_call_id=run_id,
            success=bool(result.success),
            output=result.output,
            error=result.error,
        )

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    mission.steps = steps
    mission.plan_validated = True
    mission.plan_source = "pdf_inspect_heuristic"
    store.save(mission)

    planner = MissionPlanner(AsyncMock(), registry)
    result = await engine.run(mission.mission_id, planner)

    assert create_calls == 0
    assert "replan limiti" not in (result.summary or "").casefold()
    folder_step = next(s for s in (store.load(mission.mission_id) or mission).steps if s.step_id == "prepare_important_folder")
    assert folder_step.status == MissionStepStatus.COMPLETED


@pytest.mark.asyncio
async def test_pdf_inspect_cached_search_not_rerun(tmp_path, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    pdf_path = downloads / "rapor.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n(rapor)\n")

    store = MissionStore()
    mission = store.create_mission("PDF incele")
    mission.working_context["search_results"] = {
        "scan_pdfs": {
            "matched_files": [str(pdf_path)],
            "file_pattern": "*.pdf",
            "source_location": str(downloads),
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
    scan = MissionStep(
        step_id="scan_pdfs",
        title="PDF dosyalarini ariyorum",
        action=StepAction.TOOL,
        tool_name="search_files",
        tool_arguments={"path": str(downloads), "pattern": "*.pdf"},
        status=MissionStepStatus.PENDING,
    )
    mission.steps = [scan]
    mission.plan_source = "pdf_inspect_heuristic"
    store.save(mission)

    executor = MagicMock()
    engine = MissionEngine(store, registry, executor)
    outcome = await engine._run_step(mission, scan, "run-1")  # noqa: SLF001

    assert outcome.step_done
    assert scan.status == MissionStepStatus.COMPLETED
    assert "onbellek" in (outcome.summary_line or "").casefold()
    executor._executor.execute_tool_call.assert_not_called() if hasattr(executor, "_executor") else None


@pytest.mark.asyncio
async def test_pdf_inspect_turkish_goal_empty_verify_message(tmp_path, registry):
    """Turkish İ in goal must not fall back to Desktop and generic verify failure."""
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    dest = desktop / "Onemli dosyalar"
    dest.mkdir()

    store = MissionStore()
    mission = store.create_mission(TURKISH_PDF_GOAL)
    steps = build_pdf_inspect_plan(TURKISH_PDF_GOAL, registry)
    for step in steps:
        if step.step_id == "scan_pdfs":
            step.tool_arguments = {"path": str(desktop), "pattern": "*.pdf"}
        if step.metadata.get("logical_kind") in {"copy_classified_pdfs", "verify_pdf_separation"}:
            step.metadata["destination"] = str(dest)
        if step.tool_name == "create_folder":
            step.tool_arguments = {"path": str(dest)}

    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest, ToolResultPayload

    async def fake_execute(local: LocalToolRequest, run_id: str):
        tool = registry.get(local.name)
        result = await tool.execute(**dict(local.arguments))
        return ToolResultPayload(
            tool_call_id=run_id,
            success=bool(result.success),
            output=result.output,
            error=result.error,
        )

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    mission.steps = steps
    mission.plan_validated = True
    mission.plan_source = "pdf_inspect_heuristic"
    store.save(mission)

    planner = MissionPlanner(AsyncMock(), registry)
    result = await engine.run(mission.mission_id, planner)
    loaded = store.load(mission.mission_id)
    summary = str(loaded.working_context.get("natural_summary") or result.summary or "")
    assert "dogrulanamadi" not in summary.casefold()
    assert "pdf" in summary.casefold()


@pytest.mark.asyncio
async def test_pdf_inspect_zero_important_completes_successfully(tmp_path, registry):
    """Inspect-only outcome (0 important) must not surface as mission FAILED in engine."""
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    pdf_path = downloads / "notes.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n(random notes)\n")

    store = MissionStore()
    mission = store.create_mission(TURKISH_PDF_GOAL)
    with patch("hermes.context.goal_resolution.extract_location_path", return_value=str(downloads)):
        steps = build_pdf_inspect_plan(TURKISH_PDF_GOAL, registry)

    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest, ToolResultPayload

    async def fake_execute(local: LocalToolRequest, run_id: str):
        tool = registry.get(local.name)
        result = await tool.execute(**dict(local.arguments))
        return ToolResultPayload(
            tool_call_id=run_id,
            success=bool(result.success),
            output=result.output,
            error=result.error,
        )

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    mission.steps = steps
    mission.plan_validated = True
    mission.plan_source = "pdf_inspect_heuristic"
    store.save(mission)

    planner = MissionPlanner(AsyncMock(), registry)
    result = await engine.run(mission.mission_id, planner)
    assert result.success is True
    assert "onemli bulunamadi" in (result.summary or "").casefold()
    assert int((store.load(mission.mission_id) or mission).working_context.get("replan_count") or 0) == 0


@pytest.mark.asyncio
async def test_pdf_inspect_second_run_no_replan(tmp_path, registry):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    dest = downloads / "Onemli dosyalar"
    dest.mkdir()
    pdf_path = downloads / "fatura.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n(fatura)\n")

    store = MissionStore()
    goal = TURKISH_PDF_GOAL

    from hermes.security.policy_engine import PolicyDecision
    from hermes.tools.manifest import LocalToolRequest, ToolResultPayload

    async def fake_execute(local: LocalToolRequest, run_id: str):
        tool = registry.get(local.name)
        result = await tool.execute(**dict(local.arguments))
        return ToolResultPayload(
            tool_call_id=run_id,
            success=bool(result.success),
            output=result.output,
            error=result.error,
        )

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor, execute_local_tool=fake_execute)
    planner = MissionPlanner(AsyncMock(), registry)

    with patch("hermes.context.goal_resolution.extract_location_path", return_value=str(downloads)):
        steps = build_pdf_inspect_plan(goal, registry)

    for run_idx in range(2):
        mission = store.create_mission(goal)
        mission.steps = steps
        mission.plan_validated = True
        mission.plan_source = "pdf_inspect_heuristic"
        store.save(mission)
        result = await engine.run(mission.mission_id, planner)
        assert "replan limiti" not in (result.summary or "").casefold()
        assert int((store.load(mission.mission_id) or mission).working_context.get("replan_count") or 0) == 0
        if run_idx == 0:
            assert result.success is True


@pytest.mark.asyncio
async def test_pdf_inspect_idempotent_already_in_dest(tmp_path, registry):
    from hermes.mission.step_context import run_copy_classified_pdfs

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    dest = downloads / "Onemli dosyalar"
    dest.mkdir()
    moved_pdf = dest / "fatura_ocak.pdf"
    moved_pdf.write_bytes(b"%PDF-1.4\n(fatura)\n")
    missing_source = downloads / "fatura_ocak.pdf"

    report = await run_copy_classified_pdfs(
        [{"path": str(missing_source), "name": "fatura_ocak.pdf", "important": True}],
        str(dest),
    )
    assert report["failed"] == 0
    assert int(report.get("moved") or 0) >= 1
    assert moved_pdf.is_file()
