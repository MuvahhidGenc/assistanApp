"""Phase 7.2 — Compound goal & task chaining regression tests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.agent.conversation_flow import handle_meta_conversation
from hermes.agent.goal_parser import parse_goal
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.compound_goal import (
    build_compound_desktop_file_plan,
    is_compound_chained_file_mission,
    parse_compound_file_goal,
    run_read_verify_files,
)
from hermes.mission.engine import MissionEngine
from hermes.mission.models import MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner
from hermes.mission.selection import is_fast_path_candidate, should_route_to_mission
from hermes.mission.step_context import resolve_step_tool_arguments
from hermes.mission.store import MissionStore
from hermes.server.models import ToolResultPayload
from hermes.tools.manifest import extract_url_hint
from hermes.tools.registry import create_default_registry

AJAN_TEST_GOAL = (
    "Masaüstünde AjanTest klasörü oluştur. İçine test1.txt ve test2.txt adında iki dosya oluştur. "
    "Birincisine Birinci dosya, ikincisine İkinci dosya yaz. Sonra bu klasördeki txt dosyalarını bul, "
    "içeriklerini kontrol et ve bana hangisinin hangi içeriğe sahip olduğunu söyle. "
    "Ardından test1.txt dosyasının adını sonuc.txt yap ve sonuc.txt dosyasını aç."
)

CREATE_WRITE_RENAME = (
    "Masaustunde RenameChain klasoru olustur. Icine kaynak.txt olustur, icine Kaynak metin yaz. "
    "Sonra kaynak.txt dosyasinin adini hedef.txt yap."
)

RENAME_OPEN = (
    "Masaustunde OpenChain klasoru olustur. Icine acilacak.txt olustur ve icine Acilacak icerik yaz. "
    "Sonra acilacak.txt dosyasinin adini son.txt yap ve son.txt dosyasini ac."
)


@pytest.fixture
def registry():
    return create_default_registry()


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


def test_compound_goal_detected_for_ajantest():
    assert is_compound_chained_file_mission(AJAN_TEST_GOAL) is True


def test_compound_goal_routes_to_mission_not_fast_path():
    assert should_route_to_mission(AJAN_TEST_GOAL) is True
    assert is_fast_path_candidate(AJAN_TEST_GOAL) is False


def test_compound_goal_no_rename_clarification():
    resolution = ReferenceResolver().resolve(AJAN_TEST_GOAL, ConversationalContext())
    assert not resolution.ambiguous
    assert resolution.intent is None


def test_explicit_filename_not_ambiguous():
    parsed = parse_compound_file_goal(AJAN_TEST_GOAL)
    assert parsed is not None
    assert parsed.rename_source == "test1.txt"
    assert parsed.rename_dest == "sonuc.txt"
    assert parsed.open_target == "sonuc.txt"
    assert len(parsed.files) == 2


def test_local_filenames_not_urls():
    for name in ("test1.txt", "rapor.txt", "sonuc.txt"):
        assert extract_url_hint(name) is None


def test_build_compound_plan_single_mission(registry):
    steps = build_compound_desktop_file_plan(AJAN_TEST_GOAL, registry)
    assert len(steps) >= 8
    tool_names = [step.tool_name for step in steps if step.action == StepAction.TOOL]
    assert "create_folder" in tool_names
    assert tool_names.count("write_file") == 2
    assert "search_files" in tool_names
    assert "rename_path" in tool_names
    assert "open_path" in tool_names
    open_step = next(step for step in steps if step.step_id == "open_result_file")
    assert open_step.tool_arguments["path"].endswith("sonuc.txt")
    rename_step = next(step for step in steps if step.step_id == "rename_target_file")
    assert open_step.depends_on == [rename_step.step_id]


def test_create_write_read_chain(registry):
    goal = (
        "Masaustunde CWR klasoru olustur. Icine a.txt olustur. Birincisine Alpha yaz. "
        "Sonra txt dosyalarini bul ve iceriklerini kontrol et."
    )
    steps = build_compound_desktop_file_plan(goal, registry)
    assert steps
    write_steps = [s for s in steps if s.tool_name == "write_file"]
    assert write_steps
    logical = [s for s in steps if s.action == StepAction.LOGICAL]
    assert any(s.metadata.get("logical_kind") == "read_verify_files" for s in logical)


def test_create_write_rename_chain(registry):
    steps = build_compound_desktop_file_plan(CREATE_WRITE_RENAME, registry)
    rename = next(s for s in steps if s.tool_name == "rename_path")
    assert "kaynak.txt" in str(rename.tool_arguments.get("path", ""))
    assert rename.tool_arguments.get("new_name") == "hedef.txt"


def test_rename_open_chain_binding(registry):
    steps = build_compound_desktop_file_plan(RENAME_OPEN, registry)
    open_step = next(s for s in steps if s.tool_name == "open_path")
    assert open_step.tool_arguments["path"].endswith("son.txt")
    assert open_step.depends_on


def test_previous_step_output_for_open_path(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission(RENAME_OPEN)
    steps = build_compound_desktop_file_plan(RENAME_OPEN, registry)
    mission.steps = steps
    mission.plan_validated = True
    rename = next(s for s in mission.steps if s.tool_name == "rename_path")
    rename.status = MissionStepStatus.COMPLETED
    rename.metadata["tool_output"] = {
        "source": str(Path.home() / "Desktop" / "OpenChain" / "acilacak.txt"),
        "destination": str(Path.home() / "Desktop" / "OpenChain" / "son.txt"),
        "verified": True,
    }
    mission.working_context["step_outputs"] = {
        rename.step_id: {"tool_name": "rename_path", "output": rename.metadata["tool_output"]},
    }
    open_step = next(s for s in mission.steps if s.tool_name == "open_path")
    resolved = resolve_step_tool_arguments(open_step, mission)
    assert resolved["path"].endswith("son.txt")


def test_active_file_modify_context():
    ctx = ConversationalContext()
    path = str(Path.home() / "Desktop" / "AjanTest" / "test1.txt")
    ctx.active_file = path
    ctx.update_from_tool(
        "write_file",
        {"path": path, "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.active_file == path
    assert path in ctx.created_files


def test_last_created_file_rename_context():
    ctx = ConversationalContext()
    old = str(Path.home() / "Desktop" / "AjanTest" / "test1.txt")
    new = str(Path.home() / "Desktop" / "AjanTest" / "sonuc.txt")
    ctx.update_from_tool(
        "rename_path",
        {"source": old, "destination": new, "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.last_renamed_file == new
    assert ctx.active_file == new


def test_last_renamed_file_open_resolution(tmp_path):
    f = tmp_path / "sonuc.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext()
    path = str(f.resolve())
    ctx.last_renamed_file = path
    result = ReferenceResolver().resolve("Onu tekrar ac.", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    assert result.intent.request.arguments["path"] == path


def test_verification_failure_blocks_dependent_step(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission(AJAN_TEST_GOAL)
    steps = build_compound_desktop_file_plan(AJAN_TEST_GOAL, registry)
    mission.steps = steps
    mission.plan_validated = True
    search = next(s for s in mission.steps if s.tool_name == "search_files")
    search.status = MissionStepStatus.VERIFICATION_FAILED
    store.save(mission)

    steps_list = build_compound_desktop_file_plan(AJAN_TEST_GOAL, registry)
    verify_step = next(s for s in steps_list if s.metadata.get("logical_kind") == "read_verify_files")
    assert search.step_id in verify_step.depends_on


def test_mission_restart_preserves_step_context(mission_root, registry):
    store = MissionStore()
    mission = store.create_mission(AJAN_TEST_GOAL)
    steps = build_compound_desktop_file_plan(AJAN_TEST_GOAL, registry)
    mission.steps = steps
    mission.plan_validated = True
    write = next(s for s in steps if s.step_id == "write_test1")
    write.status = MissionStepStatus.COMPLETED
    write.metadata["tool_output"] = {"path": write.tool_arguments["path"], "verified": True}
    mission.working_context["step_outputs"] = {
        write.step_id: {"tool_name": "write_file", "output": write.metadata["tool_output"]},
    }
    store.save(mission)
    loaded = store.load(mission.mission_id)
    assert loaded.working_context["step_outputs"][write.step_id]["output"]["path"]


def test_goal_parser_ordered_steps():
    parsed = parse_goal(AJAN_TEST_GOAL)
    assert parsed.is_multi_step
    assert parsed.ordered_steps
    assert any(step.action_type == "rename_path" for step in parsed.ordered_steps)


def test_meta_conversation_after_mission():
    ctx = ConversationalContext()
    ctx.last_mission_summary = (
        "AjanTest klasorunu olusturdum, 2 dosya hazirladim, test1.txt dosyasini sonuc.txt "
        "olarak yeniden adlandirdim ve actim."
    )
    turn = handle_meta_conversation("Ne yaptin?", ctx)
    assert turn.handled
    assert "AjanTest" in turn.response


def test_meta_last_created_filename():
    ctx = ConversationalContext()
    ctx.last_renamed_file = str(Path.home() / "Desktop" / "AjanTest" / "sonuc.txt")
    turn = handle_meta_conversation("Son olusturdugun dosyanin adi ne?", ctx)
    assert turn.handled
    assert turn.response == "sonuc.txt"


@pytest.mark.asyncio
async def test_compound_mission_e2e_filesystem(registry, mission_root, tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    goal = (
        "Masaustunde AjanTest klasoru olustur. Icine test1.txt ve test2.txt adinda iki dosya olustur. "
        "Birincisine Birinci dosya, ikincisine Ikinci dosya yaz. Sonra bu klasordeki txt dosyalarini bul, "
        "iceriklerini kontrol et. Ardindan test1.txt dosyasinin adini sonuc.txt yap ve sonuc.txt dosyasini ac."
    )

    store = MissionStore()
    mission = store.create_mission(goal)
    steps = build_compound_desktop_file_plan(goal, registry)
    assert steps
    mission.steps = steps
    mission.plan_validated = True
    store.save(mission)

    folder = desktop / "AjanTest"

    async def fake_execute(local, run_id):
        name = local.name
        args = local.arguments
        if name == "create_folder":
            Path(args["path"]).mkdir(parents=True, exist_ok=True)
            return ToolResultPayload(tool_call_id=run_id, success=True, output={"path": args["path"], "verified": True})
        if name == "write_file":
            path = Path(args["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(args.get("content") or ""), encoding="utf-8")
            return ToolResultPayload(tool_call_id=run_id, success=True, output={"path": str(path), "verified": True})
        if name == "search_files":
            matches = sorted(folder.glob("*.txt"))
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={
                    "folder": str(folder),
                    "pattern": "*.txt",
                    "count": len(matches),
                    "matches": [{"path": str(p), "name": p.name} for p in matches],
                    "verified": True,
                },
            )
        if name == "rename_path":
            src = Path(args["path"])
            dst = src.with_name(str(args["new_name"]))
            src.rename(dst)
            return ToolResultPayload(
                tool_call_id=run_id,
                success=True,
                output={"source": str(src), "destination": str(dst), "verified": True},
            )
        if name == "open_path":
            path = str(args["path"])
            return ToolResultPayload(tool_call_id=run_id, success=True, output={"path": path, "verified": True})
        return ToolResultPayload(tool_call_id=run_id, success=False, error=f"unexpected tool {name}")

    executor = MagicMock()
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
        result = await engine.run(mission.mission_id, MissionPlanner(AsyncMock(), registry))

    assert result.success is True
    assert (folder / "sonuc.txt").is_file()
    assert (folder / "sonuc.txt").read_text(encoding="utf-8") == "Birinci dosya"
    assert not (folder / "test1.txt").exists()
    assert "Birinci dosya" in result.summary or "Birinci dosya" in json.dumps(result.user_messages)
    loaded = store.load(mission.mission_id)
    assert loaded.status.value == "completed"
    assert loaded.working_context.get("natural_summary")


def test_read_verify_helper(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("Merhaba", encoding="utf-8")
    ok = run_read_verify_files(str(path), "Merhaba")
    assert ok["ok"] is True
    bad = run_read_verify_files(str(path), "Yanlis")
    assert bad["ok"] is False


def test_planner_uses_compound_heuristic(registry, mission_root):
    planner = MissionPlanner(AsyncMock(), registry)
    mission = MissionStore().create_mission(AJAN_TEST_GOAL)
    result = asyncio.run(planner.create_plan(mission))
    assert result.success
    assert result.source == "compound_file_heuristic"
    assert len(result.steps) >= 8
