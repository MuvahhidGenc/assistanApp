"""Smart file disambiguation — context-aware decisions."""
from __future__ import annotations

from pathlib import Path

import pytest

from hermes.context.conversational_context import ConversationalContext
from hermes.context.file_decision import decide_file_candidates, wants_open_all_files
from hermes.context.reference_resolver import ReferenceResolver


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


def test_auto_pick_last_verified_duplicate_name(tmp_path):
    a = tmp_path / "A" / "rapor.txt"
    b = tmp_path / "B" / "rapor.txt"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    ctx = ConversationalContext(
        recent_files=[str(b.resolve()), str(a.resolve())],
        last_verified_file=str(b.resolve()),
    )
    decision = decide_file_candidates(
        [str(a.resolve()), str(b.resolve())],
        ctx,
        text="rapor.txt dosyasini ac",
        filename="rapor.txt",
    )
    assert decision.chosen_paths == (str(b.resolve()),)
    assert decision.auto_selected


def test_resolver_auto_opens_recent_duplicate(resolver, tmp_path):
    a = tmp_path / "A" / "rapor.txt"
    b = tmp_path / "B" / "rapor.txt"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    ctx = ConversationalContext(
        recent_files=[str(b.resolve()), str(a.resolve())],
        last_verified_file=str(b.resolve()),
    )
    result = resolver.resolve("rapor.txt dosyasini ac", ctx)
    assert not result.ambiguous
    assert result.intent is not None
    assert result.intent.request.arguments["path"] == str(b.resolve())


def test_open_all_two_files(resolver, tmp_path):
    a = tmp_path / "rapor.txt"
    b = tmp_path / "rapor (1).txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    ctx = ConversationalContext(recent_files=[str(a.resolve()), str(b.resolve())])
    assert wants_open_all_files("ikisini de ac")
    decision = decide_file_candidates(
        [str(a.resolve()), str(b.resolve())],
        ctx,
        text="ikisini de ac",
        filename="rapor.txt",
    )
    assert len(decision.chosen_paths) == 2

    result = resolver.resolve("rapor.txt ve rapor (1).txt ikisini de ac", ctx)
    if result.intent:
        assert len(result.follow_up_intents) >= 1 or not result.ambiguous


def test_deictic_open_uses_last_created_without_question(resolver, tmp_path):
    old = tmp_path / "rapor.txt"
    new = tmp_path / "rapor (1).txt"
    old.write_text("eski", encoding="utf-8")
    new.write_text("yeni", encoding="utf-8")

    ctx = ConversationalContext(last_created_file=str(new.resolve()))
    result = resolver.resolve("son olusturdugun dosyayi ac", ctx)
    assert not result.ambiguous
    assert Path(result.intent.request.arguments["path"]).name == "rapor (1).txt"


def test_ambiguous_when_no_context_signal(tmp_path):
    a = tmp_path / "A" / "rapor.txt"
    b = tmp_path / "B" / "rapor.txt"
    a.parent.mkdir()
    b.parent.mkdir()
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    ctx = ConversationalContext()
    decision = decide_file_candidates(
        [str(a.resolve()), str(b.resolve())],
        ctx,
        text="rapor.txt ac",
        filename="rapor.txt",
    )
    assert not decision.chosen_paths
    assert "Hangisini acayim" in decision.clarification
