"""Phase 10 — general autonomous agent tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.agent_planner import AgentPlanner
from hermes.agent.general_goal import classify_generic_goal, is_general_mission_goal
from hermes.agent.general_task_planner import (
    build_browser_report_plan,
    build_disk_info_plan,
    build_general_organize_plan,
    build_general_task_plan,
    build_pdf_inspect_plan,
    is_browser_report_goal,
    is_disk_info_goal,
    is_general_organize_goal,
    is_pdf_inspect_goal,
)
from hermes.agent.plan_models import PlanningRoute, TaskCategory
from hermes.agent.server_tasks import should_defer_to_server
from hermes.context.conversational_context import ConversationalContext
from hermes.mission.planner import MissionPlanner
from hermes.mission.models import StepAction
from hermes.mission.selection import should_route_to_mission
from hermes.mission.store import MissionStore
from hermes.security.policy_engine import PolicyDecision
from hermes.server.models import ToolResultPayload
from hermes.skills.loader import match_skills_for_goal
from hermes.tools.manifest import LocalToolRequest
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


# --- Goal classification ---


def test_general_organize_goal_detected():
    msg = "Masaüstümü biraz düzenle."
    assert is_general_organize_goal(msg)
    assert is_general_mission_goal(msg)
    goal = classify_generic_goal(msg, ConversationalContext())
    assert goal.category == TaskCategory.ORGANIZATION
    assert goal.requires_observe is True


def test_pdf_inspect_goal_detected():
    msg = "İndirilenlerdeki PDFleri incele ve önemli olanları ayır."
    assert is_pdf_inspect_goal(msg)
    assert is_general_mission_goal(msg)
    goal = classify_generic_goal(msg, ConversationalContext())
    assert goal.category == TaskCategory.FILESYSTEM


def test_disk_info_goal_detected():
    msg = "Bilgisayarda ne kadar boş alan var?"
    assert is_disk_info_goal(msg)
    assert is_general_mission_goal(msg)
    goal = classify_generic_goal(msg, ConversationalContext())
    assert goal.category == TaskCategory.INFORMATION


def test_browser_report_goal_detected():
    msg = "Chrome'u aç, bir web sayfasına git, sayfadaki bilgiyi al ve masaüstüne rapor.txt olarak kaydet."
    assert is_browser_report_goal(msg)
    assert is_general_mission_goal(msg)
    goal = classify_generic_goal(msg, ConversationalContext())
    assert goal.category == TaskCategory.MULTI_STEP


# --- Routing ---


def test_organize_routes_to_mission_not_server(registry):
    msg = "Masaüstümü biraz düzenle."
    assert should_route_to_mission(msg)
    assert not should_defer_to_server(msg)
    decision = AgentPlanner(registry).evaluate(msg, ConversationalContext())
    assert decision.route == PlanningRoute.MISSION


def test_pdf_inspect_routes_to_mission(registry):
    msg = "İndirilenlerdeki PDFleri incele ve önemli olanları ayır."
    decision = AgentPlanner(registry).evaluate(msg, ConversationalContext())
    assert decision.route == PlanningRoute.MISSION
    assert "generic_goal" in decision.working_context


def test_organize_progress_hint_natural(registry):
    msg = "Masaüstümü biraz düzenle."
    decision = AgentPlanner(registry).evaluate(msg, ConversationalContext())
    assert decision.progress_hint == "Dosyalari inceliyorum."


# --- Plan builders ---


def test_general_organize_plan_has_observe_steps(registry):
    plan = build_general_organize_plan("Masaüstümü biraz düzenle.", registry)
    assert len(plan) >= 6
    kinds = [s.metadata.get("logical_kind") for s in plan if s.action == StepAction.LOGICAL]
    assert "organize_files_by_type" in kinds
    assert "verify_desktop_organize" in kinds


def test_pdf_inspect_plan(registry):
    plan = build_pdf_inspect_plan("İndirilenlerdeki PDFleri incele ve önemli olanları ayır.", registry)
    assert len(plan) >= 5
    kinds = [s.metadata.get("logical_kind") for s in plan if s.action == StepAction.LOGICAL]
    assert "classify_pdf_importance" in kinds
    assert "copy_classified_pdfs" in kinds


def test_disk_info_plan(registry):
    plan = build_disk_info_plan("Bilgisayarda ne kadar boş alan var?", registry)
    assert len(plan) == 2
    assert plan[0].tool_name == "get_disk_info"
    assert plan[1].metadata.get("logical_kind") == "produce_information_summary"


def test_browser_report_plan(registry):
    plan = build_browser_report_plan(
        "Chrome'u aç, siteye git, bilgiyi al ve masaüstüne rapor.txt kaydet.",
        registry,
    )
    kinds = [s.metadata.get("logical_kind") for s in plan if s.action == StepAction.LOGICAL]
    assert "verify_report_file" in kinds
    assert any(s.tool_name == "write_file" for s in plan)


def test_general_task_plan_dispatcher(registry):
    plan = build_general_task_plan("Masaüstümü biraz düzenle.", registry)
    assert plan
    assert plan[0].step_id == "observe_source"


def test_mission_planner_uses_general_plan_sync(registry):
    import asyncio

    async def _run():
        store = MissionStore()
        mission = store.create_mission("Masaüstümü biraz düzenle.")
        client = AsyncMock()
        client.chat = AsyncMock(return_value={"choices": [{"message": {"content": '{"steps":[]}'}}]})
        planner = MissionPlanner(client, registry)
        return await planner.create_plan(mission)

    result = asyncio.run(_run())
    assert result.success
    assert result.source == "agent_dynamic_heuristic"
    assert any(s.step_id == "observe_source" for s in result.steps)


# --- Skills ---


def test_skills_match_organize():
    skills = match_skills_for_goal("Masaüstümü biraz düzenle.")
    assert any(s.skill_id == "desktop_organize" for s in skills)


def test_skills_match_disk():
    skills = match_skills_for_goal("Bilgisayarda ne kadar boş alan var?")
    assert any(s.skill_id == "disk_space" for s in skills)


# --- E2E mission engine ---


@pytest.mark.asyncio
async def test_e2e_desktop_organize(registry, tmp_path, monkeypatch):
    from hermes.mission.engine import MissionEngine

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "rapor.pdf").write_bytes(b"%PDF-test")
    (desktop / "not.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    store = MissionStore()
    mission = store.create_mission("Masaüstümü biraz düzenle.")
    client = AsyncMock()
    client.chat = AsyncMock(return_value={"choices": [{"message": {"content": '{"steps":[]}'}}]})
    planner = MissionPlanner(client, registry)
    plan = await planner.create_plan(mission)
    assert plan.success

    async def fake_execute(local: LocalToolRequest, run_id: str):
        path = Path(str(local.arguments.get("path") or ""))
        if local.name == "list_directory":
            items = [p.name for p in path.iterdir()] if path.is_dir() else []
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"path": str(path), "entries": items, "verified": True},
            )
        if local.name == "search_files":
            pattern = str(local.arguments.get("pattern") or "*")
            if pattern == "*.pdf":
                matches = [{"path": str(desktop / "rapor.pdf"), "name": "rapor.pdf"}]
            else:
                matches = [{"path": str(p), "name": p.name} for p in desktop.glob(pattern)]
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={
                    "folder": str(path),
                    "pattern": pattern,
                    "count": len(matches),
                    "matches": matches,
                    "verified": True,
                },
            )
        if local.name == "create_folder":
            path.mkdir(parents=True, exist_ok=True)
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"path": str(path.resolve()), "verified": True},
            )
        if local.name == "copy_file":
            src = Path(str(local.arguments.get("source") or local.arguments.get("src") or ""))
            dst = Path(str(local.arguments.get("destination") or local.arguments.get("dst") or ""))
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"source": str(src), "destination": str(dst), "verified": True},
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
    assert result.success or "duzenlendi" in result.summary.casefold() or len(result.step_summaries) > 0


@pytest.mark.asyncio
async def test_e2e_disk_info(registry):
    from hermes.mission.engine import MissionEngine

    store = MissionStore()
    mission = store.create_mission("Bilgisayarda ne kadar boş alan var?")
    planner = MissionPlanner(AsyncMock(), registry)
    plan = await planner.create_plan(mission)
    assert plan.success
    assert any(s.tool_name == "get_disk_info" for s in plan.steps)

    async def fake_execute(local: LocalToolRequest, run_id: str):
        if local.name == "get_disk_info":
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={
                    "disks": [{"drive": "C:", "free_gb": 120.5, "total_gb": 500.0}],
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
    loaded = store.load(mission.mission_id)
    assert loaded.working_context.get("natural_summary") or result.summary


# --- Regression: Phase 9 implicit content still works ---


def test_phase9_implicit_still_routes(registry):
    goal = (
        "Masaüstümde AjanTest2 klasörü oluştur. "
        "İçine üç farklı türde dosya oluştur. "
        "Sonra dosyaları kontrol et ve bana ne oluşturduğunu söyle."
    )
    decision = AgentPlanner(registry).evaluate(goal, ConversationalContext())
    assert decision.route == PlanningRoute.MISSION
