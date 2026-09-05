"""Generic entity decision primitive.

Candidate discovery and confidence-based selection shared by every reference
type (file, folder, application, url, ...). Type-specific modules supply
evidence; scoring and the decide/ask boundary live here so thresholds are
defined exactly once.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class EntityType(StrEnum):
    FILE = "file"
    FOLDER = "folder"
    APPLICATION = "application"
    URL = "url"
    BROWSER_PAGE = "browser_page"
    WINDOW = "window"
    SCREEN = "screen_entity"
    DOCUMENT = "document"
    TASK = "task"
    OTHER = "other"


class Confidence(StrEnum):
    """How safe it is to act without asking the user.

    RISKY is reserved for destructive actions that must go through the
    approval path; like LOW it never auto-selects.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    RISKY = "risky"


@dataclass(frozen=True)
class DecisionThresholds:
    """Single source of truth for decision cut-offs."""

    high_min_score: int = 70
    high_min_gap: int = 25
    medium_min_score: int = 55
    medium_max_runner_up: int = 40
    max_auto_multi: int = 4
    max_clarification_options: int = 4


DEFAULT_THRESHOLDS = DecisionThresholds()

# Evidence weights. Ordered strongest-first; ties resolve by recency index.
EVIDENCE_WEIGHTS: dict[str, int] = {
    "active_focus": 110,
    "last_created_file": 100,
    "last_verified_file": 95,
    "last_modified_file": 90,
    "active_file": 85,
    "last_opened_file": 80,
    "active_folder": 75,
    "recent_files": 70,
    "created_files": 65,
    "entity_record": 60,
    "recent_folders": 60,
    "last_created_folder": 90,
    "exists_on_disk": 10,
}

LLM_EVIDENCE_SOURCE = "llm_intent"

_MAX_WEIGHT = EVIDENCE_WEIGHTS["last_created_file"]

_RECENCY_DECAY_CAP = 20


@dataclass(frozen=True)
class Evidence:
    """One reason a candidate might be the entity the user meant."""

    source: str
    weight: int
    detail: str = ""


@dataclass
class EntityCandidate:
    """A possible referent plus the evidence supporting it."""

    identifier: str
    entity_type: EntityType = EntityType.FILE
    label: str | None = None
    evidence: list[Evidence] = field(default_factory=list)

    def add(self, source: str, weight: int, detail: str = "") -> None:
        self.evidence.append(Evidence(source=source, weight=weight, detail=detail))

    @property
    def score(self) -> int:
        return max((item.weight for item in self.evidence), default=0)

    @property
    def top_evidence(self) -> Evidence | None:
        if not self.evidence:
            return None
        return max(self.evidence, key=lambda item: item.weight)

    @property
    def reason(self) -> str:
        top = self.top_evidence
        return top.source if top else ""

    @property
    def display(self) -> str:
        if self.label:
            return self.label
        if self.entity_type in (EntityType.FILE, EntityType.FOLDER):
            return human_path_label(self.identifier)
        return self.identifier


@dataclass(frozen=True)
class EntityDecision:
    """Outcome of scoring: either act on `chosen`, or ask via `clarification`."""

    chosen: tuple[str, ...] = ()
    confidence: Confidence = Confidence.LOW
    score: int = 0
    reason: str = ""
    clarification: str = ""
    candidates: tuple[str, ...] = ()

    @property
    def auto_selected(self) -> bool:
        return bool(self.chosen) and self.confidence in (Confidence.HIGH, Confidence.MEDIUM)

    @property
    def needs_user_input(self) -> bool:
        return not self.chosen and bool(self.clarification)


# --- identifier helpers -------------------------------------------------


def normalize_identifier(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(Path(str(value)).resolve()).casefold()
    except OSError:
        return str(value).casefold()


def identifiers_equal(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return normalize_identifier(left) == normalize_identifier(right)


_KNOWN_FOLDER_LABELS: tuple[tuple[str, str], ...] = (
    ("Desktop", "Masaustu"),
    ("Documents", "Belgeler"),
    ("Downloads", "Indirilenler"),
    ("Pictures", "Resimler"),
    ("Videos", "Videolar"),
    ("Music", "Muzik"),
)


def human_path_label(path: str) -> str:
    """Readable location for a path, e.g. 'Masaustu\\rapor.txt'."""
    try:
        resolved = Path(str(path)).resolve()
        home = Path.home()
        for folder, label in _KNOWN_FOLDER_LABELS:
            try:
                resolved.relative_to(home / folder)
                return f"{label}\\{resolved.name}"
            except ValueError:
                continue
        parent = resolved.parent.name
        return f"{parent}\\{resolved.name}" if parent else resolved.name
    except OSError:
        return str(path)


# --- selectors ----------------------------------------------------------

_SELECT_ALL_PATTERNS = (
    re.compile(r"ikisini\s+de\s+a[çc]", re.IGNORECASE),
    re.compile(r"hepsini\s+a[çc]", re.IGNORECASE),
    re.compile(r"her\s+ikisini", re.IGNORECASE),
    re.compile(r"both", re.IGNORECASE),
    re.compile(r"t[uü]m[uü]n[uü]\s+a[çc]", re.IGNORECASE),
)

_ORDINAL_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b(ilkini|ilkinden|birincisini|first\s+one)\b", re.IGNORECASE), 0),
    (re.compile(r"\b(ikincisini|second\s+one)\b", re.IGNORECASE), 1),
    (re.compile(r"\b([uü][çc][uü]nc[uü]s[uü]n[uü]|third\s+one)\b", re.IGNORECASE), 2),
)

# "the one in X" — deliberately restricted to locative -ki forms so that
# "masaustune rapor olustur" is never treated as a filter.
_LOCATION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"masa[uü]st[uü]ndeki", re.IGNORECASE), "Desktop"),
    (re.compile(r"belgelerdeki", re.IGNORECASE), "Documents"),
    (re.compile(r"indirilenlerdeki", re.IGNORECASE), "Downloads"),
)


def wants_all_candidates(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _SELECT_ALL_PATTERNS)


def _requested_ordinal(text: str) -> int | None:
    for pattern, index in _ORDINAL_PATTERNS:
        if pattern.search(text or ""):
            return index
    return None


def _requested_location(text: str) -> str | None:
    for pattern, folder in _LOCATION_PATTERNS:
        if pattern.search(text or ""):
            return folder
    return None


def _filter_by_location(
    candidates: list[EntityCandidate], folder: str
) -> list[EntityCandidate]:
    base = Path.home() / folder
    kept: list[EntityCandidate] = []
    for candidate in candidates:
        try:
            Path(candidate.identifier).resolve().relative_to(base)
        except (OSError, ValueError):
            continue
        kept.append(candidate)
    return kept


# --- scoring ------------------------------------------------------------


def _build_clarification(
    candidates: list[EntityCandidate],
    *,
    label: str | None,
    thresholds: DecisionThresholds,
) -> str:
    shown = candidates[: thresholds.max_clarification_options]
    name = label or (Path(candidates[0].identifier).name if candidates else "oge")
    lines = [f"{len(candidates)} farkli {name} buldum:"]
    for index, candidate in enumerate(shown, start=1):
        lines.append(f"{index}. {candidate.display}")
    tail = "ikisini de" if len(candidates) == 2 else "hepsini"
    lines.append(f"Hangisini acayim? Istersen {tail} acabilirim.")
    return "\n".join(lines)


def score_candidates(
    candidates: list[EntityCandidate],
    *,
    text: str = "",
    label: str | None = None,
    thresholds: DecisionThresholds = DEFAULT_THRESHOLDS,
) -> EntityDecision:
    """Pick one entity, several, or ask — based on evidence strength.

    HIGH   clear winner, act silently.
    MEDIUM best guess backed by context, act and say why.
    LOW    no safe winner, present options.
    """
    unique: list[EntityCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = normalize_identifier(candidate.identifier)
        if key and key not in seen:
            seen.add(key)
            unique.append(candidate)

    all_ids = tuple(candidate.identifier for candidate in unique)

    if not unique:
        return EntityDecision(confidence=Confidence.LOW)

    if len(unique) == 1:
        return EntityDecision(
            chosen=(unique[0].identifier,),
            confidence=Confidence.HIGH,
            score=unique[0].score,
            reason="single_match",
            candidates=all_ids,
        )

    ordered = sorted(unique, key=lambda item: item.score, reverse=True)

    location = _requested_location(text)
    if location:
        narrowed = _filter_by_location(ordered, location)
        if len(narrowed) == 1:
            return EntityDecision(
                chosen=(narrowed[0].identifier,),
                confidence=Confidence.HIGH,
                score=narrowed[0].score,
                reason="location_hint",
                candidates=all_ids,
            )
        if narrowed:
            ordered = narrowed

    if wants_all_candidates(text):
        chosen = tuple(item.identifier for item in ordered[: thresholds.max_auto_multi])
        return EntityDecision(
            chosen=chosen,
            confidence=Confidence.HIGH,
            score=ordered[0].score,
            reason="select_all",
            candidates=all_ids,
        )

    ordinal = _requested_ordinal(text)
    if ordinal is not None and ordinal < len(ordered):
        return EntityDecision(
            chosen=(ordered[ordinal].identifier,),
            confidence=Confidence.HIGH,
            score=ordered[ordinal].score,
            reason="ordinal_hint",
            candidates=all_ids,
        )

    top = ordered[0]
    runner_up = ordered[1].score if len(ordered) > 1 else 0

    if top.score >= thresholds.high_min_score and (top.score - runner_up) >= thresholds.high_min_gap:
        return EntityDecision(
            chosen=(top.identifier,),
            confidence=Confidence.HIGH,
            score=top.score,
            reason=top.reason,
            candidates=all_ids,
        )

    if top.score >= thresholds.medium_min_score and runner_up < thresholds.medium_max_runner_up:
        return EntityDecision(
            chosen=(top.identifier,),
            confidence=Confidence.MEDIUM,
            score=top.score,
            reason=top.reason,
            candidates=all_ids,
        )

    return EntityDecision(
        confidence=Confidence.LOW,
        score=top.score,
        candidates=tuple(item.identifier for item in ordered),
        clarification=_build_clarification(ordered, label=label, thresholds=thresholds),
    )


# --- evidence collection ------------------------------------------------


def attach_context_evidence(
    candidate: EntityCandidate,
    ctx,
    *,
    filename: str | None = None,
) -> EntityCandidate:
    """Attach conversational-context evidence to a file candidate."""
    path = candidate.identifier
    invalidated = getattr(ctx, "is_invalidated", None)
    if callable(invalidated) and invalidated(path):
        return candidate

    focus = getattr(ctx, "active_focus", None)
    if focus is not None and identifiers_equal(path, getattr(focus, "identifier", None)):
        candidate.add("active_focus", EVIDENCE_WEIGHTS["active_focus"])

    for source in ("last_created_file", "last_verified_file", "last_modified_file", "active_file", "last_opened_file"):
        if identifiers_equal(path, getattr(ctx, source, None)):
            candidate.add(source, EVIDENCE_WEIGHTS[source])

    active_folder = getattr(ctx, "active_folder", None)
    if active_folder:
        try:
            sibling = Path(active_folder) / (filename or Path(path).name)
            if identifiers_equal(path, str(sibling)):
                candidate.add("active_folder", EVIDENCE_WEIGHTS["active_folder"])
        except OSError:
            pass

    for source in ("recent_files", "created_files"):
        for index, known in enumerate(getattr(ctx, source, None) or []):
            if identifiers_equal(path, known):
                weight = EVIDENCE_WEIGHTS[source] - min(index, _RECENCY_DECAY_CAP)
                candidate.add(source, weight, detail=f"index={index}")
                break

    attach_entity_record_evidence(candidate, ctx)

    if not candidate.evidence:
        candidate.add("exists_on_disk", EVIDENCE_WEIGHTS["exists_on_disk"])
    return candidate


def attach_llm_evidence(
    candidate: EntityCandidate,
    *,
    confidence: float,
    reasoning: str = "",
) -> EntityCandidate:
    """Let a model contribute an opinion through the same evidence channel.

    `confidence` is 0..1 and is projected onto the deterministic weight scale,
    so an LLM judgement competes with context signals instead of overriding
    them. This is the seam Phase F will plug intent understanding into.
    """
    bounded = max(0.0, min(1.0, float(confidence)))
    candidate.add(LLM_EVIDENCE_SOURCE, int(round(bounded * _MAX_WEIGHT)), detail=reasoning)
    return candidate


def attach_entity_record_evidence(candidate: EntityCandidate, ctx) -> EntityCandidate:
    """Use the recorded entity trail as recency evidence.

    `recent_entities` is newest-first, so earlier positions score higher.
    """
    records = getattr(ctx, "recent_entities", None) or []
    for index, record in enumerate(records):
        if str(getattr(record, "entity_type", "")) != str(candidate.entity_type):
            continue
        if identifiers_equal(candidate.identifier, getattr(record, "identifier", None)):
            weight = EVIDENCE_WEIGHTS["entity_record"] - min(index, _RECENCY_DECAY_CAP)
            candidate.add("entity_record", weight, detail=f"index={index}")
            break
    return candidate


def build_file_candidates(
    paths: list[str],
    ctx,
    *,
    filename: str | None = None,
) -> list[EntityCandidate]:
    """Turn raw paths into scored-ready candidates.

    A candidate whose name contradicts an explicitly requested filename stays
    in the list (so it can still be offered) but carries no evidence.
    """
    candidates: list[EntityCandidate] = []
    for raw in paths:
        path = str(raw)
        candidate = EntityCandidate(identifier=path, entity_type=EntityType.FILE)
        name_conflict = bool(filename) and Path(path).name.casefold() != filename.casefold()
        if not name_conflict:
            attach_context_evidence(candidate, ctx, filename=filename)
        candidates.append(candidate)
    return candidates
