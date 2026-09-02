from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from hermes.config.settings import RiskLevel
from hermes.mission.models import Mission, MissionStatus, MissionStep
from hermes.mission.recovery.classifier import ErrorCategory, classify_error
from hermes.mission.recovery.config import RecoveryConfig
from hermes.mission.recovery.context import (
    FailureKind,
    IdempotencyResult,
    RecoveryAction,
    RecoveryContext,
    check_idempotency,
    step_is_fatal,
)
from hermes.mission.recovery.strategies import RecoveryStrategy, create_default_strategies
from hermes.security.policy_engine import PolicyDecision
from hermes.tools.registry import ToolRegistry
from hermes.utils.logging import get_logger

logger = get_logger(__name__)

ExecuteToolFn = Callable[[str, dict[str, Any], str], Awaitable[Any]]
VerifyFn = Callable[[MissionStep, Any, str], Awaitable[dict[str, Any]]]
ObserveToolFn = Callable[[str, dict[str, Any], str], Awaitable[Any]]
PolicyEvaluateFn = Callable[[str, dict[str, Any]], Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _risk_rank(level: RiskLevel | str | None) -> int:
    order = {
        RiskLevel.READ_ONLY: 0,
        RiskLevel.LOW_RISK: 1,
        RiskLevel.NORMAL_MODIFICATION: 2,
        RiskLevel.HIGH_RISK: 3,
        "read_only": 0,
        "low_risk": 1,
        "normal_modification": 2,
        "high_risk": 3,
    }
    if level is None:
        return 1
    if isinstance(level, RiskLevel):
        return order.get(level, 1)
    return order.get(str(level), 1)


@dataclass
class RecoveryOutcome:
    recovered: bool = False
    waiting_for_user: bool = False
    waiting_reason: str = ""
    user_messages: list[str] = field(default_factory=list)
    requires_approval: bool = False
    approval_action: RecoveryAction | None = None
    last_error: str = ""
    non_fatal_skip: bool = False


@dataclass
class RecoveryEngine:
    registry: ToolRegistry
    strategies: list[RecoveryStrategy] = field(default_factory=create_default_strategies)
    config: RecoveryConfig = field(default_factory=RecoveryConfig)
    evaluate_policy: PolicyEvaluateFn | None = None

    def _step_recovery_meta(self, step: MissionStep) -> dict[str, Any]:
        meta = step.metadata.setdefault("recovery", {})
        meta.setdefault("max_attempts", self.config.total_recovery_budget)
        meta.setdefault("current_attempt", 0)
        meta.setdefault("retryable", True)
        meta.setdefault("backoff_seconds", self.config.retry_backoff_seconds)
        meta.setdefault("last_error", "")
        meta.setdefault("strategy_attempts", {})
        return meta

    def _record_attempt(
        self,
        mission: Mission,
        step: MissionStep,
        *,
        strategy_id: str,
        reason: str,
        result: str,
        user_message: str = "",
    ) -> None:
        meta = self._step_recovery_meta(step)
        meta["current_attempt"] = int(meta.get("current_attempt", 0)) + 1
        meta["last_error"] = reason
        attempts = meta.setdefault("strategy_attempts", {})
        attempts[strategy_id] = int(attempts.get(strategy_id, 0)) + 1

        mission.recovery_strategy = strategy_id
        mission.recovery_reason = reason
        mission.recovery_result = result
        if not mission.recovery_started_at:
            mission.recovery_started_at = _utc_now()
        mission.recovery_finished_at = None
        mission.recovery_attempts.append(
            {
                "type": "recovery_attempt",
                "step_id": step.step_id,
                "strategy_id": strategy_id,
                "reason": reason,
                "result": result,
                "user_message": user_message,
                "current_attempt": meta["current_attempt"],
                "at": _utc_now(),
            }
        )

    def _risk_escalation_blocked(
        self,
        step: MissionStep,
        action: RecoveryAction,
    ) -> bool:
        original = step.risk_level
        if not original and step.tool_name:
            tool = self.registry.get(step.tool_name)
            if tool:
                original = tool.risk_level.value
        original_rank = _risk_rank(original)
        action_rank = _risk_rank(action.risk_level)
        if action_rank <= original_rank:
            return False
        if self.evaluate_policy is None:
            return action_rank >= _risk_rank(RiskLevel.HIGH_RISK)
        policy = self.evaluate_policy(action.tool_name, action.tool_arguments)
        return policy.decision in (PolicyDecision.REQUIRE_APPROVAL, PolicyDecision.DENY)

    def _select_strategies(self, ctx: RecoveryContext) -> list[RecoveryStrategy]:
        matched = [s for s in self.strategies if s.matches(ctx)]
        seen: set[str] = set()
        ordered: list[RecoveryStrategy] = []
        for strategy in matched:
            if strategy.strategy_id in seen:
                continue
            seen.add(strategy.strategy_id)
            ordered.append(strategy)
        return ordered[: self.config.max_alternative_strategies]

    async def attempt_recovery(
        self,
        mission: Mission,
        step: MissionStep,
        *,
        failure_kind: FailureKind,
        error_text: str,
        last_result: Any,
        verification_details: dict[str, Any] | None,
        run_id: str,
        execute_tool: ExecuteToolFn,
        verify: VerifyFn,
        observe_tool: ObserveToolFn | None = None,
    ) -> RecoveryOutcome:
        outcome = RecoveryOutcome()
        meta = self._step_recovery_meta(step)

        if not meta.get("retryable", True):
            outcome.last_error = error_text
            return outcome

        if ctx_category := classify_error(
            error_text=error_text,
            failure_kind=failure_kind.value,
            verification_details=verification_details,
            tool_name=step.tool_name or "",
        ):
            error_category = ctx_category
        else:
            error_category = ErrorCategory.UNKNOWN

        if error_category == ErrorCategory.AUTHENTICATION_REQUIRED:
            reason = error_text or "Authentication required"
            self._record_attempt(
                mission,
                step,
                strategy_id="auth_required",
                reason=reason,
                result="waiting_for_user",
            )
            mission.status = MissionStatus.WAITING_FOR_USER
            mission.waiting_for_user_reason = (
                "GitHub repo private gorunuyor. Authentication gerekiyor. "
                "Baglantiyi yapmamı ister misin?"
            )
            mission.recovery_finished_at = _utc_now()
            outcome.waiting_for_user = True
            outcome.waiting_reason = mission.waiting_for_user_reason
            outcome.user_messages.append(mission.waiting_for_user_reason)
            return outcome

        idem: IdempotencyResult = await check_idempotency(
            step, observe_tool=observe_tool, run_id=run_id
        )
        if idem.satisfied:
            outcome.user_messages.append(idem.user_message)
            synthetic = type("R", (), {"success": True, "output": idem.observation, "error": None})()
            verify_out = await verify(step, synthetic, run_id)
            if verify_out.get("verification_status") in ("verified", "not_required"):
                self._record_attempt(
                    mission,
                    step,
                    strategy_id="idempotency",
                    reason="already_satisfied",
                    result="recovered",
                    user_message="Sorunu cozdum, kuruluma devam ediyorum.",
                )
                mission.recovery_finished_at = _utc_now()
                outcome.recovered = True
                outcome.user_messages.append("Sorunu cozdum, kuruluma devam ediyorum.")
                return outcome

        ctx = RecoveryContext(
            mission=mission,
            step=step,
            failure_kind=failure_kind,
            error_category=error_category,
            error_text=error_text,
            last_result=last_result,
            verification_details=verification_details or {},
            run_id=run_id,
            strategy_attempts=dict(meta.get("strategy_attempts") or {}),
            total_attempts=int(meta.get("current_attempt", 0)),
        )

        budget = self.config.total_recovery_budget - ctx.total_attempts
        strategies = self._select_strategies(ctx)

        for strategy in strategies:
            if budget <= 0:
                break
            used = int(ctx.strategy_attempts.get(strategy.strategy_id, 0))
            if used >= min(strategy.max_attempts, self.config.max_same_strategy_attempts):
                continue

            for action in strategy.build_actions(ctx):
                if budget <= 0:
                    break
                if self._risk_escalation_blocked(step, action):
                    self._record_attempt(
                        mission,
                        step,
                        strategy_id=action.strategy_id,
                        reason="risk_escalation",
                        result="requires_approval",
                        user_message=action.user_message,
                    )
                    outcome.requires_approval = True
                    outcome.approval_action = action
                    outcome.user_messages.append(
                        "Alternatif cozum daha yuksek riskli; onay gerekiyor."
                    )
                    continue

                outcome.user_messages.append(action.user_message)
                backoff = float(meta.get("backoff_seconds") or self.config.retry_backoff_seconds)
                if backoff > 0:
                    await asyncio.sleep(backoff)

                result = await execute_tool(action.tool_name, action.tool_arguments, run_id)
                success = bool(getattr(result, "success", False))
                error = str(getattr(result, "error", "") or "")

                verify_out = await verify(step, result, run_id)
                v_status = verify_out.get("verification_status", "")

                if success and v_status in ("verified", "not_required"):
                    self._record_attempt(
                        mission,
                        step,
                        strategy_id=action.strategy_id,
                        reason=error_text,
                        result="recovered",
                        user_message=action.user_message,
                    )
                    mission.recovery_finished_at = _utc_now()
                    outcome.recovered = True
                    outcome.user_messages.append("Sorunu cozdum, kuruluma devam ediyorum.")
                    return outcome

                self._record_attempt(
                    mission,
                    step,
                    strategy_id=action.strategy_id,
                    reason=error or error_text,
                    result="failed",
                    user_message=action.user_message,
                )
                budget -= 1
                ctx.strategy_attempts[strategy.strategy_id] = (
                    int(ctx.strategy_attempts.get(strategy.strategy_id, 0)) + 1
                )
                ctx.total_attempts += 1

        if int(meta.get("current_attempt", 0)) >= int(meta.get("max_attempts", self.config.total_recovery_budget)):
            if not step_is_fatal(step):
                outcome.non_fatal_skip = True
                outcome.user_messages.append(
                    f"Bu adimi otomatik cozemiyorum. Sorun: {error_text[:200]}"
                )
                mission.recovery_finished_at = _utc_now()
                return outcome

            reason = (
                f"Bu adimi otomatik olarak cozemiyorum. Sorun: {error_text[:300]}"
            )
            mission.status = MissionStatus.WAITING_FOR_USER
            mission.waiting_for_user_reason = reason
            mission.recovery_finished_at = _utc_now()
            outcome.waiting_for_user = True
            outcome.waiting_reason = reason
            outcome.last_error = error_text
            outcome.user_messages.append(reason)
            return outcome

        outcome.last_error = error_text
        if not step_is_fatal(step):
            outcome.non_fatal_skip = True
            outcome.user_messages.append(
                f"Bu adimi otomatik cozemiyorum. Sorun: {error_text[:200]}"
            )
        return outcome
