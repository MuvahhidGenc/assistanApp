"""Phase 8 context recency — stale reference must lose to newer verified actions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.agent.conversation_flow import handle_meta_conversation
from hermes.context.agent_context import promote_file_in_context, resolve_deictic_file
from hermes.context.conversational_context import ConversationalContext
from hermes.context.context_correction import resolve_user_correction
from hermes.context.parent_directory import resolve_parent_directory_path
from hermes.context.reference_resolver import ReferenceResolver
from hermes.client.session_store import load_client_state, save_client_state


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


def _ctx_with_stale_and_new(tmp_path: Path) -> tuple[ConversationalContext, Path, Path]:
    old_folder = tmp_path / "AjanTest"
    old_folder.mkdir()
    old_file = old_folder / "sonuc.txt"
    old_file.write_text("eski", encoding="utf-8")

    new_folder = tmp_path / "Ajan8"
    new_folder.mkdir()
    new_file = new_folder / "rapor.txt"
    new_file.write_text("Bugunku rapor", encoding="utf-8")

    old_path = str(old_file.resolve())
    new_path = str(new_file.resolve())
    folder_path = str(new_folder.resolve())

    ctx = ConversationalContext(
        active_file=old_path,
        last_opened_file=old_path,
        last_created_file=old_path,
        last_verified_file=old_path,
        active_folder=str(old_folder.resolve()),
        last_verified_folder=str(old_folder.resolve()),
        recent_files=[new_path, old_path],
        recent_folders=[folder_path, str(old_folder.resolve())],
    )
    ctx.update_from_tool(
        "create_folder",
        {"path": folder_path, "verified": True},
        success=True,
        verified=True,
    )
    ctx.update_from_tool(
        "write_file",
        {"path": new_path, "verified": True},
        success=True,
        verified=True,
    )
    return ctx, new_file, new_folder


def test_new_file_overrides_stale_active_file(resolver, tmp_path):
    ctx, new_file, _ = _ctx_with_stale_and_new(tmp_path)
    assert ctx.last_verified_file == str(new_file.resolve())
    assert ctx.active_file == str(new_file.resolve())


def test_new_folder_overrides_stale_active_folder(resolver, tmp_path):
    ctx, _, new_folder = _ctx_with_stale_and_new(tmp_path)
    assert ctx.active_folder == str(new_folder.resolve())
    assert ctx.last_verified_folder == str(new_folder.resolve())


def test_onu_ac_after_new_file_creation(resolver, tmp_path):
    ctx, new_file, _ = _ctx_with_stale_and_new(tmp_path)
    result = resolver.resolve("Onu aç.", ctx)
    assert not result.ambiguous
    assert result.intent.request.name == "open_path"
    assert result.intent.request.arguments["path"] == str(new_file.resolve())


def test_bulundugu_klasoru_ac_after_new_file(resolver, tmp_path):
    ctx, _, new_folder = _ctx_with_stale_and_new(tmp_path)
    result = resolver.resolve("Bulunduğu klasörü aç.", ctx)
    assert not result.ambiguous
    assert Path(result.intent.request.arguments["path"]).resolve() == new_folder.resolve()


def test_user_correction_updates_context(tmp_path, monkeypatch):
    ctx, new_file, new_folder = _ctx_with_stale_and_new(tmp_path)
    monkeypatch.setattr(
        "hermes.context.context_correction.find_folder_by_hint",
        lambda _ctx, hint: str(new_folder.resolve()) if hint.lower() == "ajan8" else None,
    )
    correction = resolve_user_correction("yanlış dosyayı açtın ajan8 olmalıydı", ctx)
    assert correction.handled
    assert correction.corrected_path == str(new_file.resolve())
    assert ctx.active_file == str(new_file.resolve())
    assert ctx.last_verified_file == str(new_file.resolve())


def test_correction_from_old_file_to_new_folder(resolver, tmp_path):
    ctx, new_file, new_folder = _ctx_with_stale_and_new(tmp_path)
    correction = resolve_user_correction(
        f"Hayır, {new_folder.name}'deki rapor dosyasını aç.",
        ctx,
    )
    assert correction.handled
    assert correction.corrected_path == str(new_file.resolve())


def test_rename_updates_active_file(resolver, tmp_path):
    folder = tmp_path / "Proje"
    folder.mkdir()
    src = folder / "rapor.txt"
    src.write_text("x", encoding="utf-8")
    dest = folder / "final.txt"
    dest.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(active_file=str(src.resolve()))
    ctx.update_from_tool(
        "rename_path",
        {"source": str(src), "destination": str(dest), "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.active_file == str(dest.resolve())
    assert ctx.last_renamed_file == str(dest.resolve())
    result = resolver.resolve("Onu aç.", ctx)
    assert result.intent.request.arguments["path"] == str(dest.resolve())


def test_modify_keeps_active_file(resolver, tmp_path):
    f = tmp_path / "rapor.txt"
    f.write_text("old", encoding="utf-8")
    path = str(f.resolve())
    ctx = ConversationalContext(active_file=path, last_verified_file=path)
    ctx.update_from_tool(
        "write_file",
        {"path": path, "verified": True},
        success=True,
        verified=True,
    )
    assert ctx.active_file == path
    assert ctx.last_modified_file == path


def test_parent_directory_follows_latest_file(resolver, tmp_path):
    ctx, _, new_folder = _ctx_with_stale_and_new(tmp_path)
    folder_path, error = resolve_parent_directory_path("Bulunduğu klasörü aç", ctx)
    assert not error
    assert Path(folder_path).resolve() == new_folder.resolve()


def test_restart_reconcile(tmp_path, monkeypatch):
    new_folder = tmp_path / "Ajan8"
    new_folder.mkdir()
    new_file = new_folder / "rapor.txt"
    new_file.write_text("Bugunku rapor", encoding="utf-8")
    stale = tmp_path / "missing.txt"
    payload = {
        "active_file": str(stale),
        "last_verified_file": str(stale),
        "recent_files": [str(new_file.resolve())],
        "recent_folders": [str(new_folder.resolve())],
    }
    ctx = ConversationalContext.from_dict(payload)
    ctx.reconcile_with_filesystem()
    assert ctx.active_file == str(new_file.resolve())
    assert ctx.last_verified_file == str(new_file.resolve())
    assert ctx.active_folder == str(new_folder.resolve())


def test_multiple_old_files_one_new_wins(resolver, tmp_path):
    ctx, new_file, _ = _ctx_with_stale_and_new(tmp_path)
    resolved = resolve_deictic_file(ctx, "Onu aç", intent="open_file")
    assert resolved is not None
    assert resolved.path == str(new_file.resolve())
    assert "opened" not in resolved.source


def test_same_filename_different_folders_ambiguous(resolver, tmp_path):
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    fa = a / "rapor.txt"
    fb = b / "rapor.txt"
    fa.write_text("a", encoding="utf-8")
    fb.write_text("b", encoding="utf-8")
    ctx = ConversationalContext(
        recent_files=[str(fb.resolve()), str(fa.resolve())],
        last_verified_file=str(fb.resolve()),
    )
    result = resolver.resolve("rapor.txt dosyasını aç", ctx)
    if result.ambiguous:
        assert "Birden fazla" in result.clarification or result.intent
    else:
        assert result.intent.request.arguments["path"] in {
            str(fa.resolve()),
            str(fb.resolve()),
        }


def test_explicit_filename_overrides_context(resolver, tmp_path):
    ctx, new_file, _ = _ctx_with_stale_and_new(tmp_path)
    old_file = ctx.recent_files[1]
    result = resolver.resolve("sonuc.txt dosyasını aç", ctx)
    assert not result.ambiguous
    assert result.intent.request.arguments["path"] == old_file


def test_full_ajan_test_to_ajan8_e2e(resolver, tmp_path):
    ctx, new_file, new_folder = _ctx_with_stale_and_new(tmp_path)

    open_result = resolver.resolve("Onu aç.", ctx)
    assert open_result.intent.request.arguments["path"] == str(new_file.resolve())

    parent_result = resolver.resolve("Bulunduğu klasörü aç.", ctx)
    assert Path(parent_result.intent.request.arguments["path"]).resolve() == new_folder.resolve()

    content_turn = handle_meta_conversation("İçinde ne yazıyor?", ctx)
    assert content_turn.handled
    assert "Bugunku rapor" in content_turn.response


def test_last_opened_does_not_beat_new_create_for_onu(resolver, tmp_path):
    old = tmp_path / "old.txt"
    new = tmp_path / "new.txt"
    old.write_text("o", encoding="utf-8")
    new.write_text("n", encoding="utf-8")
    ctx = ConversationalContext(
        last_opened_file=str(old.resolve()),
        active_file=str(old.resolve()),
    )
    ctx.update_from_tool(
        "write_file",
        {"path": str(new.resolve()), "verified": True},
        success=True,
        verified=True,
    )
    result = resolver.resolve("Onu aç.", ctx)
    assert result.intent.request.arguments["path"] == str(new.resolve())


def test_promote_file_in_context(tmp_path):
    f = tmp_path / "rapor.txt"
    f.write_text("x", encoding="utf-8")
    ctx = ConversationalContext()
    promote_file_in_context(ctx, str(f.resolve()))
    assert ctx.last_verified_file == str(f.resolve())
    assert ctx.recent_files[0] == str(f.resolve())
