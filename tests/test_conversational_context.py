from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.context.conversational_context import ConversationalContext
from hermes.context.content_extraction import extract_content_modification
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.engine import MissionEngine
from hermes.mission.models import Mission, MissionStep, MissionStepStatus, StepAction
from hermes.mission.output_tracking import infer_expected_outputs, outputs_requirement_met
from hermes.mission.store import MissionStore
from hermes.security.policy_engine import PolicyDecision
from hermes.server.models import ToolResultPayload
from hermes.tools.registry import create_default_registry


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


@pytest.fixture
def ctx(tmp_path) -> ConversationalContext:
    folder = tmp_path / "HermesMultiTest"
    folder.mkdir()
    bilgisayar = folder / "bilgisayar.txt"
    tarih = folder / "tarih.txt"
    bilgisayar.write_text("a", encoding="utf-8")
    tarih.write_text("b", encoding="utf-8")
    return ConversationalContext(
        active_file=str(bilgisayar.resolve()),
        active_folder=str(folder.resolve()),
        last_created_file=str(bilgisayar.resolve()),
        last_modified_file=str(bilgisayar.resolve()),
        last_created_folder=str(folder.resolve()),
        recent_files=[str(bilgisayar.resolve()), str(tarih.resolve())],
        recent_folders=[str(folder.resolve())],
    )


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


def test_last_created_file_resolution(resolver, ctx):
    ctx.last_created_file = ctx.recent_files[-1]
    ctx.active_file = ctx.last_created_file
    result = resolver.resolve("Son olusturdugumuz dosyayi ac.", ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    assert result.intent.request.arguments["path"] == ctx.last_created_file


def test_active_file_resolution(resolver, ctx):
    result = resolver.resolve("Icerigini sadece Afferin Dostum yap.", ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "write_file"
    assert result.intent.request.arguments["path"] == ctx.active_file
    assert result.intent.request.arguments["content"] == "Afferin Dostum"


def test_active_folder_resolution(resolver, ctx):
    result = resolver.resolve(
        "Ayni klasore ikinci.txt olustur ve icine 12345 yaz.",
        ctx,
    )
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "write_file"
    path = result.intent.request.arguments["path"]
    assert path.endswith("ikinci.txt")
    assert str(ctx.active_folder) in path.replace("/", "\\")
    assert result.intent.request.arguments["content"] == "12345"


def test_ambiguous_reference_requires_user(resolver):
    empty = ConversationalContext()
    result = resolver.resolve("Icerigini degistir.", empty)
    assert result.ambiguous
    assert "Hangi dosya" in result.clarification
    assert result.intent is None


def test_completed_mission_context_persists(tmp_path, monkeypatch):
    state_path = tmp_path / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_path)
    monkeypatch.setattr("hermes.client.session_store.ensure_user_dirs", lambda: None)

    ctx = ConversationalContext(
        active_file=r"C:\Users\test\Desktop\HermesContextTest\test.txt",
        last_created_file=r"C:\Users\test\Desktop\HermesContextTest\test.txt",
    )
    ctx.save()

    loaded = ConversationalContext.load()
    assert loaded.active_file == ctx.active_file
    assert loaded.last_created_file == ctx.last_created_file


def test_same_folder_reference(resolver, ctx):
    result = resolver.resolve("Ayni klasore log.txt olustur.", ctx)
    assert not result.ambiguous
    assert result.resolved_references.get("target_folder") == ctx.active_folder
    assert "log.txt" in result.intent.request.arguments["path"]


def test_multiple_files_resolution(resolver, ctx):
    result = resolver.resolve("tarih dosyasini ac", ctx)
    assert not result.ambiguous
    assert result.intent.request.arguments["path"].endswith("tarih.txt")

    result2 = resolver.resolve("bilgisayar dosyasini degistir ve sadece YENI yaz.", ctx)
    assert not result2.ambiguous
    assert "bilgisayar.txt" in result2.intent.request.arguments["path"]
    assert result2.intent.request.arguments["content"] == "YENI"


def test_literal_content_not_confused_with_instruction():
    content = extract_content_modification('Dosyaya "Afferin Dostum...." yaz')
    assert content is not None
    assert "Afferin Dostum" in content
    assert "başka hiçbir şey" not in (content or "").casefold()


def test_expected_outputs_prevent_premature_completion():
    mission = Mission.create("Iki dosya olustur.")
    mission.steps = [
        MissionStep(step_id="write1", title="Write 1", tool_name="write_file", status=MissionStepStatus.COMPLETED),
    ]
    mission.working_context["expected_outputs"] = 2
    mission.working_context["completed_outputs"] = 1
    assert outputs_requirement_met(mission) is False
    assert infer_expected_outputs("Iki dosya olustur.") == 2


@pytest.mark.asyncio
async def test_reference_resolver_does_not_invent_target():
    resolver = ReferenceResolver()
    resolution = resolver.resolve("Icerigini degistir.", ConversationalContext())
    assert resolution.ambiguous
    assert resolution.intent is None
    assert "notlar" not in (resolution.clarification or "").casefold()
    assert "Desktop" not in (resolution.clarification or "")


def test_context_updates_from_write_file():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "write_file",
        {"path": r"C:\Users\test\Desktop\HermesContextTest\test.txt", "size": 12, "verified": True},
        success=True,
    )
    assert ctx.active_file.endswith("test.txt")
    assert ctx.last_created_file.endswith("test.txt")
    assert "HermesContextTest" in (ctx.active_folder or "")


def test_context_updates_from_create_folder():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "create_folder",
        {"path": r"C:\Users\test\Desktop\HermesContextTest", "verified": True},
        success=True,
    )
    assert ctx.active_folder.endswith("HermesContextTest")
    assert ctx.last_created_folder.endswith("HermesContextTest")


@pytest.mark.asyncio
async def test_mission_fails_when_expected_outputs_not_met(registry, mission_root):
    store = MissionStore()
    mission = store.create_mission("Iki dosya olustur.")
    mission.steps = [
        MissionStep(
            step_id="write1",
            title="Write 1",
            tool_name="write_file",
            tool_arguments={"path": "a.txt", "content": "1"},
            status=MissionStepStatus.COMPLETED,
        ),
    ]
    mission.plan_validated = True
    mission.working_context["expected_outputs"] = 2
    mission.working_context["completed_outputs"] = 1
    store.save(mission)

    executor = MagicMock()
    executor._policy.evaluate.return_value.decision = PolicyDecision.ALLOW
    engine = MissionEngine(store, registry, executor)
    result = await engine.run(mission.mission_id, MagicMock())
    assert result.success is False
    assert "tamamlanmadi" in result.summary.casefold()
