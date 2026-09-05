"""Turn screenshot + OCR word boxes into a ScreenState.

Spatial data from `read_screen_text` is kept: word boxes, line groups with
full bboxes, and ScreenEntity rows. OCR preprocessing scales the image; boxes
are converted back to screen pixels so `click(x, y)` lands correctly.
"""
from __future__ import annotations

from typing import Any

from hermes.screen.classify import classify_screen_entities
from hermes.screen.models import BoundingBox, ScreenEntity, ScreenState
from hermes.screen.store import remember_screen_state

# `vision.preprocess_for_ocr` resizes by this factor before Tesseract.
OCR_BOX_SCALE = 1.6


def _screen_px(value: Any, scale: float = OCR_BOX_SCALE) -> int:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return 0
    if scale <= 0:
        return int(raw)
    return int(round(raw / scale))


def _group_lines(boxes: list[dict[str, Any]], *, scale: float = OCR_BOX_SCALE) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for box in boxes:
        key = (int(box.get("block") or 0), int(box.get("line") or 0))
        grouped.setdefault(key, []).append(box)
    lines: list[dict[str, Any]] = []
    for items in grouped.values():
        items.sort(key=lambda item: int(item.get("x") or 0))
        text = " ".join(str(item.get("text") or "") for item in items).strip()
        if not text:
            continue
        xs = [_screen_px(item.get("x"), scale) for item in items]
        ys = [_screen_px(item.get("y"), scale) for item in items]
        rights = [
            _screen_px(item.get("x"), scale) + _screen_px(item.get("w"), scale)
            for item in items
        ]
        bottoms = [
            _screen_px(item.get("y"), scale) + _screen_px(item.get("h"), scale)
            for item in items
        ]
        confs = []
        for item in items:
            try:
                confs.append(float(item.get("conf") or 0))
            except (TypeError, ValueError):
                continue
        x = min(xs) if xs else 0
        y = min(ys) if ys else 0
        w = max(rights) - x if rights else 0
        h = max(bottoms) - y if bottoms else 0
        lines.append(
            {
                "text": text,
                "x": x,
                "y": y,
                "w": max(w, 0),
                "h": max(h, 0),
                "confidence": (sum(confs) / len(confs) / 100.0) if confs else 0.0,
            }
        )
    lines.sort(key=lambda item: (int(item["y"]), int(item["x"])))
    return lines


def _window_from_ocr(payload: dict[str, Any]) -> dict[str, Any]:
    rect = payload.get("window_rect") if isinstance(payload.get("window_rect"), dict) else {}
    shot = payload.get("screenshot") if isinstance(payload.get("screenshot"), dict) else {}
    return {
        "title": str(payload.get("window_title") or payload.get("title") or "").strip(),
        "app": str(payload.get("browser_app") or "").strip(),
        "url": str(payload.get("url") or payload.get("page_url") or "").strip(),
        "width": int(shot.get("width") or rect.get("width") or 0),
        "height": int(shot.get("height") or rect.get("height") or 0),
        "rect": dict(rect) if rect else {},
    }


def _scale_word_box(box: dict[str, Any], *, scale: float = OCR_BOX_SCALE) -> dict[str, Any]:
    return {
        "text": str(box.get("text") or ""),
        "x": _screen_px(box.get("x"), scale),
        "y": _screen_px(box.get("y"), scale),
        "w": _screen_px(box.get("w"), scale),
        "h": _screen_px(box.get("h"), scale),
        "conf": box.get("conf"),
        "line": box.get("line"),
        "block": box.get("block"),
    }


def build_screen_state(
    payload: dict[str, Any] | None,
    *,
    scale: float = OCR_BOX_SCALE,
    remember: bool = True,
) -> ScreenState:
    """Build a ScreenState from a `read_screen_text` / OCR dict."""
    data = payload if isinstance(payload, dict) else {}
    raw_boxes = [dict(item) for item in (data.get("boxes") or []) if isinstance(item, dict)]
    word_boxes = [_scale_word_box(item, scale=scale) for item in raw_boxes]
    line_groups = _group_lines(raw_boxes, scale=scale)
    screenshot = dict(data.get("screenshot") or {})
    window = _window_from_ocr(data)
    entities: list[ScreenEntity] = []

    title = str(window.get("title") or "").strip()
    width = int(screenshot.get("width") or window.get("width") or 0)
    height = int(screenshot.get("height") or window.get("height") or 0)
    if title or width or height:
        entities.append(
            ScreenEntity(
                id="se_window",
                type="window",
                role="window",
                text=title,
                bbox=BoundingBox(0, 0, max(width, 1), max(height, 1)),
                confidence=1.0 if title else 0.4,
                source="window",
            )
        )

    for index, line in enumerate(line_groups):
        entities.append(
            ScreenEntity(
                id=f"se_{index}",
                type="text_line",
                role="unknown",
                text=str(line.get("text") or ""),
                bbox=BoundingBox(
                    int(line.get("x") or 0),
                    int(line.get("y") or 0),
                    int(line.get("w") or 0),
                    int(line.get("h") or 0),
                ),
                confidence=float(line.get("confidence") or 0.0),
                source="ocr",
            )
        )

    from hermes.screen.store import last_screen_state

    previous = last_screen_state()
    state = ScreenState(
        screenshot=screenshot,
        window=window,
        word_boxes=word_boxes,
        line_groups=line_groups,
        entities=entities,
        text=str(data.get("text") or ""),
        previous_state_id=previous.state_id if previous is not None else "",
    )
    classify_screen_entities(state)
    if remember:
        remember_screen_state(state)
    return state


def attach_screen_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep spatial fields on a tool output and add `entities` / `screen_state`."""
    data = dict(payload) if isinstance(payload, dict) else {}
    state = build_screen_state(data, remember=True)
    data["boxes"] = list(state.word_boxes)
    data["lines"] = [str(item.get("text") or "") for item in state.line_groups]
    data["line_groups"] = list(state.line_groups)
    data["entities"] = [item.to_dict() for item in state.entities]
    data["screen_state"] = state.to_dict()
    data["state_id"] = state.state_id
    return data
