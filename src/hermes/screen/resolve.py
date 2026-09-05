"""Resolve a natural-language reference against a ScreenState.

Reuses EntityDecision / Evidence / score_candidates. No per-phrase routes:
spatial, text, visibility, type, and session signals are evidence on the
same candidates.

When the user was shown a numbered screen result set, ordinal/spatial
choices bind to that presented order instead of re-scoring the whole OCR.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from hermes.context.entity_decision import (
    Confidence,
    EntityCandidate,
    EntityDecision,
    EntityType,
    score_candidates,
)
from hermes.screen.classify import entities_for_hints
from hermes.screen.models import ScreenEntity, ScreenState
from hermes.screen.reference import (
    ReferenceFeatures,
    SpatialSlot,
    extract_reference_features,
    requests_screen_rescan,
    stem_token,
    tokens_match,
)

# Weights live next to file evidence but are screen-specific sources.
POSITION_WEIGHT = 85
TEXT_WEIGHT = 90
TEXT_PHRASE_WEIGHT = 120
VISIBILITY_WEIGHT = 40
TYPE_WEIGHT = 50
SESSION_WEIGHT = 70
WEAK_POSITION_WEIGHT = 12


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def build_screen_result_set(
    *,
    state: ScreenState | None,
    presented_order: list[str] | tuple[str, ...],
    source: str = "resolve_screen_entity",
    clarification: str = "",
) -> dict[str, Any]:
    """Snapshot of what was offered to the user, in presentation order."""
    order = [str(item) for item in presented_order if str(item).strip()]
    entities: list[dict[str, Any]] = []
    if state is not None:
        for entity_id in order:
            entity = state.entity(entity_id)
            if entity is None:
                continue
            entities.append(
                {
                    "id": entity.id,
                    "text": entity.text,
                    "type": entity.type,
                    "bbox": entity.bbox.to_dict(),
                }
            )
    return {
        "result_id": uuid4().hex[:12],
        "state_id": state.state_id if state is not None else "",
        "entities": entities,
        "presented_order": order,
        "timestamp": _utc_now(),
        "source": source,
        "clarification": clarification,
    }


def _result_set_order(result_set: dict[str, Any] | None) -> list[str]:
    if not isinstance(result_set, dict):
        return []
    order = result_set.get("presented_order")
    if isinstance(order, list) and order:
        return [str(item) for item in order if str(item).strip()]
    entities = result_set.get("entities")
    if isinstance(entities, list):
        return [
            str(item.get("id") or "")
            for item in entities
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
    return []


def bind_presented_screen_choice(
    reference: str,
    result_set: dict[str, Any] | None,
) -> str | None:
    """Map '1 / ilkini / ortadakini' onto the list the user was shown."""
    order = _result_set_order(result_set)
    if not order:
        return None
    features = extract_reference_features(reference)
    # Quantity phrases ("3 videoyu ac") are not list-index picks.
    if features.quantity is not None and features.ordinal is None:
        return None
    index: int | None = None
    if features.ordinal is not None:
        index = features.ordinal
    elif features.spatial is SpatialSlot.FIRST:
        index = 0
    elif features.spatial is SpatialSlot.LAST:
        index = len(order) - 1
    elif features.spatial is SpatialSlot.CENTER:
        index = len(order) // 2
    if index is None:
        return None
    if 0 <= index < len(order):
        return order[index]
    return None


def _entity_tokens(text: str) -> tuple[str, ...]:
    features = extract_reference_features(text)
    return features.text_tokens


def _phrase_coverage(
    entity_text: str,
    user_tokens: tuple[str, ...],
) -> tuple[float, float]:
    """Return (entity_coverage, user_coverage) using stemmed token equality."""
    entity_tokens = _entity_tokens(entity_text)
    if not entity_tokens or not user_tokens:
        return 0.0, 0.0
    entity_hits = sum(
        1
        for entity_token in entity_tokens
        if any(tokens_match(entity_token, user_token) for user_token in user_tokens)
    )
    user_hits = sum(
        1
        for user_token in user_tokens
        if any(tokens_match(user_token, entity_token) for entity_token in entity_tokens)
    )
    return entity_hits / len(entity_tokens), user_hits / len(user_tokens)


def _token_overlap(text: str, tokens: tuple[str, ...]) -> float:
    entity_cov, user_cov = _phrase_coverage(text, tokens)
    return max(entity_cov, user_cov * 0.85)


def _reading_order(entities: list[ScreenEntity]) -> list[ScreenEntity]:
    return sorted(entities, key=lambda item: (item.bbox.y, item.bbox.x, item.id))


def _spatial_ranks(
    entities: list[ScreenEntity],
    slot: SpatialSlot,
    state: ScreenState,
) -> dict[str, int]:
    if not entities:
        return {}
    ordered = _reading_order(entities)
    width = state.width or max((item.bbox.right for item in entities), default=1)
    height = state.height or max((item.bbox.bottom for item in entities), default=1)
    cx = width / 2
    cy = height / 2

    def distance(entity: ScreenEntity) -> float:
        if slot is SpatialSlot.LEFT:
            return float(entity.bbox.center_x)
        if slot is SpatialSlot.RIGHT:
            return float(-entity.bbox.center_x)
        if slot is SpatialSlot.TOP or slot is SpatialSlot.FIRST:
            return float(entity.bbox.center_y * 1000 + entity.bbox.center_x)
        if slot is SpatialSlot.BOTTOM or slot is SpatialSlot.LAST:
            return float(-(entity.bbox.center_y * 1000 + entity.bbox.center_x))
        # CENTER
        dx = entity.bbox.center_x - cx
        dy = entity.bbox.center_y - cy
        return float(dx * dx + dy * dy)

    ranked = sorted(ordered, key=distance)
    return {item.id: index for index, item in enumerate(ranked)}


def attach_screen_evidence(
    entity: ScreenEntity,
    features: ReferenceFeatures,
    state: ScreenState,
    *,
    spatial_rank: int | None = None,
    session_id: str | None = None,
) -> EntityCandidate:
    candidate = EntityCandidate(
        identifier=entity.id,
        entity_type=EntityType.SCREEN,
        label=entity.text or entity.id,
    )
    candidate.add("visibility", VISIBILITY_WEIGHT, detail=entity.source)

    if features.text_tokens:
        entity_cov, user_cov = _phrase_coverage(entity.text, features.text_tokens)
        if entity_cov >= 0.85 and entity_cov * len(_entity_tokens(entity.text)) >= 1.7:
            candidate.add("text_similarity", TEXT_PHRASE_WEIGHT, detail="phrase_match")
        elif entity_cov >= 1.0 or (entity_cov >= 0.66 and user_cov >= 0.5):
            candidate.add("text_similarity", TEXT_WEIGHT, detail="full")
        elif entity_cov >= 0.5 or user_cov >= 0.5:
            score = int(TEXT_WEIGHT * max(entity_cov, user_cov * 0.85))
            candidate.add(
                "text_similarity",
                max(score, int(TEXT_WEIGHT * 0.55)),
                detail=f"overlap={max(entity_cov, user_cov):.2f}",
            )
        elif entity_cov > 0 or user_cov > 0:
            candidate.add(
                "text_similarity",
                int(TEXT_WEIGHT * 0.45),
                detail=f"overlap={max(entity_cov, user_cov):.2f}",
            )

    if features.type_hints:
        if entity.type in features.type_hints or entity.role in features.type_hints:
            candidate.add("role_type", TYPE_WEIGHT, detail=entity.type)
        elif any(hint in entity.text.casefold() for hint in features.type_hints):
            candidate.add("role_type", int(TYPE_WEIGHT * 0.7), detail="text")

    if spatial_rank is not None:
        if spatial_rank == 0:
            candidate.add("position", POSITION_WEIGHT, detail=str(features.spatial or features.ordinal))
        else:
            decay = max(WEAK_POSITION_WEIGHT, POSITION_WEIGHT - spatial_rank * 25)
            candidate.add("position", decay, detail=f"rank={spatial_rank}")

    if session_id and session_id == entity.id and _session_evidence_allowed(features):
        candidate.add("session_reference", SESSION_WEIGHT, detail=session_id)

    return candidate


def _session_evidence_allowed(features: ReferenceFeatures) -> bool:
    """Session locks only an explicit prior target, not a fresh 'bir video' look."""
    if features.session_ref:
        return True
    if not features.deictic:
        return False
    if requests_screen_rescan(features.raw):
        return False
    if features.spatial is not None or features.ordinal is not None:
        return False
    if features.quantity is not None:
        return False
    # Named targets override the previous session lock.
    if len(features.text_tokens) >= 2:
        return False
    return True


def _context_result_set(context: Any) -> dict[str, Any] | None:
    if context is None:
        return None
    raw = getattr(context, "last_screen_result_set", None)
    if isinstance(raw, dict) and _result_set_order(raw):
        return raw
    if isinstance(context, dict):
        nested = context.get("last_screen_result_set")
        if isinstance(nested, dict) and _result_set_order(nested):
            return nested
        pending = context.get("pending_screen_resolve")
        if isinstance(pending, dict):
            order = pending.get("presented_order") or pending.get("candidates")
            if isinstance(order, list) and order:
                return {
                    "presented_order": [str(item) for item in order],
                    "state_id": str(pending.get("state_id") or ""),
                    "source": "pending_screen_resolve",
                }
    return None


def resolve_screen_reference(
    state: ScreenState | None,
    reference: str,
    *,
    session_entity_id: str | None = None,
    context: Any = None,
    result_set: dict[str, Any] | None = None,
) -> EntityDecision:
    if state is None or not state.entities:
        return EntityDecision(confidence=Confidence.LOW)

    features = extract_reference_features(reference)
    active_result = result_set if isinstance(result_set, dict) else _context_result_set(context)
    bound_id = bind_presented_screen_choice(reference, active_result)
    if bound_id:
        entity = state.entity(bound_id)
        if entity is not None:
            return EntityDecision(
                chosen=(bound_id,),
                confidence=Confidence.HIGH,
                score=POSITION_WEIGHT + SESSION_WEIGHT,
                reason="presented_order",
                candidates=tuple(_result_set_order(active_result)) or (bound_id,),
            )
        # Stale id still preferred when state was refreshed but order is known.
        return EntityDecision(
            chosen=(bound_id,),
            confidence=Confidence.HIGH,
            score=SESSION_WEIGHT,
            reason="presented_order",
            candidates=tuple(_result_set_order(active_result)) or (bound_id,),
        )

    session_id = session_entity_id
    if session_id is None and context is not None:
        focus = getattr(context, "active_focus", None)
        if focus is not None and getattr(focus, "type", "") == "screen_entity":
            session_id = getattr(focus, "identifier", None)
        else:
            session_id = getattr(context, "last_screen_entity_id", None)

    # Pure deixis ("onu ac") → last bound entity, no OCR re-ranking.
    if (
        session_id
        and features.deictic
        and not features.text_tokens
        and features.ordinal is None
        and features.spatial is None
        and features.quantity is None
        and not requests_screen_rescan(features.raw)
    ):
        if state.entity(str(session_id)) is not None or active_result:
            return EntityDecision(
                chosen=(str(session_id),),
                confidence=Confidence.HIGH,
                score=SESSION_WEIGHT + POSITION_WEIGHT,
                reason="session_deixis",
                candidates=(str(session_id),),
            )

    pool = entities_for_hints(state, features.type_hints)
    if features.type_hints == frozenset({"window"}):
        pool = [item for item in state.entities if item.type == "window"] or pool

    ranks: dict[str, int] = {}
    if features.spatial is not None:
        ranks = _spatial_ranks(pool, features.spatial, state)
    elif features.ordinal is not None and features.quantity is None:
        ordered = _reading_order(pool)
        ranks = {item.id: abs(index - features.ordinal) for index, item in enumerate(ordered)}

    candidates = [
        attach_screen_evidence(
            entity,
            features,
            state,
            spatial_rank=ranks.get(entity.id),
            session_id=session_id,
        )
        for entity in pool
    ]

    # Strong unique phrase match skips the crowded OCR clarification path.
    phrase_winners = [
        item
        for item in candidates
        if any(
            evidence.source == "text_similarity" and evidence.detail == "phrase_match"
            for evidence in item.evidence
        )
    ]
    if len(phrase_winners) == 1:
        winner = phrase_winners[0]
        return EntityDecision(
            chosen=(winner.identifier,),
            confidence=Confidence.HIGH,
            score=winner.score,
            reason="phrase_match",
            candidates=tuple(item.identifier for item in candidates),
        )

    return score_candidates(candidates, text=reference, label="oge")
