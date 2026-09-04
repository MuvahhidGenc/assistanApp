"""Bounds that make dynamic planning safe to enable.

Every limit here answers one question: "is this mission still making progress,
or is it repeating itself?" The state lives in `mission.working_context` so it
survives replans, persistence and process restarts — a guard that resets on
replan would not be a guard at all.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

_GUARD_KEY = "execution_guard"


class GuardStop(StrEnum):
    """Why the guard halted the mission. Each maps to a user-facing reason."""

    ACTION_BUDGET = "action_budget"
    DURATION = "duration"
    REPEATED_ACTION = "repeated_action"
    REPEATED_PLAN = "repeated_plan"
    RECOVERY_BUDGET = "recovery_budget"
    NO_PROGRESS = "no_progress"
    SKILL_DEPTH = "skill_depth"
    REPEATED_SKILL = "repeated_skill"


@dataclass(frozen=True)
class GuardLimits:
    max_total_actions: int = 40
    max_duration_seconds: float = 900.0
    max_identical_actions: int = 3
    max_recovery_attempts: int = 12
    max_actions_without_progress: int = 8
    # Skills may call skills; these keep composition from becoming recursion.
    max_skill_depth: int = 4
    max_identical_skill_runs: int = 2


DEFAULT_LIMITS = GuardLimits()

_STOP_REASONS: dict[GuardStop, str] = {
    GuardStop.ACTION_BUDGET: "Bu gorev icin ayrilan islem sayisini doldurdum.",
    GuardStop.DURATION: "Bu gorev icin ayrilan sureyi doldurdum.",
    GuardStop.REPEATED_ACTION: "Ayni islemi tekrar tekrar deniyorum ve sonuc degismiyor.",
    GuardStop.REPEATED_PLAN: "Ayni plani yeniden urettim; farkli bir yol bulamiyorum.",
    GuardStop.RECOVERY_BUDGET: "Kurtarma denemelerinin sinirina ulastim.",
    GuardStop.NO_PROGRESS: "Birkac islemdir ilerleme saglayamiyorum.",
    GuardStop.SKILL_DEPTH: "Islem fazla ic ice gecti; daha derine inmiyorum.",
    GuardStop.REPEATED_SKILL: "Ayni islemi ayni girdiyle tekrar calistirmayi denedim.",
}


@dataclass(frozen=True)
class GuardVerdict:
    allowed: bool = True
    stop: GuardStop | None = None
    detail: str = ""

    @property
    def reason(self) -> str:
        if self.allowed or self.stop is None:
            return ""
        base = _STOP_REASONS.get(self.stop, "Gorevi guvenli sekilde durdurdum.")
        return f"{base} {self.detail}".strip()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _fingerprint(payload: Any) -> str:
    try:
        blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = repr(payload)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def plan_fingerprint(steps: list[Any]) -> str:
    """Identity of a plan: its ordered (tool, arguments, logical kind) triples.

    Titles and step ids are excluded so that a cosmetically different plan that
    would execute identically is still recognised as a repeat.
    """
    shape = []
    for step in steps:
        metadata = getattr(step, "metadata", None) or {}
        shape.append(
            {
                "tool": getattr(step, "tool_name", "") or "",
                "args": getattr(step, "tool_arguments", None) or {},
                "kind": metadata.get("logical_kind", ""),
            }
        )
    return _fingerprint(shape)


def action_fingerprint(tool_name: str, arguments: dict[str, Any] | None) -> str:
    return _fingerprint({"tool": tool_name or "", "args": arguments or {}})


class ExecutionGuard:
    """Mission-scoped budgets. Read and mutate through the mission itself."""

    def __init__(self, mission: Any, limits: GuardLimits = DEFAULT_LIMITS) -> None:
        self._mission = mission
        self._limits = limits

    @property
    def state(self) -> dict[str, Any]:
        context = self._mission.working_context
        state = context.get(_GUARD_KEY)
        if not isinstance(state, dict):
            state = {
                "started_at": _utc_now(),
                "action_count": 0,
                "actions": {},
                "plans": [],
                "actions_since_progress": 0,
                "skill_depth": 0,
                "skill_runs": {},
            }
            context[_GUARD_KEY] = state
        return state

    def _elapsed_seconds(self) -> float:
        raw = self.state.get("started_at")
        try:
            started = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return 0.0
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return (datetime.now(UTC) - started).total_seconds()

    def check_budget(self) -> GuardVerdict:
        """Cheap pre-flight check that does not depend on a specific action."""
        state = self.state
        count = int(state.get("action_count") or 0)
        if count >= self._limits.max_total_actions:
            return GuardVerdict(
                allowed=False,
                stop=GuardStop.ACTION_BUDGET,
                detail=f"({count} islem)",
            )

        elapsed = self._elapsed_seconds()
        if elapsed >= self._limits.max_duration_seconds:
            return GuardVerdict(
                allowed=False,
                stop=GuardStop.DURATION,
                detail=f"({int(elapsed)} saniye)",
            )

        stalled = int(state.get("actions_since_progress") or 0)
        if stalled >= self._limits.max_actions_without_progress:
            return GuardVerdict(allowed=False, stop=GuardStop.NO_PROGRESS)

        recoveries = int(getattr(self._mission, "recovery_count", 0) or 0)
        if recoveries >= self._limits.max_recovery_attempts:
            return GuardVerdict(
                allowed=False,
                stop=GuardStop.RECOVERY_BUDGET,
                detail=f"({recoveries} deneme)",
            )

        return GuardVerdict()

    def check_action(self, tool_name: str, arguments: dict[str, Any] | None) -> GuardVerdict:
        verdict = self.check_budget()
        if not verdict.allowed:
            return verdict

        key = action_fingerprint(tool_name, arguments)
        seen = int((self.state.get("actions") or {}).get(key, 0))
        if seen >= self._limits.max_identical_actions:
            return GuardVerdict(
                allowed=False,
                stop=GuardStop.REPEATED_ACTION,
                detail=f"({tool_name} x{seen})",
            )
        return GuardVerdict()

    def record_action(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        made_progress: bool = False,
    ) -> None:
        state = self.state
        state["action_count"] = int(state.get("action_count") or 0) + 1
        actions = state.setdefault("actions", {})
        key = action_fingerprint(tool_name, arguments)
        actions[key] = int(actions.get(key, 0)) + 1
        if made_progress:
            state["actions_since_progress"] = 0
        else:
            state["actions_since_progress"] = int(state.get("actions_since_progress") or 0) + 1

    def check_plan(self, steps: list[Any]) -> GuardVerdict:
        """Reject a plan that is identical to one already tried."""
        verdict = self.check_budget()
        if not verdict.allowed:
            return verdict
        if plan_fingerprint(steps) in (self.state.get("plans") or []):
            return GuardVerdict(allowed=False, stop=GuardStop.REPEATED_PLAN)
        return GuardVerdict()

    def record_plan(self, steps: list[Any]) -> None:
        state = self.state
        plans = state.setdefault("plans", [])
        fingerprint = plan_fingerprint(steps)
        if fingerprint not in plans:
            plans.append(fingerprint)

    # --- skill composition ---------------------------------------------

    def check_skill(self, skill_id: str, inputs: dict[str, Any] | None) -> GuardVerdict:
        """Composition is allowed; unbounded nesting and repetition are not."""
        verdict = self.check_budget()
        if not verdict.allowed:
            return verdict

        state = self.state
        depth = int(state.get("skill_depth") or 0)
        if depth >= self._limits.max_skill_depth:
            return GuardVerdict(
                allowed=False, stop=GuardStop.SKILL_DEPTH, detail=f"({depth} kademe)"
            )

        key = action_fingerprint(skill_id, inputs)
        runs = int((state.get("skill_runs") or {}).get(key, 0))
        if runs >= self._limits.max_identical_skill_runs:
            return GuardVerdict(
                allowed=False,
                stop=GuardStop.REPEATED_SKILL,
                detail=f"({skill_id} x{runs})",
            )
        return GuardVerdict()

    def enter_skill(self, skill_id: str, inputs: dict[str, Any] | None) -> None:
        state = self.state
        state["skill_depth"] = int(state.get("skill_depth") or 0) + 1
        runs = state.setdefault("skill_runs", {})
        key = action_fingerprint(skill_id, inputs)
        runs[key] = int(runs.get(key, 0)) + 1

    def exit_skill(self) -> None:
        state = self.state
        state["skill_depth"] = max(0, int(state.get("skill_depth") or 0) - 1)

    @property
    def skill_depth(self) -> int:
        return int(self.state.get("skill_depth") or 0)
