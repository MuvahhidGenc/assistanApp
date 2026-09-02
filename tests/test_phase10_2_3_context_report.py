"""Phase 10.2.3 — report collision, read_file routing, browser URL context."""
from __future__ import annotations

from pathlib import Path
import pytest

from hermes.agent.conversation_flow import handle_meta_conversation
from hermes.agent.general_task_planner import build_browser_report_plan
from hermes.agent.tool_intent import ToolIntentMatcher
from hermes.context.conversational_context import ConversationalContext
from hermes.context.file_intent import is_file_read_message, resolve_read_file_path
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.step_context import resolve_mission_browser_url, resolve_step_tool_arguments
from hermes.tools.registry import create_default_registry
from hermes.tools.windows.file_tools import resolve_unique_file_path


@pytest.fixture
def registry():
    return create_default_registry()


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return desktop_dir


def test_resolve_unique_file_path_skips_existing(desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski icerik", encoding="utf-8")
    unique = resolve_unique_file_path(desktop / "rapor.txt")
    assert unique.name == "rapor (1).txt"
    assert not unique.exists()


def test_existing_rapor_unchanged_after_unique_resolution(desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski icerik", encoding="utf-8")
    new_path = resolve_unique_file_path(existing)
    new_path.write_text("yeni rapor", encoding="utf-8")
    assert existing.read_text(encoding="utf-8") == "eski icerik"
    assert new_path.read_text(encoding="utf-8") == "yeni rapor"


def test_write_report_plan_has_unique_if_exists(registry):
    steps = build_browser_report_plan("Google sayfasini kontrol et ve rapor kaydet", registry)
    write_step = next(item for item in steps if item.step_id == "write_report_file")
    assert write_step.metadata.get("unique_if_exists") is True


def test_resolve_step_tool_arguments_adds_unique_if_exists():
    mission = Mission(mission_id="m1", user_goal="rapor", status=MissionStatus.RUNNING)
    step = MissionStep(
        step_id="write_report_file",
        title="write",
        action=StepAction.TOOL,
        tool_name="write_file",
        tool_arguments={"path": str(Path.home() / "Desktop" / "rapor.txt"), "content": "x" * 40},
        metadata={"unique_if_exists": True},
    )
    args = resolve_step_tool_arguments(step, mission)
    assert args.get("unique_if_exists") is True


def test_last_created_file_points_to_new_report(desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski", encoding="utf-8")
    new_path = resolve_unique_file_path(existing)
    new_path.write_text("yeni rapor icerigi", encoding="utf-8")

    ctx = ConversationalContext()
    ctx.last_created_file = str(new_path)
    ctx.update_from_tool(
        "write_file",
        {"path": str(new_path), "exists": True, "size": new_path.stat().st_size, "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.last_created_file == str(new_path.resolve())
    assert Path(ctx.last_created_file).read_text(encoding="utf-8") == "yeni rapor icerigi"


def test_son_olusturdugun_rapor_routes_to_read_file(registry, desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski", encoding="utf-8")
    new_path = desktop / "rapor (1).txt"
    new_path.write_text("yeni rapor satiri", encoding="utf-8")

    ctx = ConversationalContext()
    ctx.last_created_file = str(new_path)
    message = "son olusturdugun rapor.txt icinde ne yaziyor buraya yaz"
    assert is_file_read_message(message)
    result = ReferenceResolver().resolve(message, ctx)
    assert result.intent is not None
    assert result.intent.request.name == "read_file"
    assert Path(result.intent.request.arguments["path"]).name == "rapor (1).txt"


def test_icerigi_oku_routes_read_not_create(registry, desktop):
    target = desktop / "notlar.txt"
    target.write_text("satir bir", encoding="utf-8")
    ctx = ConversationalContext()
    ctx.active_file = str(target)

    result = ReferenceResolver().resolve("icerigini oku", ctx)
    assert result.intent is not None
    assert result.intent.request.name == "read_file"


def test_buraya_yaz_not_folder_create_clarification(desktop):
    target = desktop / "rapor (1).txt"
    target.write_text("rapor govdesi", encoding="utf-8")
    ctx = ConversationalContext()
    ctx.last_created_file = str(target)

    result = ReferenceResolver().resolve("buraya yaz", ctx)
    assert result.clarification != "Hangi klasore dosya olusturmami istiyorsun?"
    assert result.intent is None or result.intent.request.name == "read_file"


def test_meta_conversation_returns_file_content(desktop):
    target = desktop / "rapor (1).txt"
    target.write_text("Hermes test raporu", encoding="utf-8")
    ctx = ConversationalContext()
    ctx.last_created_file = str(target)

    turn = handle_meta_conversation("son olusturdugun rapor.txt icinde ne yaziyor", ctx)
    assert turn.handled
    assert "Hermes test raporu" in turn.response


def test_open_url_stored_in_conversational_context():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "open_url",
        {"url": "https://www.google.com", "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.last_url == "https://www.google.com"
    assert ctx.last_browser_url == "https://www.google.com"
    refs = ctx.resolved_references()
    assert refs["last_url"] == "https://www.google.com"


def test_resolve_mission_browser_url_from_open_url_step():
    mission = Mission(mission_id="m2", user_goal="google", status=MissionStatus.RUNNING)
    mission.steps = [
        MissionStep(
            step_id="navigate_target",
            title="nav",
            action=StepAction.TOOL,
            tool_name="open_url",
            tool_arguments={"url": "https://www.google.com"},
            status=MissionStepStatus.COMPLETED,
        )
    ]
    mission.working_context["step_outputs"] = {
        "navigate_target": {
            "tool_name": "open_url",
            "output": {"url": "https://www.google.com"},
        }
    }
    assert resolve_mission_browser_url(mission) == "https://www.google.com"


def test_generated_report_contains_browser_url(desktop):
    from hermes.mission.reality_verification import build_report_content

    mission = Mission(mission_id="m3", user_goal="rapor", status=MissionStatus.RUNNING)
    mission.steps = [
        MissionStep(
            step_id="navigate_target",
            title="nav",
            action=StepAction.TOOL,
            tool_name="open_url",
            tool_arguments={"url": "https://www.google.com"},
            status=MissionStepStatus.COMPLETED,
        ),
    ]
    mission.working_context["step_outputs"] = {
        "navigate_target": {
            "tool_name": "open_url",
            "output": {"url": "https://www.google.com"},
        },
    }
    url = resolve_mission_browser_url(mission)
    content = build_report_content(
        url=url,
        page_title="google.com",
        visible_text="Google Search Gmail Images",
        extraction_timestamp="2026-01-01T00:00:00+00:00",
    )
    assert "URL: https://www.google.com" in content
    assert "Google Search" in content


def test_explicit_rapor_open_vs_last_created(desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski", encoding="utf-8")
    new_path = desktop / "rapor (1).txt"
    new_path.write_text("yeni", encoding="utf-8")

    ctx = ConversationalContext()
    ctx.last_created_file = str(new_path)

    explicit = ReferenceResolver().resolve("rapor.txt'yi ac", ctx)
    assert explicit.intent is not None
    assert Path(explicit.intent.request.arguments["path"]).name == "rapor.txt"

    deictic = ReferenceResolver().resolve("son olusturdugun dosyayi ac", ctx)
    assert deictic.intent is not None
    assert Path(deictic.intent.request.arguments["path"]).name == "rapor (1).txt"


def test_tool_intent_read_file_active_file(registry, desktop):
    target = desktop / "rapor (1).txt"
    target.write_text("tool intent okuma", encoding="utf-8")
    ctx = ConversationalContext()
    ctx.active_file = str(target)
    match = ToolIntentMatcher(registry).match("icerigi oku", ctx)
    assert match.intent is not None
    assert match.intent.request.name == "read_file"


def test_resolve_read_file_path_explicit_desktop(desktop):
    target = desktop / "rapor.txt"
    target.write_text("desktop rapor", encoding="utf-8")
    ctx = ConversationalContext()
    path = resolve_read_file_path("rapor.txt icinde ne yaziyor", ctx)
    assert path is not None
    assert Path(path).name == "rapor.txt"


def test_report_mission_inherits_browser_url_from_context():
    mission = Mission(mission_id="m4", user_goal="raporu kaydet", status=MissionStatus.RUNNING)
    mission.working_context = {
        "resolved_references": {"last_url": "https://www.google.com"},
        "last_url": "https://www.google.com",
        "last_browser_url": "https://www.google.com",
    }
    assert resolve_mission_browser_url(mission) == "https://www.google.com"


def test_report_mission_inherits_url_from_context_snapshot():
    mission = Mission(mission_id="m5", user_goal="raporu kaydet", status=MissionStatus.RUNNING)
    mission.working_context = {
        "context_snapshot": {"last_url": "https://www.google.com", "last_browser_url": "https://www.google.com"},
    }
    assert resolve_mission_browser_url(mission) == "https://www.google.com"


def test_sync_from_mission_updates_last_created_file(desktop):
    target = desktop / "rapor (2).txt"
    target.write_text("rapor govdesi", encoding="utf-8")
    mission = Mission(mission_id="m6", user_goal="rapor", status=MissionStatus.COMPLETED)
    mission.working_context = {"last_created_file": str(target)}
    ctx = ConversationalContext()
    ctx.sync_from_mission(mission)
    assert ctx.last_created_file == str(target)


def test_extract_explicit_filename_numbered_variant():
    from hermes.context.folder_reference import extract_explicit_filename

    assert extract_explicit_filename("rapor (1).txt'yi ac") == "rapor (1).txt"
    assert extract_explicit_filename("rapor.txt'yi ac") == "rapor.txt"


def test_resolve_unique_file_path_second_collision(desktop):
    (desktop / "rapor.txt").write_text("eski", encoding="utf-8")
    (desktop / "rapor (1).txt").write_text("eski1", encoding="utf-8")
    unique = resolve_unique_file_path(desktop / "rapor.txt")
    assert unique.name == "rapor (2).txt"


@pytest.mark.asyncio
async def test_write_file_collision_chain_last_created(desktop, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: desktop.parent)
    from hermes.tools.windows.file_tools import WriteFileTool

    tool = WriteFileTool()
    content = "yeni rapor icerigi " + ("x" * 30)

    result1 = await tool.execute(path=str(desktop / "rapor.txt"), content=content)
    assert result1.success
    assert Path(result1.output["path"]).name == "rapor.txt"

    (desktop / "rapor.txt").write_text("eski", encoding="utf-8")
    result2 = await tool.execute(path=str(desktop / "rapor.txt"), content=content)
    assert result2.success
    actual = Path(result2.output["path"])
    assert actual.name == "rapor (1).txt"
    assert (desktop / "rapor.txt").read_text(encoding="utf-8") == "eski"

    ctx = ConversationalContext()
    ctx.update_from_tool("write_file", result2.output, success=True, verified=True)
    assert Path(ctx.last_created_file).name == "rapor (1).txt"
    assert any(Path(item).name == "rapor (1).txt" for item in ctx.created_files)


def test_open_rapor_1_txt_explicit_no_ambiguity(desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski", encoding="utf-8")
    numbered = desktop / "rapor (1).txt"
    numbered.write_text("yeni", encoding="utf-8")

    ctx = ConversationalContext()
    ctx.last_created_file = str(numbered)
    ctx.recent_files = [str(existing), str(numbered)]

    result = ReferenceResolver().resolve("rapor (1).txt'yi ac", ctx)
    assert result.ambiguous is False
    assert result.intent is not None
    assert Path(result.intent.request.arguments["path"]).name == "rapor (1).txt"


def test_son_olusturdugun_dosya_open_uses_last_created(desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski", encoding="utf-8")
    numbered = desktop / "rapor (1).txt"
    numbered.write_text("yeni", encoding="utf-8")

    ctx = ConversationalContext()
    ctx.last_created_file = str(numbered)

    result = ReferenceResolver().resolve("son olusturdugun dosyayi ac", ctx)
    assert result.intent is not None
    assert Path(result.intent.request.arguments["path"]).name == "rapor (1).txt"


def test_son_olusturdugun_dosya_read_content(desktop):
    numbered = desktop / "rapor (1).txt"
    numbered.write_text("Hermes gercek rapor", encoding="utf-8")
    ctx = ConversationalContext()
    ctx.last_created_file = str(numbered)

    path = resolve_read_file_path("son olusturdugun dosyanin icerigini buraya yaz", ctx)
    assert path is not None
    assert Path(path).name == "rapor (1).txt"


def test_sync_from_mission_updates_created_files(desktop):
    first = desktop / "rapor.txt"
    second = desktop / "rapor (1).txt"
    first.write_text("eski", encoding="utf-8")
    second.write_text("yeni", encoding="utf-8")
    mission = Mission(mission_id="m7", user_goal="rapor", status=MissionStatus.COMPLETED)
    mission.working_context = {
        "last_created_file": str(second),
        "created_files": [str(second), str(first)],
    }
    ctx = ConversationalContext()
    ctx.sync_from_mission(mission)
    assert ctx.last_created_file == str(second)
    assert any(Path(item).name == "rapor (1).txt" for item in ctx.created_files)


def test_google_report_read_chain_uses_actual_path(desktop):
    existing = desktop / "rapor.txt"
    existing.write_text("eski", encoding="utf-8")
    actual = desktop / "rapor (1).txt"
    actual.write_text("# Hermes Web Raporu\nURL: https://www.google.com\nGoogle Search", encoding="utf-8")

    ctx = ConversationalContext()
    ctx.last_url = "https://www.google.com"
    ctx.update_from_tool(
        "write_file",
        {"path": str(actual), "exists": True, "size": actual.stat().st_size, "verified": True},
        success=True,
        verified=True,
    )

    mission = Mission(mission_id="m8", user_goal="rapor", status=MissionStatus.COMPLETED)
    mission.working_context = {
        "last_created_file": str(actual),
        "created_files": [str(actual)],
        "last_url": ctx.last_url,
    }
    ctx.sync_from_mission(mission)

    turn = handle_meta_conversation("son olusturdugun rapor.txt icinde ne yaziyor", ctx)
    assert turn.handled
    assert "Google Search" in turn.response
    assert Path(ctx.last_created_file).name == "rapor (1).txt"
