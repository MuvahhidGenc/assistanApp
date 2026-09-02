"""Phase 8 — agent context, deictic resolution, mission continuity regression tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.agent.conversation_flow import handle_meta_conversation, is_user_facing_summary
from hermes.agent.mission_continuation import (
    detect_mission_continuation,
    is_mission_continuation_message,
)
from hermes.agent.mission_progress import (
    format_mission_progress,
    format_mission_remaining,
    format_partial_failure_summary,
)
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.agent_context import (
    extract_icine_write_content,
    is_deictic_file_reference,
    is_deictic_folder_reference,
    resolve_context_file,
    resolve_context_folder,
    resolve_file_by_priority,
)
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.store import MissionStore
from hermes.server.models import ToolResultPayload
from hermes.tools.pc_manager import parse_pc_command
from hermes.tools.registry import create_default_registry


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


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
def store(mission_root) -> MissionStore:
    return MissionStore(mission_root)


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    folder = tmp_path / "Desktop"
    folder.mkdir()
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: folder / name,
    )
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda _text: folder,
    )
    return folder


def _make_real_pc_process(desktop: Path):
    registry = create_default_registry()

    async def process(self, command, run_id="", skip_approval=False, *, user_message=""):
        tool_name, arguments = parse_pc_command(command)
        tool = registry.get(tool_name)
        if not tool:
            return ToolResultPayload(tool_call_id="x", success=False, error="unknown")
        exec_result = await tool.execute(**arguments)
        output = dict(exec_result.output) if isinstance(exec_result.output, dict) else {}
        if exec_result.verified:
            output["verified"] = True
        if tool_name == "open_path":
            target = Path(str(arguments.get("path") or ""))
            if target.exists():
                output["verified"] = True
                output["path"] = str(target.resolve())
        if tool_name in ("write_file", "create_folder", "rename_path"):
            output["verified"] = True
        return ToolResultPayload(
            tool_call_id=f"pc-{tool_name}",
            success=bool(exec_result.success),
            output=output,
            error=exec_result.error,
        )

    return process


# --- 1. Context chain / deictic ---


def test_context_chain_bunu(resolver, tmp_path):
    f = tmp_path / "rapor.txt"
    f.write_text("old", encoding="utf-8")
    ctx = ConversationalContext(active_file=str(f.resolve()))
    result = resolver.resolve("Onu aç.", ctx)
    assert not result.ambiguous
    assert result.intent.request.name == "open_path"
    assert result.intent.request.arguments["path"] == str(f.resolve())


def test_context_chain_onu(resolver, tmp_path):
    f = tmp_path / "final.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(
        last_renamed_file=str(f.resolve()),
        active_file=str(f.resolve()),
    )
    result = resolver.resolve("Onu aç.", ctx)
    assert result.intent.request.arguments["path"] == str(f.resolve())


def test_deictic_oraya_folder_reference(tmp_path):
    assert is_deictic_folder_reference("Oraya kopyala")
    folder = tmp_path / "Proje"
    folder.mkdir()
    ctx = ConversationalContext(active_folder=str(folder.resolve()))
    assert resolve_context_folder("Oraya kopyala", ctx) == str(folder.resolve())


def test_deictic_bunu_file_reference(tmp_path):
    assert is_deictic_file_reference("Bunu aç")
    f = tmp_path / "rapor.txt"
    f.write_text("x", encoding="utf-8")
    path = str(f.resolve())
    ctx = ConversationalContext(active_file=path, recent_files=[path])
    resolved = resolve_context_file("Bunu aç", ctx)
    assert resolved is not None
    assert resolved.path == path


def test_last_created_file_parent_directory(resolver, tmp_path):
    folder = tmp_path / "Proje"
    folder.mkdir()
    f = folder / "rapor.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(
        last_created_file=str(f.resolve()),
        active_file=str(f.resolve()),
        active_folder=str(folder.resolve()),
    )
    result = resolver.resolve("Bulunduğu klasörü aç.", ctx)
    assert not result.ambiguous
    assert result.intent.request.name == "open_path"
    assert Path(result.intent.request.arguments["path"]).resolve() == folder.resolve()


def test_olusturdugumuz_klasor(resolver, tmp_path):
    folder = tmp_path / "Proje"
    folder.mkdir()
    ctx = ConversationalContext(
        active_folder=str(folder.resolve()),
        last_created_folder=str(folder.resolve()),
    )
    result = resolver.resolve("Oluşturduğumuz klasörü aç.", ctx)
    assert result.intent.request.arguments["path"] == str(folder.resolve())


def test_son_dosya(resolver, tmp_path):
    f = tmp_path / "rapor.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(last_created_file=str(f.resolve()))
    result = resolver.resolve("Son dosyayı aç.", ctx)
    assert result.intent.request.arguments["path"] == str(f.resolve())


def test_last_opened_file(resolver, tmp_path):
    f = tmp_path / "final.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(
        last_opened_file=str(f.resolve()),
        active_file=str(f.resolve()),
    )
    result = resolver.resolve("Son açılan dosyayı aç.", ctx)
    assert "open_path" in result.intent.request.name or result.intent.request.name == "open_path"


def test_rename_updates_context():
    ctx = ConversationalContext(
        active_file=r"C:\Desktop\Proje\rapor.txt",
        last_created_file=r"C:\Desktop\Proje\rapor.txt",
    )
    ctx.update_from_tool(
        "rename_path",
        {
            "source": r"C:\Desktop\Proje\rapor.txt",
            "destination": r"C:\Desktop\Proje\final.txt",
            "verified": True,
        },
        success=True,
    )
    assert ctx.active_file.endswith("final.txt")
    assert ctx.last_renamed_file.endswith("final.txt")


def test_modify_updates_context():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "write_file",
        {"path": r"C:\Desktop\Proje\rapor.txt", "verified": True},
        success=True,
    )
    assert ctx.last_modified_file.endswith("rapor.txt")
    assert ctx.active_file.endswith("rapor.txt")


def test_icine_write_without_create(resolver, tmp_path):
    folder = tmp_path / "Proje"
    folder.mkdir()
    f = folder / "rapor.txt"
    f.write_text("old", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(f.resolve()),
        last_created_file=str(f.resolve()),
        active_folder=str(folder.resolve()),
    )
    content = extract_icine_write_content("İçine Bugünkü rapor yaz.")
    assert content == "Bugünkü rapor"
    result = resolver.resolve("İçine Bugünkü rapor yaz.", ctx)
    assert not result.ambiguous
    assert result.intent.request.name == "write_file"
    assert result.intent.request.arguments["content"] == "Bugünkü rapor"
    assert result.intent.request.arguments["path"] == str(f.resolve())


# --- Mission continuity ---


def test_mission_continuation_phrase():
    assert is_mission_continuation_message("Bir de önemli olanları ayır.")
    assert is_mission_continuation_message("Özetini de hazırla.")


def test_mission_continuation_detect(store):
    ctx = ConversationalContext()
    mission = store.create_mission("PDF'leri bul ve Raporlar klasörüne kopyala.")
    mission.status = MissionStatus.RUNNING
    store.save(mission)
    ctx.active_mission_id = mission.mission_id
    decision = detect_mission_continuation("Bir de önemli olanları ayır.", ctx, store)
    assert decision.is_continuation
    assert decision.continue_mission_id == mission.mission_id


def test_independent_task_not_continuation(store):
    ctx = ConversationalContext()
    mission = store.create_mission("PDF'leri bul ve Raporlar klasörüne kopyala.")
    mission.status = MissionStatus.RUNNING
    store.save(mission)
    ctx.active_mission_id = mission.mission_id
    decision = detect_mission_continuation("Chrome'u aç.", ctx, store)
    assert not decision.is_continuation


def test_suspended_mission_resume(store):
    ctx = ConversationalContext()
    mission = store.create_mission("PDF arama gorevi")
    store.suspend_mission(mission.mission_id, reason="interrupt")
    ctx.suspended_mission_ids = [mission.mission_id]
    decision = detect_mission_continuation("Özetini de hazırla.", ctx, store)
    assert decision.is_continuation


# --- Meta conversation ---


def test_ne_yaptin_natural(store):
    ctx = ConversationalContext()
    ctx.record_verified_action("Proje klasorunu olusturdum")
    ctx.record_verified_action("rapor.txt dosyasini olusturdum")
    turn = handle_meta_conversation("Ne yaptın?", ctx, store)
    assert turn.handled
    assert "Proje" in turn.response
    assert "write_file" not in turn.response


def test_neredeyiz_with_mission(store):
    ctx = ConversationalContext()
    mission = store.create_mission("PDF'leri Raporlar klasörüne kopyala.")
    mission.steps = [
        MissionStep(step_id="s1", title="PDF ara", status=MissionStepStatus.COMPLETED),
        MissionStep(step_id="s2", title="Kopyala", status=MissionStepStatus.PENDING),
    ]
    mission.status = MissionStatus.RUNNING
    store.save(mission)
    ctx.active_mission_id = mission.mission_id
    turn = handle_meta_conversation("Neredeyiz?", ctx, store)
    assert turn.handled
    assert "devam" in turn.response.casefold() or "adim" in turn.response.casefold()


def test_ne_kaldi(store):
    ctx = ConversationalContext()
    mission = store.create_mission("Dosya kopyalama")
    mission.steps = [
        MissionStep(step_id="s1", title="Arama tamamlandi", status=MissionStepStatus.COMPLETED),
        MissionStep(
            step_id="s2",
            title="Kopyalama",
            status=MissionStepStatus.PENDING,
            tool_name="copy_file",
        ),
    ]
    mission.status = MissionStatus.RUNNING
    store.save(mission)
    ctx.active_mission_id = mission.mission_id
    turn = handle_meta_conversation("Ne kaldı?", ctx, store)
    assert turn.handled
    assert "Kalan" in turn.response or "kopyalama" in turn.response.casefold()


def test_user_correction_previous_file():
    ctx = ConversationalContext(
        recent_files=[r"C:\a\final.txt", r"C:\a\rapor.txt"],
        active_file=r"C:\a\final.txt",
    )
    turn = handle_meta_conversation("Diğer dosyayı.", ctx)
    assert turn.handled
    assert turn.repeat_message
    assert "rapor.txt" in turn.repeat_message


def test_user_correction_wrong_folder():
    ctx = ConversationalContext(
        recent_folders=[r"C:\Desktop\Proje", r"C:\Desktop\Yanlis"],
        active_folder=r"C:\Desktop\Yanlis",
    )
    turn = handle_meta_conversation("Yanlış klasör.", ctx)
    assert turn.handled
    assert "Proje" in turn.response


def test_partial_failure_summary(store):
    mission = store.create_mission("Toplu kopyalama")
    mission.status = MissionStatus.FAILED
    mission.steps = [
        MissionStep(step_id="s1", title="Arama", status=MissionStepStatus.COMPLETED),
        MissionStep(
            step_id="s2",
            title="Kopyalama",
            status=MissionStepStatus.FAILED,
            tool_name="copy_file",
            result_summary="2 dosyada hata",
        ),
    ]
    mission.working_context["copy_results"] = {"copied_count": 18, "failed_count": 2}
    summary = format_partial_failure_summary(mission)
    assert "18" in summary
    assert "2" in summary


# --- Persistence ---


def test_context_persistence_fields():
    ctx = ConversationalContext(
        current_objective="Proje klasoru olustur",
        last_successful_mission_id="m-123",
    )
    ctx.record_structured_action("create", "Proje klasorunu olusturdum", path=r"C:\Desktop\Proje")
    data = ctx.to_dict()
    restored = ConversationalContext.from_dict(data)
    assert restored.current_objective == "Proje klasoru olustur"
    assert restored.last_successful_mission_id == "m-123"
    assert len(restored.recent_actions) == 1


def test_reconcile_stale_paths(tmp_path):
    missing = tmp_path / "gone.txt"
    ctx = ConversationalContext(
        active_file=str(missing),
        recent_files=[str(missing)],
    )
    ctx.reconcile_with_filesystem()
    assert ctx.active_file is None
    assert ctx.recent_files == []


def test_mission_persistence_roundtrip(store):
    mission = store.create_mission("Test gorevi")
    mission.working_context["natural_summary"] = "18 dosyayi kopyaladim."
    mission.status = MissionStatus.COMPLETED
    store.save(mission)
    loaded = store.load(mission.mission_id)
    assert loaded is not None
    assert loaded.working_context["natural_summary"] == "18 dosyayi kopyaladim."


def test_file_priority_order(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(a.resolve()),
        last_created_file=str(b.resolve()),
        last_verified_file=str(b.resolve()),
    )
    resolved = resolve_file_by_priority(ctx)
    assert resolved.path == str(b.resolve())
    deictic = resolve_file_by_priority(ctx, text="Onu aç")
    assert deictic.path == str(b.resolve())


def test_ambiguity_multiple_files(resolver, tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("1", encoding="utf-8")
    f2.write_text("2", encoding="utf-8")
    ctx = ConversationalContext(
        recent_files=[str(f1.resolve()), str(f2.resolve())],
    )
    result = resolver.resolve("Bunu aç.", ctx)
    assert result.ambiguous or result.intent is not None


def test_nonexistent_file_clarification(resolver, tmp_path):
    ctx = ConversationalContext(active_file=str(tmp_path / "missing.txt"))
    result = resolver.resolve("Onu aç.", ctx)
    assert result.ambiguous
    assert "?" in result.clarification or "bulunamadi" in result.clarification.casefold()


@pytest.mark.asyncio
async def test_phase8_proje_e2e_flow(settings, desktop, monkeypatch):
    """Full Proje/rapor.txt context chain E2E."""
    ctx = ConversationalContext()
    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)
    settings.sessions.record_sessions = False

    monkeypatch.setattr(
        "hermes.tools.pc_manager.PCManager.process",
        _make_real_pc_process(desktop),
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.save",
        lambda self: None,
    )
    monkeypatch.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)
    monkeypatch.setattr("hermes.agent.server_tasks.should_defer_to_server", lambda _m: False)

    steps = [
        "Desktop'ta Proje klasörü oluştur.",
        "İçine rapor.txt oluştur.",
        "İçine Bugünkü rapor yaz.",
        "Onu aç.",
        "Bulunduğu klasörü aç.",
        "Adını final.txt yap.",
        "Tekrar aç.",
    ]

    for message in steps:
        response = await orchestrator.process_message(message)
        assert "?" not in response or "dogrulayamadim" in response.casefold()

    proje = desktop / "Proje"
    final_path = proje / "final.txt"
    assert proje.is_dir()
    assert final_path.exists()
    assert final_path.read_text(encoding="utf-8") == "Bugünkü rapor"
    assert ctx.active_file and Path(ctx.active_file).name == "final.txt"
    assert ctx.active_folder and Path(ctx.active_folder).name == "Proje"

    content_turn = handle_meta_conversation("İçinde ne yazıyor?", ctx)
    assert content_turn.handled
    assert "Bugünkü rapor" in content_turn.response

    summary_turn = handle_meta_conversation("Ne yaptın?", ctx)
    assert summary_turn.handled
    assert is_user_facing_summary(summary_turn.response)


@pytest.mark.asyncio
async def test_phase8_test_folder_chain(settings, desktop, monkeypatch):
    """Desktop Test klasörü — numaralı dosyalar zinciri."""
    ctx = ConversationalContext()
    server = MagicMock()
    orchestrator = AgentOrchestrator(settings, server)

    monkeypatch.setattr(
        "hermes.tools.pc_manager.PCManager.process",
        _make_real_pc_process(desktop),
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.save",
        lambda self: None,
    )
    monkeypatch.setattr("hermes.client.session_store.append_conversation_turn", lambda *a, **k: None)

    await orchestrator.process_message("Desktop'ta Test klasörü oluştur.")
    test_dir = desktop / "Test"
    assert test_dir.is_dir()

    await orchestrator.process_message("İçine test1.txt oluştur.")
    await orchestrator.process_message("İçine test2.txt oluştur.")
    await orchestrator.process_message("İçine test3.txt oluştur.")

    assert (test_dir / "test1.txt").exists()
    assert (test_dir / "test3.txt").exists()
    assert ctx.active_folder and "Test" in ctx.active_folder

    await orchestrator.process_message("Son oluşturduğun dosyayı aç.")
    assert ctx.last_opened_file and Path(ctx.last_opened_file).name == "test3.txt"

    await orchestrator.process_message("Adını final.txt yap.")
    assert (test_dir / "final.txt").exists()
    assert not (test_dir / "test3.txt").exists()

    await orchestrator.process_message("Bulunduğu klasörü aç.")
    assert ctx.active_folder and Path(ctx.active_folder).resolve() == test_dir.resolve()
