"""ScreenEntity → existing click(x, y) coordinates.

No new mouse backend: the bbox center is the UI target the click tool already
accepts.
"""
from __future__ import annotations

from hermes.screen.models import ScreenEntity


def entity_click_point(entity: ScreenEntity) -> tuple[int, int]:
    return entity.bbox.center_x, entity.bbox.center_y
