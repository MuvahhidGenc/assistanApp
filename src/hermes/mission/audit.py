"""Mission lifecycle audit events — internal only, not shown in chat UI."""
from __future__ import annotations

from typing import Any

from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class MissionAuditor:
    def __init__(self, mission_id: str) -> None:
        self._mission_id = mission_id

    def _emit(self, event: str, **fields: Any) -> None:
        payload = {"mission_id": self._mission_id, **fields}
        logger.info(event, **payload)

    def mission_created(self, user_goal: str) -> None:
        self._emit("MISSION_CREATED", goal_preview=user_goal[:200])

    def mission_resumed(self, *, from_status: str = "") -> None:
        self._emit("MISSION_RESUMED", from_status=from_status)

    def mission_suspended(self, *, reason: str = "") -> None:
        self._emit("MISSION_SUSPENDED", reason=reason[:300])

    def mission_cancelled(self, *, reason: str = "") -> None:
        self._emit("MISSION_CANCELLED", reason=reason[:300])

    def user_interrupted(self, *, new_goal: str = "") -> None:
        self._emit("USER_INTERRUPTED", new_goal_preview=new_goal[:200])

    def goal_parsed(self, parsed: dict[str, Any]) -> None:
        self._emit("GOAL_PARSED", **{k: v for k, v in parsed.items() if k != "raw_message"})

    def plan_created(self, *, source: str, step_count: int) -> None:
        self._emit("PLAN_CREATED", source=source, step_count=step_count)

    def plan_validated(self, *, step_count: int) -> None:
        self._emit("PLAN_VALIDATED", step_count=step_count)

    def plan_rejected(self, *, errors: list[str]) -> None:
        self._emit("PLAN_REJECTED", errors=errors[:5])

    def step_started(self, step_id: str, title: str = "") -> None:
        self._emit("STEP_STARTED", step_id=step_id, title=title[:120])

    def step_completed(self, step_id: str, *, summary: str = "") -> None:
        self._emit("STEP_COMPLETED", step_id=step_id, summary=summary[:200])

    def step_failed(self, step_id: str, *, reason: str = "") -> None:
        self._emit("STEP_FAILED", step_id=step_id, reason=reason[:300])

    def tool_selected(self, tool_name: str, step_id: str = "") -> None:
        self._emit("TOOL_SELECTED", tool_name=tool_name, step_id=step_id)

    def tool_executed(self, tool_name: str, *, success: bool, step_id: str = "") -> None:
        self._emit("TOOL_EXECUTED", tool_name=tool_name, success=success, step_id=step_id)

    def verification_started(self, tool_name: str, method: str = "") -> None:
        self._emit("VERIFICATION_STARTED", tool_name=tool_name, method=method)

    def verification_passed(self, tool_name: str, method: str = "") -> None:
        self._emit("VERIFICATION_PASSED", tool_name=tool_name, method=method)

    def verification_failed(self, tool_name: str, reason: str = "") -> None:
        self._emit("VERIFICATION_FAILED", tool_name=tool_name, reason=reason[:300])

    def recovery_started(self, step_id: str, *, strategy: str = "") -> None:
        self._emit("RECOVERY_STARTED", step_id=step_id, strategy=strategy)

    def recovery_completed(self, step_id: str, *, strategy: str = "") -> None:
        self._emit("RECOVERY_COMPLETED", step_id=step_id, strategy=strategy)

    def recovery_failed(self, step_id: str, *, reason: str = "") -> None:
        self._emit("RECOVERY_FAILED", step_id=step_id, reason=reason[:300])

    def recovery_attempted(self, step_id: str, strategy: str = "") -> None:
        self._emit("RECOVERY_ATTEMPTED", step_id=step_id, strategy=strategy)

    def mission_completed(self, *, success: bool, summary: str = "") -> None:
        event = "MISSION_COMPLETED" if success else "MISSION_FAILED"
        self._emit(event, summary=summary[:300])
