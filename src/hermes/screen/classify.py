"""Assign generic roles to OCR entities from geometry and neighborhood.

Roles are evidence for later filtering. Site-specific text lists are not used:
chrome is a thin/top/symbolic band, cards are repeating content regions.
"""
from __future__ import annotations

from hermes.screen.models import ScreenEntity, ScreenState

_CHROME_ROLES = frozenset({"chrome", "window"})
_HINT_CLASSES: dict[str, frozenset[str]] = {
    "video": frozenset({"video", "card", "image"}),
    "button": frozenset({"button"}),
    "input": frozenset({"input", "search"}),
    "image": frozenset({"image", "card"}),
    "window": frozenset({"window"}),
    "file": frozenset({"file"}),
}


def hint_roles(type_hints: frozenset[str]) -> set[str]:
    wanted: set[str] = set()
    for hint in type_hints:
        wanted.update(_HINT_CLASSES.get(hint, {hint}))
    return wanted


def classify_screen_entities(state: ScreenState) -> ScreenState:
    """Fill `type` / `role` when still unknown. Safe to call more than once."""
    width = state.width or 1
    height = state.height or 1
    lines = [item for item in state.entities if item.type != "window"]
    for entity in lines:
        if entity.role in {"unknown", ""} and _chrome_like(entity, width, height):
            entity.role = "chrome"
            entity.type = "chrome"

    _mark_top_horizontal_strips(lines, width, height)
    content = [item for item in lines if item.role not in _CHROME_ROLES]
    for entity in _repeating_regions(content, width, height):
        if entity.role in {"unknown", ""}:
            entity.role = "card"
            entity.type = "card"

    for entity in content:
        if entity.role not in {"unknown", ""}:
            continue
        if _input_like(entity, width, height):
            entity.role = "input"
            entity.type = "input"
        elif _button_like(entity, width, height):
            entity.role = "button"
            entity.type = "button"
    return state


def entities_for_hints(state: ScreenState, type_hints: frozenset[str]) -> list[ScreenEntity]:
    """Target class first: spatial/ordinal ranks run on this pool only."""
    classify_screen_entities(state)
    actionable = [item for item in state.entities if item.type != "window"] or list(state.entities)
    content = [item for item in actionable if item.role not in _CHROME_ROLES]
    if not type_hints:
        return content or actionable

    wanted = hint_roles(type_hints)
    matched = [
        item
        for item in content
        if item.role in wanted or item.type in wanted
    ]
    if "video" in type_hints or "image" in type_hints:
        width = state.width or 1
        height = state.height or 1
        pool: list[ScreenEntity] = []
        seen: set[str] = set()
        for item in matched:
            seen.add(item.id)
            pool.append(item)
        for item in content:
            if item.id in seen:
                continue
            if _tile_like(item, width, height) and _title_like(item):
                seen.add(item.id)
                pool.append(item)
        if pool:
            return pool
        return [item for item in matched if _title_like(item)] or []
    return matched or content or actionable


def _chrome_like(entity: ScreenEntity, width: int, height: int) -> bool:
    text = (entity.text or "").strip()
    letters = [char for char in text if char.isalnum()]
    if len(letters) <= 2 and len(text) <= 12:
        return True
    y_ratio = entity.bbox.y / max(height, 1)
    h_ratio = entity.bbox.h / max(height, 1)
    w_ratio = entity.bbox.w / max(width, 1)
    if y_ratio < 0.18 and h_ratio < 0.05 and w_ratio > 0.45:
        return True
    if y_ratio < 0.10 and h_ratio < 0.04:
        return True
    if y_ratio > 0.90 and h_ratio < 0.06:
        return True
    return False


def _title_like(entity: ScreenEntity) -> bool:
    words = [word for word in (entity.text or "").split() if word]
    return len(words) >= 2 or len((entity.text or "").strip()) >= 10


def _tile_like(entity: ScreenEntity, width: int, height: int) -> bool:
    w_ratio = entity.bbox.w / max(width, 1)
    h_ratio = entity.bbox.h / max(height, 1)
    return 0.12 <= w_ratio <= 0.52 and h_ratio < 0.12


def _button_like(entity: ScreenEntity, width: int, height: int) -> bool:
    words = [word for word in (entity.text or "").split() if word]
    if len(words) > 4:
        return False
    if _title_like(entity) and _tile_like(entity, width, height):
        return False
    w_ratio = entity.bbox.w / max(width, 1)
    h_ratio = entity.bbox.h / max(height, 1)
    return w_ratio < 0.28 and 0.02 <= h_ratio <= 0.10


def _input_like(entity: ScreenEntity, width: int, height: int) -> bool:
    y_ratio = entity.bbox.y / max(height, 1)
    w_ratio = entity.bbox.w / max(width, 1)
    h_ratio = entity.bbox.h / max(height, 1)
    return y_ratio < 0.28 and w_ratio > 0.35 and h_ratio < 0.07


def _mark_top_horizontal_strips(
    entities: list[ScreenEntity], width: int, height: int
) -> None:
    """Chip/shorts rails sit in a thin top row. They are not video tiles."""
    buckets: dict[int, list[ScreenEntity]] = {}
    y_step = max(int(height * 0.03), 6)
    x_step = max(int(width * 0.06), 8)
    for entity in entities:
        if entity.role in _CHROME_ROLES:
            continue
        buckets.setdefault(entity.bbox.y // y_step, []).append(entity)
    for group in buckets.values():
        if len(group) < 2:
            continue
        y_ratio = min(item.bbox.y for item in group) / max(height, 1)
        if y_ratio >= 0.22:
            continue
        if not all(item.bbox.h / max(height, 1) < 0.06 for item in group):
            continue
        columns = {item.bbox.x // x_step for item in group}
        if len(columns) < 2:
            continue
        for item in group:
            item.role = "chrome"
            item.type = "chrome"


def _repeating_regions(
    entities: list[ScreenEntity], width: int, height: int
) -> list[ScreenEntity]:
    buckets: dict[tuple[int, int], list[ScreenEntity]] = {}
    width_step = max(int(width * 0.08), 8)
    height_step = max(int(height * 0.03), 4)
    for entity in entities:
        if entity.role in _CHROME_ROLES:
            continue
        if entity.bbox.w / max(width, 1) >= 0.55:
            continue
        key = (entity.bbox.w // width_step, entity.bbox.h // height_step)
        buckets.setdefault(key, []).append(entity)
    repeating: list[ScreenEntity] = []
    for group in buckets.values():
        if len(group) >= 2:
            repeating.extend(group)
    return repeating
