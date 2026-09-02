"""Phase 10.1 — goal achievement vs step success separation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class GoalCheckResult:
    step_success: bool = True
    state_verified: bool = False
    goal_achieved: bool = False
    status: str = "failed"  # completed | partial | failed
    message: str = ""
    audit: list[dict[str, Any]] = field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_goal_audit(mission: Any, event: str, **fields: Any) -> None:
    trail = mission.working_context.setdefault("goal_audit", [])
    trail.append({"event": event, "at": _utc_now(), **fields})
    if len(trail) > 100:
        mission.working_context["goal_audit"] = trail[-100:]


def record_goal_check(
    mission: Any,
    *,
    goal_achieved: bool,
    state_verified: bool,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    mission.working_context["goal_check"] = {
        "goal_achieved": goal_achieved,
        "state_verified": state_verified,
        "message": message,
        "details": dict(details or {}),
        "checked_at": _utc_now(),
    }
    append_goal_audit(
        mission,
        "GOAL_CHECK",
        goal_achieved=goal_achieved,
        state_verified=state_verified,
        message=message[:500],
    )


def finalize_mission_goal(mission: Any, *, all_steps_done: bool) -> GoalCheckResult:
    """Final gate: COMPLETED only when GOAL_ACHIEVED && STATE_VERIFIED."""
    check = mission.working_context.get("goal_check") or {}
    audit = list(mission.working_context.get("goal_audit") or [])

    if check:
        achieved = bool(check.get("goal_achieved"))
        verified = bool(check.get("state_verified"))
        message = str(check.get("message") or "").strip()
        if achieved and verified:
            return GoalCheckResult(
                step_success=all_steps_done,
                state_verified=True,
                goal_achieved=True,
                status="completed",
                message=message,
                audit=audit,
            )
        status = "partial" if check.get("details", {}).get("partial") else "failed"
        if achieved and not verified:
            status = "partial"
        return GoalCheckResult(
            step_success=all_steps_done,
            state_verified=verified,
            goal_achieved=achieved,
            status=status,
            message=message or "Hedef dogrulanamadi.",
            audit=audit,
        )

    # Legacy missions without explicit goal_check — preserve prior behavior.
    if all_steps_done:
        return GoalCheckResult(
            step_success=True,
            state_verified=True,
            goal_achieved=True,
            status="completed",
            message=str(mission.working_context.get("natural_summary") or ""),
            audit=audit,
        )
    return GoalCheckResult(
        step_success=False,
        state_verified=False,
        goal_achieved=False,
        status="failed",
        message="Adimlar tamamlanamadi.",
        audit=audit,
    )
