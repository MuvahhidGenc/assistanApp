"""File-specific view over the generic entity decision layer.

Kept as the file-facing API (`decide_file_candidates`) so existing callers are
unchanged; all scoring and threshold logic lives in `entity_decision`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from hermes.context.entity_decision import (
    Confidence,
    EntityDecision,
    build_file_candidates,
    human_path_label,
    score_candidates,
    wants_all_candidates,
)

if TYPE_CHECKING:
    from hermes.context.conversational_context import ConversationalContext

FileAction = Literal["open", "read", "modify"]

__all__ = [
    "FileAction",
    "FileDecision",
    "decide_file_candidates",
    "decision_open_summary",
    "build_open_results_from_decision",
    "human_path_label",
    "wants_open_all_files",
]

_CONFIDENCE_SCORES: dict[Confidence, float] = {
    Confidence.HIGH: 0.95,
    Confidence.MEDIUM: 0.8,
    Confidence.LOW: 0.0,
    Confidence.RISKY: 0.0,
}

_CERTAIN_REASONS = frozenset({"single_match", "select_all", "location_hint", "ordinal_hint"})

# Reasons that carry no useful explanation for the user.
_SILENT_REASONS = frozenset({"single_match", "exists_on_disk", ""})


@dataclass(frozen=True)
class FileDecision:
    chosen_paths: tuple[str, ...] = ()
    confidence: float = 0.0
    reason: str = ""
    clarification: str = ""
    candidates: tuple[str, ...] = ()
    auto_selected: bool = False
    level: Confidence = Confidence.LOW


def wants_open_all_files(text: str) -> bool:
    return wants_all_candidates(text)


def _to_file_decision(decision: EntityDecision) -> FileDecision:
    score = _CONFIDENCE_SCORES.get(decision.confidence, 0.0)
    if decision.reason in _CERTAIN_REASONS and decision.chosen:
        score = 1.0
    return FileDecision(
        chosen_paths=decision.chosen,
        confidence=score,
        reason=decision.reason,
        clarification=decision.clarification,
        candidates=decision.candidates,
        auto_selected=decision.auto_selected,
        level=decision.confidence,
    )


def decide_file_candidates(
    candidates: list[str],
    ctx: ConversationalContext,
    *,
    text: str = "",
    filename: str | None = None,
    action: FileAction = "open",
) -> FileDecision:
    """Pick the best file path from duplicates using conversational context."""
    del action  # reserved for read/modify-specific tuning

    if not candidates:
        return FileDecision(clarification="Dosya bulunamadi.")

    entity_candidates = build_file_candidates(candidates, ctx, filename=filename)
    decision = score_candidates(entity_candidates, text=text, label=filename)
    return _to_file_decision(decision)


def _reason_to_message(reason: str, path: str, filename: str | None) -> str:
    label = human_path_label(path)
    name = filename or Path(path).name
    if reason == "last_created_file":
        return f"En son olusturdugumuz {name} dosyasini aciyorum ({label})."
    if reason in ("last_verified_file", "last_modified_file", "entity_record") or reason.startswith(
        "recent_files"
    ):
        return f"En son kullandiginiz {name} dosyasini aciyorum ({label})."
    if reason == "active_folder":
        return f"Aktif klasordeki {name} dosyasini aciyorum ({label})."
    if reason == "location_hint":
        return f"{label} dosyasini aciyorum."
    return f"{label} dosyasini aciyorum."


def decision_open_summary(decision: FileDecision, *, filename: str | None = None) -> str:
    if not decision.chosen_paths:
        return ""
    if decision.reason == "select_all":
        labels = ", ".join(human_path_label(path) for path in decision.chosen_paths[:3])
        count = len(decision.chosen_paths)
        prefix = "Iki dosyayi da" if count == 2 else f"{count} dosyayi da"
        return f"{prefix} aciyorum: {labels}."
    path = decision.chosen_paths[0]
    if decision.auto_selected and decision.reason not in _SILENT_REASONS:
        return _reason_to_message(decision.reason, path, filename)
    return f"Dosya acilacak: {human_path_label(path)}"


def build_open_results_from_decision(
    decision: FileDecision,
    *,
    filename: str | None,
    build_open_path_result,
) -> tuple[Any | None, list[Any]]:
    """Return primary ResolutionResult and optional follow-up intents."""
    from hermes.context.reference_resolver import ResolutionResult

    if decision.clarification and not decision.chosen_paths:
        return (
            ResolutionResult(
                ambiguous=True,
                clarification=decision.clarification,
                resolved_references={"candidates": "|".join(decision.candidates)},
                is_new_task=False,
            ),
            [],
        )

    if not decision.chosen_paths:
        return None, []

    summary = decision_open_summary(decision, filename=filename)
    primary = build_open_path_result(decision.chosen_paths[0])
    if primary.intent and summary:
        from hermes.agent.local_intent import LocalIntent, LocalToolRequest

        req = primary.intent.request
        primary.intent = LocalIntent(
            LocalToolRequest(req.name, dict(req.arguments)),
            summary,
        )
    follow_ups: list[Any] = []
    for extra in decision.chosen_paths[1:]:
        built = build_open_path_result(extra)
        if built.intent:
            follow_ups.append(built.intent)
    primary.is_new_task = False
    return primary, follow_ups
