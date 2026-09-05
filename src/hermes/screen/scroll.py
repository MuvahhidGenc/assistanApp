"""Scroll action parameters derived from utterance structure.

Direction and magnitude live on the existing scroll tool (`direction`, `amount`).
Intensifiers and relative follow-ups adjust magnitude; entity geometry can
supply a unit step. No domain/site keyword tables.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hermes.agent.application_catalog import normalize_user_text


class ScrollMagnitude(StrEnum):
    SMALL = "small"
    NORMAL = "normal"
    LARGE = "large"
    UNIT = "unit"


# Wheel-click amounts for each magnitude (tool `amount` parameter).
_AMOUNT = {
    ScrollMagnitude.SMALL: 2,
    ScrollMagnitude.NORMAL: 4,
    ScrollMagnitude.LARGE: 9,
    ScrollMagnitude.UNIT: 5,
}

_DOWN = re.compile(
    r"\b(a[sş]a[gğ][ıi]|down|page\s*down|scroll\s*down|kaydir|kaydır)\b",
    re.I,
)
_UP = re.compile(
    r"\b(yukar[ıi]|up|page\s*up|scroll\s*up)\b",
    re.I,
)
_SCROLL_VERB = re.compile(
    r"\b(kaydir|kaydır|scroll|in|in(?:dir)?|indir)\b",
    re.I,
)
# Closed-class degree morphology on the scroll utterance (not domain keywords).
_SMALL = re.compile(r"\b(biraz|hafif|az(?:c[ıi]k)?|little|slightly)\b", re.I)
_LARGE = re.compile(r"\b(fazla|[cç]ok|epey|baya[gğ][ıi]|quite|more)\b", re.I)
_MORE = re.compile(r"\b(daha|again|tekrar|bir\s*kez\s*daha)\b", re.I)
_UNIT = re.compile(
    r"\b(bir|one)\s+[\wçğıöşüÇĞİÖŞÜ]+\s+(kadar|amount|worth)\b",
    re.I,
)


@dataclass(frozen=True)
class ScrollAction:
    direction: str
    magnitude: ScrollMagnitude
    amount: int
    relative_to_previous: bool = False

    def to_tool_arguments(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "amount": int(self.amount),
            "magnitude": self.magnitude.value,
            "relative_to_previous_scroll": self.relative_to_previous,
        }


def _unit_amount_from_context(context: Any) -> int | None:
    state = None
    if context is not None:
        raw = getattr(context, "last_screen_state", None)
        if isinstance(raw, dict):
            from hermes.screen.models import ScreenState

            state = ScreenState.from_dict(raw)
        if state is None:
            try:
                from hermes.screen.store import last_screen_state

                state = last_screen_state()
            except Exception:
                state = None
    if state is None or not state.entities:
        return None
    heights = [int(item.bbox.h) for item in state.entities if int(item.bbox.h) > 0]
    if not heights:
        return None
    heights.sort()
    typical = heights[len(heights) // 2]
    # Map entity height (px) onto wheel clicks (~120px per notch locally).
    return max(3, min(12, int(round(typical / 80))))


def _previous_scroll_amount(context: Any) -> int | None:
    if context is None:
        return None
    raw = getattr(context, "last_scroll_amount", None)
    if raw is None and isinstance(getattr(context, "last_tool_result", None), dict):
        raw = context.last_tool_result.get("amount")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_scroll_action(message: str, context: Any = None) -> ScrollAction | None:
    """Return scroll tool args when the utterance is a scroll request."""
    text = normalize_user_text(message or "").strip()
    if not text:
        return None
    lower = text.casefold()
    direction: str | None = None
    if _DOWN.search(lower):
        direction = "down"
    elif _UP.search(lower):
        direction = "up"
    elif _SCROLL_VERB.search(lower) and re.search(r"\bsayfa\b", lower):
        direction = "down"
    if direction is None:
        return None

    relative = bool(_MORE.search(lower))
    if _UNIT.search(lower):
        magnitude = ScrollMagnitude.UNIT
    elif _LARGE.search(lower):
        magnitude = ScrollMagnitude.LARGE
    elif _SMALL.search(lower):
        magnitude = ScrollMagnitude.SMALL
    else:
        magnitude = ScrollMagnitude.NORMAL

    amount = _AMOUNT[magnitude]
    if magnitude is ScrollMagnitude.UNIT:
        amount = _unit_amount_from_context(context) or amount

    if relative:
        previous = _previous_scroll_amount(context)
        if previous:
            # "biraz daha" stays small but still advances; "daha fazla" grows.
            if magnitude is ScrollMagnitude.SMALL:
                amount = max(amount, previous + 1)
            elif magnitude is ScrollMagnitude.LARGE:
                amount = max(amount, int(previous * 1.75) + 1)
            else:
                amount = max(amount, previous + 2)
        elif magnitude is ScrollMagnitude.NORMAL:
            amount = _AMOUNT[ScrollMagnitude.NORMAL] + 2

    return ScrollAction(
        direction=direction,
        magnitude=magnitude,
        amount=int(amount),
        relative_to_previous=relative,
    )
