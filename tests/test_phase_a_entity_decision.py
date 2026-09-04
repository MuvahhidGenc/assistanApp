"""Phase A — generic entity decision layer.

Covers the decide/ask boundary, evidence sources (including EntityRecord) and
the behaviours that must not regress while decisions move into the shared
primitive.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hermes.context.conversational_context import ConversationalContext
from hermes.context.entity_decision import (
    DEFAULT_THRESHOLDS,
    Confidence,
    EntityCandidate,
    EntityType,
    build_file_candidates,
    human_path_label,
    score_candidates,
)
from hermes.context.file_decision import decide_file_candidates
from hermes.context.file_intent import resolve_read_file_path
from hermes.context.reference_resolver import ReferenceResolver


@pytest.fixture
def resolver() -> ReferenceResolver:
    return ReferenceResolver()


def _duplicate_pair(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "A" / "rapor.txt"
    b = tmp_path / "B" / "rapor.txt"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    return a.resolve(), b.resolve()


# --- 1-4: evidence picks the right candidate ---------------------------


def test_last_created_file_wins(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext(last_created_file=str(b), recent_files=[str(a)])

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="rapor.txt ac", filename="rapor.txt"
    )

    assert decision.chosen_paths == (str(b),)
    assert decision.level is Confidence.HIGH
    assert decision.reason == "last_created_file"


def test_last_verified_file_wins(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext(last_verified_file=str(b))

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="rapor.txt ac", filename="rapor.txt"
    )

    assert decision.chosen_paths == (str(b),)
    assert decision.reason == "last_verified_file"
    assert decision.auto_selected


def test_exact_numbered_filename_is_not_ambiguous(resolver, tmp_path):
    plain = tmp_path / "rapor.txt"
    numbered = tmp_path / "rapor (1).txt"
    plain.write_text("eski", encoding="utf-8")
    numbered.write_text("yeni", encoding="utf-8")

    ctx = ConversationalContext(
        recent_files=[str(plain.resolve()), str(numbered.resolve())],
        last_created_file=str(plain.resolve()),
    )
    result = resolver.resolve("rapor (1).txt dosyasini ac", ctx)

    assert not result.ambiguous
    assert Path(result.intent.request.arguments["path"]).name == "rapor (1).txt"


def test_duplicate_filename_resolved_by_active_folder(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext(active_folder=str(b.parent))

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="rapor.txt ac", filename="rapor.txt"
    )

    assert decision.chosen_paths == (str(b),)
    assert decision.reason == "active_folder"


# --- 5-6: ask when unsure, act on multi-target -------------------------


def test_no_evidence_asks_user_with_named_options(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext()

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="rapor.txt ac", filename="rapor.txt"
    )

    assert not decision.chosen_paths
    assert decision.level is Confidence.LOW
    assert "Hangisini acayim" in decision.clarification
    assert human_path_label(str(a)) in decision.clarification
    assert human_path_label(str(b)) in decision.clarification


def test_open_both_selects_every_candidate(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext()

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="ikisini de ac", filename="rapor.txt"
    )

    assert len(decision.chosen_paths) == 2
    assert decision.reason == "select_all"
    assert decision.level is Confidence.HIGH


# --- 7: EntityRecord becomes decision evidence -------------------------


def test_entity_record_breaks_the_tie(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext()
    ctx.record_entity("file", str(b), label="rapor.txt")

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="rapor.txt ac", filename="rapor.txt"
    )

    assert decision.chosen_paths == (str(b),)
    assert decision.reason == "entity_record"
    assert decision.level is Confidence.MEDIUM


def test_entity_record_does_not_override_stronger_evidence(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext(last_created_file=str(a))
    ctx.record_entity("file", str(b), label="rapor.txt")

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="rapor.txt ac", filename="rapor.txt"
    )

    assert decision.chosen_paths == (str(a),)
    assert decision.reason == "last_created_file"


def test_entity_record_type_must_match(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext()
    ctx.record_entity("folder", str(b.parent))

    decision = decide_file_candidates(
        [str(a), str(b)], ctx, text="rapor.txt ac", filename="rapor.txt"
    )

    assert not decision.chosen_paths


# --- generic primitive is not file-only --------------------------------


def test_primitive_scores_non_file_entities():
    chrome = EntityCandidate("chrome", EntityType.APPLICATION, label="Chrome")
    chrome.add("active_application", 90)
    notepad = EntityCandidate("notepad", EntityType.APPLICATION, label="Notepad")
    notepad.add("installed", 10)

    decision = score_candidates([chrome, notepad], text="onu kapat")

    assert decision.chosen == ("chrome",)
    assert decision.confidence is Confidence.HIGH
    assert decision.reason == "active_application"


def test_primitive_asks_when_urls_tie():
    first = EntityCandidate("https://a.example", EntityType.URL, label="A")
    first.add("recent", 30)
    second = EntityCandidate("https://b.example", EntityType.URL, label="B")
    second.add("recent", 30)

    decision = score_candidates([first, second], text="onu ac", label="sekme")

    assert decision.needs_user_input
    assert "A" in decision.clarification and "B" in decision.clarification


def test_llm_opinion_enters_through_the_evidence_channel():
    from hermes.context.entity_decision import LLM_EVIDENCE_SOURCE, attach_llm_evidence

    guess = EntityCandidate("task-1", EntityType.TASK, label="Dun yarim kalan is")
    attach_llm_evidence(guess, confidence=0.9, reasoning="kullanici dunku isten bahsetti")
    other = EntityCandidate("task-2", EntityType.TASK, label="Baska is")
    other.add("exists_on_disk", 10)

    decision = score_candidates([guess, other], text="onu devam ettir")

    assert decision.chosen == ("task-1",)
    assert decision.reason == LLM_EVIDENCE_SOURCE

    # A hesitant model must not be able to force a silent decision.
    unsure = EntityCandidate("task-1", EntityType.TASK, label="Dun yarim kalan is")
    attach_llm_evidence(unsure, confidence=0.3, reasoning="emin degilim")
    rival = EntityCandidate("task-2", EntityType.TASK, label="Baska is")
    attach_llm_evidence(rival, confidence=0.3, reasoning="emin degilim")

    assert score_candidates([unsure, rival], text="onu devam ettir").needs_user_input


def test_risky_decisions_never_auto_select():
    from hermes.context.entity_decision import EntityDecision

    risky = EntityDecision(chosen=("C:/Windows",), confidence=Confidence.RISKY, score=100)
    assert not risky.auto_selected

    assert not EntityDecision(chosen=("x",), confidence=Confidence.LOW).auto_selected
    assert EntityDecision(chosen=("x",), confidence=Confidence.HIGH).auto_selected


def test_thresholds_are_defined_once():
    assert DEFAULT_THRESHOLDS.high_min_score == 70
    assert DEFAULT_THRESHOLDS.medium_min_score == 55
    # The decide/ask boundary must come from the shared threshold object.
    strong = EntityCandidate("x", EntityType.OTHER)
    strong.add("evidence", DEFAULT_THRESHOLDS.medium_min_score - 1)
    weak = EntityCandidate("y", EntityType.OTHER)
    weak.add("evidence", DEFAULT_THRESHOLDS.medium_min_score - 2)

    assert score_candidates([strong, weak]).needs_user_input


def test_ordinal_selector_works_on_any_entity(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext()
    candidates = build_file_candidates([str(a), str(b)], ctx, filename="rapor.txt")

    decision = score_candidates(candidates, text="ikincisini ac")

    assert decision.chosen == (str(b),)
    assert decision.reason == "ordinal_hint"

    first = score_candidates(candidates, text="ilkini ac")
    assert first.chosen == (str(a),)


def test_location_hint_only_matches_locative_form():
    from hermes.context.entity_decision import _requested_location

    assert _requested_location("masaustundeki rapor.txt'yi ac") == "Desktop"
    assert _requested_location("belgelerdeki olani ac") == "Documents"
    # A destination ("save to the desktop") must never act as a filter.
    assert _requested_location("bunu masaustune rapor.txt olarak kaydet") is None


def test_duplicate_read_path_uses_shared_decision(tmp_path):
    a, b = _duplicate_pair(tmp_path)
    ctx = ConversationalContext(
        recent_files=[str(a), str(b)],
        last_verified_file=str(b),
    )

    assert resolve_read_file_path("rapor.txt icinde ne yaziyor", ctx) == str(b)


# --- 8-10: behaviours that must not regress ----------------------------


def test_turkish_downloads_alias_still_resolves():
    from hermes.context.system_paths import detect_known_folder_alias

    assert detect_known_folder_alias("Indirilenlerdeki PDFleri incele") == "Downloads"
    assert detect_known_folder_alias("İndirilenlerdeki PDFleri incele") == "Downloads"


def test_report_unique_filename_still_increments(tmp_path):
    from hermes.tools.windows.file_tools import resolve_unique_file_path

    target = tmp_path / "rapor.txt"
    assert resolve_unique_file_path(target) == target

    target.write_text("a", encoding="utf-8")
    first = resolve_unique_file_path(target)
    assert first.name == "rapor (1).txt"

    first.write_text("b", encoding="utf-8")
    assert resolve_unique_file_path(target).name == "rapor (2).txt"


def test_last_created_report_chain_targets_actual_path(resolver, tmp_path):
    planned = tmp_path / "rapor.txt"
    actual = tmp_path / "rapor (1).txt"
    planned.write_text("eski rapor", encoding="utf-8")
    actual.write_text("yeni rapor", encoding="utf-8")

    ctx = ConversationalContext(
        last_created_file=str(actual.resolve()),
        created_files=[str(actual.resolve())],
        recent_files=[str(planned.resolve())],
    )

    result = resolver.resolve("son olusturdugun dosyayi ac", ctx)
    assert not result.ambiguous
    assert Path(result.intent.request.arguments["path"]).name == "rapor (1).txt"

    assert Path(resolve_read_file_path("son olusturdugun dosyayi oku", ctx)).name == "rapor (1).txt"
