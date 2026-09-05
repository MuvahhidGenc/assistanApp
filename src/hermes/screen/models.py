"""Shared screen perception models.

Vision providers (OCR now, later a11y/vision) all emit the same ScreenEntity
shape so reference resolution does not care how the snapshot was produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class BoundingBox:
    x: int
    y: int
    w: int
    h: int

    @property
    def center_x(self) -> int:
        return int(self.x + self.w / 2)

    @property
    def center_y(self) -> int:
        return int(self.y + self.h / 2)

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: Any) -> BoundingBox:
        payload = data if isinstance(data, dict) else {}
        return cls(
            x=int(payload.get("x") or 0),
            y=int(payload.get("y") or 0),
            w=int(payload.get("w") or 0),
            h=int(payload.get("h") or 0),
        )


@dataclass
class ScreenEntity:
    id: str
    type: str = "text_line"
    role: str = "unknown"
    text: str = ""
    bbox: BoundingBox = field(default_factory=lambda: BoundingBox(0, 0, 0, 0))
    confidence: float = 0.0
    source: str = "ocr"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "role": self.role,
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "confidence": self.confidence,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ScreenEntity:
        payload = data if isinstance(data, dict) else {}
        return cls(
            id=str(payload.get("id") or ""),
            type=str(payload.get("type") or "text_line"),
            role=str(payload.get("role") or "unknown"),
            text=str(payload.get("text") or ""),
            bbox=BoundingBox.from_dict(payload.get("bbox")),
            confidence=float(payload.get("confidence") or 0.0),
            source=str(payload.get("source") or "ocr"),
        )


@dataclass
class ScreenState:
    state_id: str = field(default_factory=lambda: uuid4().hex[:12])
    timestamp: str = field(default_factory=_utc_now)
    screenshot: dict[str, Any] = field(default_factory=dict)
    window: dict[str, Any] = field(default_factory=dict)
    word_boxes: list[dict[str, Any]] = field(default_factory=list)
    line_groups: list[dict[str, Any]] = field(default_factory=list)
    entities: list[ScreenEntity] = field(default_factory=list)
    text: str = ""

    @property
    def width(self) -> int:
        return int(
            self.screenshot.get("width")
            or self.window.get("width")
            or 0
        )

    @property
    def height(self) -> int:
        return int(
            self.screenshot.get("height")
            or self.window.get("height")
            or 0
        )

    def entity(self, entity_id: str) -> ScreenEntity | None:
        wanted = str(entity_id or "")
        for item in self.entities:
            if item.id == wanted:
                return item
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "timestamp": self.timestamp,
            "screenshot": dict(self.screenshot),
            "window": dict(self.window),
            "word_boxes": list(self.word_boxes),
            "line_groups": list(self.line_groups),
            "entities": [item.to_dict() for item in self.entities],
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ScreenState | None:
        if not isinstance(data, dict):
            return None
        entities = [
            ScreenEntity.from_dict(item)
            for item in (data.get("entities") or [])
            if isinstance(item, dict)
        ]
        return cls(
            state_id=str(data.get("state_id") or uuid4().hex[:12]),
            timestamp=str(data.get("timestamp") or _utc_now()),
            screenshot=dict(data.get("screenshot") or {}),
            window=dict(data.get("window") or {}),
            word_boxes=[
                dict(item) for item in (data.get("word_boxes") or []) if isinstance(item, dict)
            ],
            line_groups=[
                dict(item) for item in (data.get("line_groups") or []) if isinstance(item, dict)
            ],
            entities=entities,
            text=str(data.get("text") or ""),
        )
