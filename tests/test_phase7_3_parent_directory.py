"""Phase 7.3 — Parent directory reference resolution tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from hermes.context.conversational_context import ConversationalContext
from hermes.context.goal_resolution import extract_named_folder_name, parse_open_folder_goal
from hermes.context.parent_directory import (
    is_parent_directory_open_message,
    is_pseudo_folder_name,
    resolve_parent_directory_path,
)
from hermes.context.reference_resolver import ReferenceResolver
from hermes.tools.manifest import extract_url_hint


@pytest.fixture
def resolver():
    return ReferenceResolver()


def _ctx_with_file(tmp_path: Path, name: str = "sonuc.txt") -> ConversationalContext:
    folder = tmp_path / "AjanTest"
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / name
    file_path.write_text("Birinci dosya", encoding="utf-8")
    ctx = ConversationalContext()
    ctx.active_file = str(file_path)
    ctx.last_renamed_file = str(file_path)
    ctx.last_created_file = str(file_path)
    ctx.last_opened_file = str(file_path)
    ctx.created_files = [str(file_path)]
    ctx.recent_files = [str(file_path)]
    ctx.active_folder = str(folder)
    return ctx


@pytest.mark.parametrize(
    "message",
    [
        "sonuc.txt'nin bulunduğu klasörü aç",
        "sonuc.txt'nin olduğu klasörü aç",
        "sonuc.txt dosyasının bulunduğu klasörü aç",
        "sonuc dosyasının bulunduğu klasörü aç",
        "bu dosyanın bulunduğu klasörü aç",
        "bu dosyanın olduğu klasörü aç",
        "son oluşturduğun dosyanın bulunduğu klasörü aç",
        "son olusturdugun dosyanin oldugu klasoru ac",
        "bulunduğu klasörü aç",
        "Bulundukları Klasörü Aç",
        "dosyanın klasörünü aç",
        "bunun bulunduğu klasörü aç",
    ],
)
def test_parent_directory_intent_detected(message: str):
    assert is_parent_directory_open_message(message) is True


def test_pseudo_folder_names_not_literal(resolver):
    assert is_pseudo_folder_name("bulunduğu") is True
    assert is_pseudo_folder_name("bulundukları") is True
    assert extract_named_folder_name("Bulundukları Klasörü Aç") is None
    assert parse_open_folder_goal("Bulundukları Klasörü Aç") is None


def test_explicit_file_parent_directory(resolver, tmp_path):
    ctx = _ctx_with_file(tmp_path)
    result = resolver.resolve("sonuc.txt'nin bulunduğu klasörü aç", ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.name == "open_path"
    assert Path(result.intent.request.arguments["path"]).name == "AjanTest"


def test_active_file_parent_directory(resolver, tmp_path):
    ctx = _ctx_with_file(tmp_path)
    ctx.last_renamed_file = None
    ctx.last_created_file = None
    result = resolver.resolve("bulunduğu klasörü aç", ctx)
    assert result.intent.request.arguments["path"] == str((tmp_path / "AjanTest").resolve())


def test_last_created_file_parent_directory(resolver, tmp_path):
    folder = tmp_path / "CreatedHere"
    folder.mkdir()
    file_path = folder / "created.txt"
    file_path.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(tmp_path / "other.txt"),
        last_created_file=str(file_path),
    )
    (tmp_path / "other.txt").write_text("y", encoding="utf-8")
    result = resolver.resolve("son olusturdugun dosyanin bulundugu klasoru ac", ctx)
    assert Path(result.intent.request.arguments["path"]).resolve() == folder.resolve()


def test_last_renamed_file_parent_directory(resolver, tmp_path):
    ctx = _ctx_with_file(tmp_path, "sonuc.txt")
    ctx.active_file = str(tmp_path / "stale.txt")
    (tmp_path / "stale.txt").write_text("z", encoding="utf-8")
    result = resolver.resolve("sonuc.txt dosyasının bulunduğu klasörü aç", ctx)
    assert Path(result.intent.request.arguments["path"]).name == "AjanTest"


def test_last_opened_file_parent_directory(resolver, tmp_path):
    folder = tmp_path / "OpenedHere"
    folder.mkdir()
    opened = folder / "opened.txt"
    opened.write_text("x", encoding="utf-8")
    ctx = ConversationalContext(
        active_file=str(opened),
        last_opened_file=str(opened),
    )
    result = resolver.resolve("bulunduğu klasörü aç", ctx)
    assert Path(result.intent.request.arguments["path"]).resolve() == folder.resolve()


def test_deictic_bu_dosyanin_bulundugu(resolver, tmp_path):
    ctx = _ctx_with_file(tmp_path)
    result = resolver.resolve("bu dosyanın bulunduğu klasörü aç", ctx)
    assert Path(result.intent.request.arguments["path"]).name == "AjanTest"


def test_deictic_bunun_bulundugu(resolver, tmp_path):
    ctx = _ctx_with_file(tmp_path)
    result = resolver.resolve("bunun bulunduğu klasörü aç", ctx)
    assert Path(result.intent.request.arguments["path"]).name == "AjanTest"


@pytest.mark.parametrize(
    "message",
    [
        "sonuc.txt'nin bulunduğu klasörü aç",
        "sonuc dosyasının olduğu klasörü aç",
        "sonuc.txt dosyasının bulunduğu klasörü aç",
    ],
)
def test_turkish_inflection_variations(resolver, tmp_path, message: str):
    ctx = _ctx_with_file(tmp_path)
    result = resolver.resolve(message, ctx)
    assert not result.ambiguous
    assert Path(result.intent.request.arguments["path"]).name == "AjanTest"


def test_nonexistent_file_clarification(resolver, tmp_path):
    ctx = ConversationalContext(
        active_file=str(tmp_path / "missing.txt"),
        last_renamed_file=str(tmp_path / "missing.txt"),
    )
    result = resolver.resolve("sonuc.txt'nin bulunduğu klasörü aç", ctx)
    assert result.ambiguous
    assert "bulamadim" in result.clarification.casefold()


def test_local_filename_not_url():
    assert extract_url_hint("sonuc.txt") is None


def test_resolve_helper_returns_parent(resolver, tmp_path):
    ctx = _ctx_with_file(tmp_path)
    folder, error = resolve_parent_directory_path("sonuc.txt'nin bulunduğu klasörü aç", ctx)
    assert not error
    assert Path(folder).name == "AjanTest"


def test_ajantest_e2e_parent_folder_open(resolver, tmp_path):
    """create test1.txt → rename sonuc.txt → open sonuc → open parent folder."""
    folder = tmp_path / "Desktop" / "AjanTest"
    folder.mkdir(parents=True)
    sonuc = folder / "sonuc.txt"
    sonuc.write_text("Birinci dosya", encoding="utf-8")

    ctx = ConversationalContext()
    ctx.update_from_tool(
        "rename_path",
        {"source": str(folder / "test1.txt"), "destination": str(sonuc), "verified": True},
        success=True,
        verified=True,
    )
    ctx.update_from_tool(
        "open_path",
        {"path": str(sonuc), "verified": True},
        success=True,
        verified=True,
    )

    result = resolver.resolve("sonuc.txt'nin bulunduğu klasörü aç", ctx)
    assert not result.ambiguous
    assert result.intent.request.name == "open_path"
    assert Path(result.intent.request.arguments["path"]).resolve() == folder.resolve()

    bare = resolver.resolve("Tamam Bulundukları Klasörü Aç", ctx)
    assert not bare.ambiguous
    assert Path(bare.intent.request.arguments["path"]).resolve() == folder.resolve()
