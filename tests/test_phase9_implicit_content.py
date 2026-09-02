"""Phase 9 — implicit file content generation regression tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.agent_planner import AgentPlanner
from hermes.agent.implicit_file_content import (
    generate_implicit_file_specs,
    infer_file_type_count,
    requires_implicit_content_generation,
)
from hermes.agent.plan_analysis import build_create_and_audit_plan, is_create_and_audit_goal
from hermes.agent.plan_models import PlanningRoute
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.models import MissionStepStatus, StepAction
from hermes.mission.selection import should_route_to_mission
from hermes.mission.write_content import (
    extract_literal_write_content,
    is_literal_composite_file_mission,
    plan_composite_file_sequence,
)
from hermes.tools.registry import create_default_registry


AJAN_TEST2_GOAL = (
    "Masaüstümde AjanTest2 klasörü oluştur. "
    "İçine üç farklı türde dosya oluştur. "
    "Sonra dosyaları kontrol et ve bana ne oluşturduğunu söyle."
)

EXPLICIT_GOAL = (
    "Masaüstünde HermesTest2 klasörü oluştur. "
    "İçine test.txt dosyası oluştur ve dosyanın içine TAM OLARAK `123456789` yaz."
)


@pytest.fixture
def registry():
    return create_default_registry()


def test_implicit_content_detected_for_ajan_test2():
    assert requires_implicit_content_generation(AJAN_TEST2_GOAL)
    assert extract_literal_write_content(AJAN_TEST2_GOAL).literal_content is None


def test_explicit_content_not_implicit():
    assert not requires_implicit_content_generation(EXPLICIT_GOAL)
    assert extract_literal_write_content(EXPLICIT_GOAL).literal_content == "123456789"


def test_not_literal_composite_when_implicit():
    assert not is_literal_composite_file_mission(AJAN_TEST2_GOAL)
    assert plan_composite_file_sequence(AJAN_TEST2_GOAL) == []


def test_should_route_to_mission_for_implicit_multi_file():
    assert should_route_to_mission(AJAN_TEST2_GOAL)
    assert is_create_and_audit_goal(AJAN_TEST2_GOAL)


def test_agent_planner_routes_to_mission(registry):
    decision = AgentPlanner(registry).evaluate(AJAN_TEST2_GOAL, ConversationalContext())
    assert decision.route == PlanningRoute.MISSION


def test_three_different_file_types_generated(registry, tmp_path):
    folder = tmp_path / "AjanTest2"
    specs = generate_implicit_file_specs(AJAN_TEST2_GOAL, folder)
    assert len(specs) == 3
    extensions = {spec.extension for spec in specs}
    assert extensions == {".txt", ".md", ".csv"}


def test_create_and_audit_plan_has_three_writes(registry):
    plan = build_create_and_audit_plan(AJAN_TEST2_GOAL, registry)
    write_steps = [s for s in plan if s.tool_name == "write_file"]
    assert len(write_steps) == 3
    assert all(s.metadata.get("implicit_content") for s in write_steps)
    assert any(s.tool_arguments["path"].endswith(".txt") for s in write_steps)
    assert any(s.tool_arguments["path"].endswith(".md") for s in write_steps)
    assert any(s.tool_arguments["path"].endswith(".csv") for s in write_steps)


def test_variants_infer_count():
    assert infer_file_type_count("içine bir metin dosyası ve bir CSV oluştur") == 2
    assert infer_file_type_count("üç farklı türde dosya") == 3


def test_variant_two_file_plan(registry):
    msg = "Deneme klasörü oluştur, içine bir metin dosyası ve bir CSV oluştur, sonra kontrol et."
    assert requires_implicit_content_generation(msg)
    plan = build_create_and_audit_plan(msg, registry)
    write_steps = [s for s in plan if s.tool_name == "write_file"]
    assert len(write_steps) == 2


@pytest.mark.asyncio
async def test_e2e_create_scan_summary(registry, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, patch

    from hermes.mission.engine import MissionEngine
    from hermes.mission.planner import MissionPlanner
    from hermes.mission.store import MissionStore
    from hermes.security.policy_engine import PolicyDecision
    from hermes.server.models import ToolResultPayload
    from hermes.tools.manifest import LocalToolRequest

    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "hermes.tools.windows.file_tools.resolve_user_path",
        lambda raw: Path(raw),
    )

    store = MissionStore()
    mission = store.create_mission(AJAN_TEST2_GOAL)
    planner = MissionPlanner(AsyncMock(), registry)
    plan = await planner.create_plan(mission)
    assert plan.success
    assert plan.source == "agent_dynamic_heuristic"

    async def fake_execute(local: LocalToolRequest, run_id: str):
        path = Path(str(local.arguments.get("path") or ""))
        if local.name == "create_folder":
            path.mkdir(parents=True, exist_ok=True)
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"path": str(path.resolve()), "verified": True},
            )
        if local.name == "write_file":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(local.arguments.get("content") or ""), encoding="utf-8")
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"path": str(path.resolve()), "verified": True},
            )
        if local.name == "list_directory":
            items = [p.name for p in path.iterdir()] if path.is_dir() else []
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"path": str(path), "entries": items, "verified": True},
            )
        return ToolResultPayload(tool_call_id=run_id, success=False, error="unknown")

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW

    engine = MissionEngine(
        store,
        registry,
        executor,
        execute_local_tool=fake_execute,
    )

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
    assert result.success

    folder = desktop / "AjanTest2"
    assert folder.is_dir()
    files = list(folder.iterdir())
    assert len(files) == 3
    extensions = {f.suffix for f in files}
    assert extensions == {".txt", ".md", ".csv"}
    assert "Olusturduklarim" in result.summary or "AjanTest2" in result.summary


def test_context_after_mission_sync(tmp_path):
    from hermes.mission.models import Mission, MissionStatus

    f1 = tmp_path / "AjanTest2" / "ornek.txt"
    f1.parent.mkdir(parents=True)
    f1.write_text("x", encoding="utf-8")
    ctx = ConversationalContext()
    mission = Mission(
        mission_id="m1",
        user_goal=AJAN_TEST2_GOAL,
        status=MissionStatus.COMPLETED,
    )
    mission.working_context["verified_created_files"] = [str(f1.resolve())]
    ctx.sync_from_mission(mission)
    ctx.update_from_tool(
        "write_file",
        {"path": str(f1.resolve()), "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.last_verified_file == str(f1.resolve())
    assert ctx.last_created_file == str(f1.resolve())

    resolver = ReferenceResolver()
    ctx.last_verified_file = str(f1.resolve())
    ctx.last_created_file = str(f1.resolve())
    ctx.recent_files = [str(f1.resolve())]
    result = resolver.resolve("Son oluşturduğun dosyayı aç.", ctx)
    assert result.intent is not None
    assert result.intent.request.arguments["path"] == str(f1.resolve())
