"""Screen perception tools: resolve a reference against the last ScreenState."""
from __future__ import annotations

from typing import Any

from hermes.config.settings import RiskLevel
from hermes.screen.bind import entity_click_point
from hermes.screen.models import ScreenState
from hermes.screen.resolve import resolve_screen_reference
from hermes.screen.store import last_screen_state
from hermes.tools.base import BaseTool, ToolExecutionResult


class ResolveScreenEntityTool(BaseTool):
    name = "resolve_screen_entity"
    description = "Ekran referansini mevcut ScreenState icindeki entity adaylarina baglar."
    risk_level = RiskLevel.READ_ONLY
    category = "computer_control"

    async def execute(self, reference: str = "", **kwargs: Any) -> ToolExecutionResult:
        raw = (reference or kwargs.get("query") or "").strip()
        if not raw:
            return ToolExecutionResult(success=False, error="reference gerekli")

        state = ScreenState.from_dict(kwargs.get("screen_state")) or last_screen_state()
        if state is None:
            return ToolExecutionResult(
                success=False,
                error="ekran durumu yok",
                output={"found": False, "needs_scroll": True},
            )

        session_id = str(kwargs.get("session_entity_id") or "").strip() or None
        decision = resolve_screen_reference(
            state,
            raw,
            session_entity_id=session_id,
            context=kwargs.get("context"),
        )
        if decision.needs_user_input:
            return ToolExecutionResult(
                success=False,
                error="needs_user_input",
                output={
                    "found": False,
                    "needs_user": True,
                    "clarification": decision.clarification,
                    "candidates": list(decision.candidates),
                },
            )
        if not decision.auto_selected or not decision.chosen:
            return ToolExecutionResult(
                success=False,
                error="ekranda hedef bulunamadi",
                output={
                    "found": False,
                    "needs_scroll": True,
                    "candidates": list(decision.candidates),
                },
            )

        entity = state.entity(decision.chosen[0])
        if entity is None:
            return ToolExecutionResult(
                success=False,
                error="ekranda hedef bulunamadi",
                output={"found": False, "needs_scroll": True},
            )
        x, y = entity_click_point(entity)
        return ToolExecutionResult(
            success=True,
            output={
                "found": True,
                "entity_id": entity.id,
                "x": x,
                "y": y,
                "text": entity.text,
                "bbox": entity.bbox.to_dict(),
                "state_id": state.state_id,
                "confidence": str(decision.confidence),
                "score": decision.score,
                "reason": decision.reason,
            },
        )

    def get_parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "description": "Kullanicinin ekran referansi",
                },
                "screen_state": {"type": "object"},
                "session_entity_id": {"type": "string"},
            },
            "required": ["reference"],
        }
