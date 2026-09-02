"""Phase 9 — autonomous agent planning and adaptive execution tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.agent_planner import AgentPlanner
from hermes.agent.plan_analysis import (
    analyze_goal,
    build_agent_dynamic_plan,
    build_agent_state_snapshot,
    build_create_and_audit_plan,
    build_organize_files_plan,
    is_create_and_audit_goal,
    is_organize_files_goal,
)
from hermes.agent.plan_models import PlanningRoute
from hermes.agent.risk_gate import assess_message_risk
from hermes.context.conversational_context import ConversationalContext
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner
from hermes.mission.replan import (
    MAX_REPLAN_ATTEMPTS,
    analyze_failure_for_replan,
    apply_replan,
)
from hermes.mission.validator import validate_plan_steps
from hermes.tools.registry import create_default_registry


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def planner(registry):
    client = MagicMock()
    return MissionPlanner(client, registry)


# --- 1. Simple single-step routing stays fast ---


def test_simple_task_not_forced_to_mission(registry):
    decision = AgentPlanner(registry).evaluate("Chrome'u aç", ConversationalContext())
    assert decision.route in {PlanningRoute.SERVER, PlanningRoute.FAST_LOCAL}


# --- 2. Multi-step compound routes to mission ---


def test_compound_organize_routes_to_mission(registry):
    msg = "Masaüstündeki rapor dosyalarını düzenle."
    decision = AgentPlanner(registry).evaluate(msg, ConversationalContext())
    assert decision.route == PlanningRoute.MISSION
    assert decision.goal_analysis is not None
    assert decision.goal_analysis.is_compound


# --- 3. Dynamic plan creation ---


def test_dynamic_organize_plan(registry):
    plan = build_organize_files_plan("Masaüstündeki raporları düzenle.", registry)
    assert len(plan) >= 4
    tool_names = {step.tool_name for step in plan if step.action == StepAction.TOOL}
    assert "list_directory" in tool_names
    assert "search_files" in tool_names


def test_create_and_audit_plan(registry):
    msg = "Masaüstümde AjanTest klasörü oluştur, içine üç farklı türde dosya koy, sonra kontrol et."
    assert is_create_and_audit_goal(msg)
    plan = build_create_and_audit_plan(msg, registry)
    assert len(plan) >= 5
    write_steps = [s for s in plan if s.tool_name == "write_file"]
    assert len(write_steps) >= 2


def test_agent_dynamic_plan_integration(registry):
    msg = "Masaüstündeki rapor dosyalarını düzenle."
    plan = build_agent_dynamic_plan(msg, registry)
    validation = validate_plan_steps([s.to_dict() for s in plan], registry)
    assert validation.ok


# --- 4. Tool selection in plan ---


def test_organize_plan_selects_filesystem_tools(registry):
    plan = build_organize_files_plan("PDF raporları düzenle", registry)
    names = [s.tool_name for s in plan if s.tool_name]
    assert "search_files" in names
    assert "create_folder" in names


# --- 5. Verification steps in plan ---


def test_plan_includes_verification_steps(registry):
    plan = build_create_and_audit_plan(
        "AjanTest klasörü oluştur üç dosya koy kontrol et",
        registry,
    )
    verified = [s for s in plan if s.verification.get("required")]
    assert len(verified) >= 3


# --- 6. Replan after failure ---


def test_replan_analyzer_detects_recoverable_failure():
    mission = Mission(mission_id="m1", user_goal="PDF kopyala")
    failed = MissionStep(
        step_id="copy",
        title="Kopyala",
        tool_name="copy_file",
        status=MissionStepStatus.FAILED,
        result_summary="Dosya kilitli",
    )
    done = MissionStep(step_id="search", title="Ara", status=MissionStepStatus.COMPLETED)
    mission.steps = [done, failed]
    decision = analyze_failure_for_replan(mission, failed)
    assert decision.should_replan


def test_replan_limit_respected():
    mission = Mission(mission_id="m1", user_goal="test")
    mission.working_context["replan_count"] = MAX_REPLAN_ATTEMPTS
    failed = MissionStep(step_id="x", title="X", status=MissionStepStatus.FAILED)
    decision = analyze_failure_for_replan(mission, failed)
    assert not decision.should_replan


def test_apply_replan_marks_incomplete_skipped():
    mission = Mission(mission_id="m1", user_goal="test")
    mission.plan_validated = True
    pending = MissionStep(step_id="p", title="P", status=MissionStepStatus.PENDING)
    mission.steps = [pending]
    from hermes.mission.replan import ReplanDecision

    apply_replan(mission, ReplanDecision(should_replan=True, reason="test"))
    assert mission.plan_validated is False
    assert pending.status == MissionStepStatus.SKIPPED


# --- 7. Partial success ---


def test_partial_success_summary():
    from hermes.mission.replan import build_partial_success_summary

    mission = Mission(mission_id="m1", user_goal="test")
    mission.steps = [
        MissionStep(step_id="a", title="Adim A", status=MissionStepStatus.COMPLETED),
        MissionStep(step_id="b", title="Adim B", status=MissionStepStatus.FAILED),
    ]
    summary = build_partial_success_summary(mission, failed_reason="2 dosya kilitli")
    assert "Adim A" in summary
    assert "kilitli" in summary


# --- 8. Context-aware planning ---


def test_agent_state_snapshot_includes_verified(tmp_path):
    f = tmp_path / "rapor.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(
        last_verified_file=str(f.resolve()),
        last_verified_folder=str(tmp_path.resolve()),
    )
    snap = build_agent_state_snapshot(ctx)
    assert snap["verified_file"] == str(f.resolve())
    assert snap["verified_paths"]


def test_goal_analysis_uses_context(tmp_path):
    f = tmp_path / "rapor.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(last_verified_file=str(f.resolve()))
    analysis = analyze_goal(
        "Son oluşturduğun dosyayı kontrol et ve adını düzelt.",
        ctx,
    )
    assert analysis.confidence >= 0.88


# --- 9. Verified state over stale ---


def test_planner_working_context_includes_agent_state(registry):
    ctx_data = {
        "agent_state": {"verified_file": "C:/x/rapor.txt"},
        "goal_analysis": {"desired_state": "Duzenli"},
    }
    from hermes.mission.context import build_agent_context_for_planning

    merged = build_agent_context_for_planning(ctx_data)
    assert merged["verified_file"] == "C:/x/rapor.txt"
    assert merged["desired_state"] == "Duzenli"


# --- 10. No unnecessary clarification ---


def test_deictic_with_verified_skips_clarification(registry, tmp_path):
    f = tmp_path / "rapor.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(
        last_verified_file=str(f.resolve()),
        last_created_file=str(f.resolve()),
    )
    decision = AgentPlanner(registry).evaluate("Son oluşturduğun dosyayı aç.", ctx)
    assert decision.route != PlanningRoute.CLARIFY or not decision.clarification


# --- 11. Risk confirmation gate ---


def test_bulk_delete_requires_confirmation():
    risk = assess_message_risk("Tüm dosyaları toplu sil")
    assert risk.requires_confirmation


def test_normal_create_no_confirmation():
    risk = assess_message_risk("Masaüstünde rapor.txt oluştur")
    assert not risk.requires_confirmation


# --- 12. Mission planner uses agent dynamic plan ---


@pytest.mark.asyncio
async def test_mission_planner_agent_dynamic_source(planner, registry):
    mission = Mission(
        mission_id="test-mission",
        user_goal="Masaüstündeki rapor dosyalarını düzenle.",
        working_context={"agent_state": {}, "goal_analysis": {"is_compound": True}},
    )
    result = await planner.create_plan(mission)
    assert result.success
    assert result.source == "agent_dynamic_heuristic"


# --- 13. Goal detection helpers ---


def test_is_organize_files_goal():
    assert is_organize_files_goal("Masaüstündeki raporları düzenle")
    assert not is_organize_files_goal("Chrome'u aç")


# --- 14. Context file fix plan ---


def test_context_file_fix_plan(registry, tmp_path):
    f = tmp_path / "rapor"
    f.write_text("content", encoding="utf-8")
    plan = build_agent_dynamic_plan(
        "Son oluşturduğun dosyayı kontrol et ve adını düzelt.",
        registry,
        agent_context={"verified_file": str(f.resolve())},
    )
    assert len(plan) >= 1


# --- 15. Planning decision working context ---


def test_mission_decision_carries_working_context(registry):
    decision = AgentPlanner(registry).evaluate(
        "İndirilenlerdeki PDF'leri bul ve masaüstünde rapor klasörü oluştur.",
        ConversationalContext(),
    )
    assert "parsed_goal" in decision.working_context
    assert "agent_state" in decision.working_context
