"""active_focus + correction/replan: stale slots must not steal the target."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes.agent.local_intent import guess_file_action
from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.settings import AppSettings
from hermes.context.context_correction import resolve_user_correction
from hermes.context.conversational_context import ConversationalContext
from hermes.context.reference_resolver import ReferenceResolver
from hermes.intent.models import AgentIntent
from hermes.intent.router import build_input_pool
from hermes.screen.classify import entities_for_hints
from hermes.screen.models import BoundingBox, ScreenEntity, ScreenState
from hermes.screen.reference import extract_reference_features
from hermes.screen.resolve import resolve_screen_reference
from hermes.server.models import ToolResultPayload


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


def _patch_session(monkeypatch, ctx: ConversationalContext) -> None:
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.load",
        lambda: ctx,
    )
    monkeypatch.setattr(
        "hermes.context.conversational_context.ConversationalContext.save",
        lambda self: None,
    )
    monkeypatch.setattr(
        "hermes.client.session_store.append_conversation_turn",
        lambda *a, **k: None,
    )


def _orchestrator(settings: AppSettings) -> AgentOrchestrator:
    return AgentOrchestrator(settings, MagicMock())


def _stale_file(tmp_path: Path) -> Path:
    old_dir = tmp_path / "Deneme2026"
    old_dir.mkdir()
    old = old_dir / "test (2).txt"
    old.write_text("eski", encoding="utf-8")
    return old


def _seed_old_file_context(tmp_path: Path) -> tuple[ConversationalContext, Path]:
    old = _stale_file(tmp_path)
    ctx = ConversationalContext(
        active_file=str(old.resolve()),
        last_created_file=str(old.resolve()),
        last_verified_file=str(old.resolve()),
        recent_files=[str(old.resolve())],
        active_folder=str(old.parent.resolve()),
        last_created_folder=str(old.parent.resolve()),
    )
    ctx.commit_focus("file", str(old.resolve()), container=str(old.parent.resolve()), source="stale")
    return ctx, old


def _entity(entity_id: str, text: str, x: int, y: int, w: int = 80, h: int = 20) -> ScreenEntity:
    return ScreenEntity(
        id=entity_id,
        type="text_line",
        role="unknown",
        text=text,
        bbox=BoundingBox(x, y, w, h),
        confidence=0.9,
        source="ocr",
    )


def _video_state() -> ScreenState:
    return ScreenState(
        screenshot={"width": 400, "height": 400, "path": "mem.png"},
        window={"title": "YouTube", "width": 400, "height": 400},
        entities=[
            _entity("chrome_0", "»", 8, 4, w=16, h=12),
            _entity("bar", "— » YouTube Ara", 8, 6, w=360, h=14),
            _entity("se_0", "Gunun ozeti", 40, 180),
            _entity("se_1", "Teknoloji ve Yapay Zeka", 160, 180),
            _entity("se_2", "Muzik listesi", 280, 180),
        ],
        text="Gunun ozeti Teknoloji ve Yapay Zeka Muzik listesi",
    )


def test_resolved_references_does_not_dump_stale_file_as_target(tmp_path):
    ctx, old = _seed_old_file_context(tmp_path)
    folder = tmp_path / "test12"
    folder.mkdir()
    ctx.update_from_tool(
        "create_folder",
        {"path": str(folder.resolve()), "verified": True},
        success=True,
        verified=True,
    )

    refs = ctx.resolved_references("Icine Word dosyasi olusturup tevhid ile ilgili makale yaz")
    assert refs.get("target_folder") == str(folder.resolve())
    assert refs.get("target_file") != str(old.resolve())
    assert "test (2).txt" not in (refs.get("target_file") or "")


def test_file_focus_icine_uses_parent_folder(tmp_path):
    folder = tmp_path / "test12"
    folder.mkdir()
    created = folder / "not.txt"
    created.write_text("x", encoding="utf-8")
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "write_file",
        {"path": str(created.resolve()), "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.active_focus is not None
    assert ctx.active_focus.type == "file"
    assert ctx.focus_container() == str(folder.resolve())

    refs = ctx.resolved_references("icine Word dosyasi olustur")
    assert refs["target_folder"] == str(folder.resolve())
    assert "target_file" not in refs


@pytest.mark.asyncio
async def test_process_message_create_folder_then_file_inside(
    settings: AppSettings, tmp_path, monkeypatch
):
    folder = tmp_path / "test12"
    old = _stale_file(tmp_path)
    ctx = ConversationalContext(
        active_file=str(old.resolve()),
        last_created_file=str(old.resolve()),
        recent_files=[str(old.resolve())],
    )
    written: list[str] = []

    async def fake_execute(local_call, run_id, *, user_message=""):
        name = getattr(local_call, "name", None) or (
            local_call.get("name") if isinstance(local_call, dict) else ""
        )
        args = getattr(local_call, "arguments", None) or (
            local_call.get("arguments") if isinstance(local_call, dict) else {}
        )
        path = Path(str(args.get("path") or ""))
        if name == "create_folder":
            path.mkdir(parents=True, exist_ok=True)
            return ToolResultPayload(
                tool_call_id="1",
                success=True,
                output={"path": str(path), "verified": True},
            )
        if name in {"create_word_document", "write_file"}:
            written.append(str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(args.get("content") or "ok"), encoding="utf-8")
            return ToolResultPayload(
                tool_call_id="2",
                success=True,
                output={"path": str(path), "verified": True},
            )
        return ToolResultPayload(tool_call_id="x", success=False, error=name)

    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_desktop_folder_path",
        lambda name: (tmp_path / str(name).strip()).resolve(),
    )
    monkeypatch.setattr(
        "hermes.context.goal_resolution.extract_location_path",
        lambda text: tmp_path,
    )
    monkeypatch.setattr(
        "hermes.context.folder_reference.resolve_create_folder_path",
        lambda text: str((tmp_path / "test12").resolve()) if "test12" in (text or "") else None,
    )
    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._execute_local_tool = fake_execute  # type: ignore[method-assign]

    await orch.process_message("Masaustune test12 adli klasor olustur")
    assert ctx.active_focus is not None
    assert ctx.active_focus.type == "folder"
    assert Path(ctx.active_focus.identifier).name == "test12"

    await orch.process_message("Icine Word dosyasi olusturup tevhid ile ilgili makale yaz")
    assert written
    target = Path(written[-1])
    assert target.parent.resolve() == folder.resolve()
    assert "test (2).txt" not in str(target)
    assert "Deneme2026" not in str(target)


@pytest.mark.asyncio
async def test_old_file_must_not_steal_inside_create(
    settings: AppSettings, tmp_path, monkeypatch
):
    ctx, old = _seed_old_file_context(tmp_path)
    folder = tmp_path / "test12"
    folder.mkdir()
    ctx.update_from_tool(
        "create_folder",
        {"path": str(folder.resolve()), "verified": True},
        success=True,
        verified=True,
    )
    _patch_session(monkeypatch, ctx)

    intent = guess_file_action(
        "Icine Word dosyasi olusturup tevhid ile ilgili makale yaz",
        resolved_references=ctx.resolved_references(
            "Icine Word dosyasi olusturup tevhid ile ilgili makale yaz"
        ),
        conv_ctx=ctx,
    )
    assert intent is not None
    path = Path(str(intent.request.arguments["path"]))
    assert path.parent.resolve() == folder.resolve()
    assert path.resolve() != old.resolve()
    assert path.name.endswith(".docx")

    written: list[str] = []

    async def fake_execute(local_call, run_id, *, user_message=""):
        args = getattr(local_call, "arguments", None) or {}
        path = Path(str(args.get("path") or ""))
        written.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"path": str(path), "verified": True},
        )

    orch = _orchestrator(settings)
    orch._execute_local_tool = fake_execute  # type: ignore[method-assign]
    text = await orch.process_message(
        "Icine Word dosyasi olusturup tevhid ile ilgili makale yaz"
    )
    assert written
    assert Path(written[-1]).parent.resolve() == folder.resolve()
    assert "test (2).txt" not in text
    assert "write_file" not in text.casefold() or "test12" in str(written[-1])


def test_correction_updates_target_and_replans(tmp_path):
    ctx, old = _seed_old_file_context(tmp_path)
    folder = tmp_path / "test12"
    folder.mkdir()
    ctx.last_created_folder = str(folder.resolve())
    ctx.recent_folders = [str(folder.resolve()), str(old.parent.resolve())]
    ctx.current_objective = "Icine Word dosyasi olusturup tevhid ile ilgili makale yaz"
    ctx.last_intent = {
        "goal": "Icine Word dosyasi olusturup tevhid ile ilgili makale yaz",
        "required_capabilities": ["document.create"],
    }

    result = resolve_user_correction(
        "Hayir test 12 klasoru olusturduk onun icine olusturmalisin",
        ctx,
    )
    assert result.handled
    assert result.repeat_message
    assert "Word" in result.repeat_message or "tevhid" in result.repeat_message.casefold()
    assert result.corrected_path
    assert Path(result.corrected_path).name == "test12"
    assert ctx.focus_container() == str(folder.resolve())
    assert ctx.is_invalidated(str(old.resolve()))
    refs = ctx.resolved_references(result.repeat_message)
    assert refs.get("target_folder") == str(folder.resolve())
    assert refs.get("target_file") != str(old.resolve())


@pytest.mark.asyncio
async def test_process_message_correction_replans_into_folder(
    settings: AppSettings, tmp_path, monkeypatch
):
    ctx, old = _seed_old_file_context(tmp_path)
    folder = tmp_path / "test12"
    folder.mkdir()
    ctx.last_created_folder = str(folder.resolve())
    ctx.recent_folders = [str(folder.resolve())]
    ctx.current_objective = "Icine Word dosyasi olusturup tevhid ile ilgili makale yaz"
    written: list[str] = []

    async def fake_execute(local_call, run_id, *, user_message=""):
        args = getattr(local_call, "arguments", None) or {}
        path = Path(str(args.get("path") or ""))
        written.append(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        return ToolResultPayload(
            tool_call_id="1",
            success=True,
            output={"path": str(path), "verified": True},
        )

    _patch_session(monkeypatch, ctx)
    orch = _orchestrator(settings)
    orch._execute_local_tool = fake_execute  # type: ignore[method-assign]
    text = await orch.process_message(
        "Hayir test 12 klasoru olusturduk onun icine olusturmalisin"
    )
    assert written
    assert Path(written[-1]).parent.resolve() == folder.resolve()
    assert Path(written[-1]).resolve() != old.resolve()
    assert "onay" not in text.casefold()
    assert "ne yazmami" not in text.casefold()


def test_screen_first_and_second_video_use_video_pool_only():
    state = _video_state()
    first_features = extract_reference_features("ekranda gordugun ilk videoyu ac")
    second_features = extract_reference_features("ekranda gordugun ikinci videoyu ac")
    assert "video" in first_features.type_hints
    assert "video" in second_features.type_hints

    pool = entities_for_hints(state, first_features.type_hints)
    pool_ids = {item.id for item in pool}
    assert "chrome_0" not in pool_ids
    assert "bar" not in pool_ids
    assert pool_ids & {"se_0", "se_1", "se_2"}

    first = resolve_screen_reference(state, "ekranda gordugun ilk videoyu ac")
    second = resolve_screen_reference(state, "ekranda gordugun ikinci videoyu ac")
    assert first.chosen
    assert second.chosen
    assert first.chosen[0] not in {"chrome_0", "bar"}
    assert second.chosen[0] not in {"chrome_0", "bar"}
    assert first.chosen[0] != second.chosen[0]


def test_unknown_click_does_not_commit_focus():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "resolve_screen_entity",
        {"entity_id": "se_0", "text": "Gunun ozeti", "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.active_focus is None or ctx.active_focus.type != "screen_entity"
    assert ctx.last_screen_entity_id is None

    ctx.update_from_tool(
        "click",
        {
            "entity_id": "se_0",
            "state_id": "state-a",
            "text": "Gunun ozeti",
            "verification_status": "unknown",
        },
        success=True,
        verified=False,
    )
    assert ctx.active_focus is None or ctx.active_focus.identifier != "se_0"
    assert ctx.last_screen_entity_id != "se_0"


def test_verified_screen_click_commits_focus():
    ctx = ConversationalContext()
    ctx.update_from_tool(
        "click",
        {
            "entity_id": "se_1",
            "state_id": "state-b",
            "text": "Teknoloji",
            "verified": True,
        },
        success=True,
        verified=True,
    )
    assert ctx.active_focus is not None
    assert ctx.active_focus.type == "screen_entity"
    assert ctx.active_focus.identifier == "se_1"
    assert ctx.active_focus.state_id == "state-b"
    assert ctx.last_screen_entity_id == "se_1"
    refs = ctx.resolved_references("az once actigimiz video")
    assert refs.get("session_entity_id") == "se_1"


def test_old_recent_entity_does_not_override_active_focus(tmp_path):
    old = _stale_file(tmp_path)
    folder = tmp_path / "test12"
    folder.mkdir()
    ctx = ConversationalContext(
        recent_files=[str(old.resolve())],
        last_created_file=str(old.resolve()),
        active_file=str(old.resolve()),
    )
    ctx.commit_focus("folder", str(folder.resolve()), container=str(folder.resolve()), source="create")

    refs = ctx.resolved_references("icine word dosyasi olustur")
    assert refs.get("target_folder") == str(folder.resolve())
    assert refs.get("target_file") != str(old.resolve())

    resolver = ReferenceResolver()
    result = resolver.resolve("Ayni klasore not.txt olustur ve icine merhaba yaz.", ctx)
    assert result.intent is not None
    path = Path(result.intent.request.arguments["path"])
    assert path.parent.resolve() == folder.resolve()
    assert path.name == "not.txt"


def test_new_task_does_not_inherit_unrelated_old_target_file(tmp_path):
    old = _stale_file(tmp_path)
    ctx = ConversationalContext(
        active_file=str(old.resolve()),
        last_created_file=str(old.resolve()),
        recent_files=[str(old.resolve())],
    )
    refs = ctx.resolved_references("Masaustune test12 adli klasor olustur")
    assert refs.get("target_file") is None

    pool = build_input_pool(
        AgentIntent.from_dict(
            {
                "goal": "Masaustune test12 adli klasor olustur",
                "required_capabilities": ["filesystem.create_directory"],
                "references": {},
                "confidence": 0.9,
            }
        ),
        ctx,
    )
    assert pool.get("path") != str(old.resolve())
    assert pool.get("path") is None
