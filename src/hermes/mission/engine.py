from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from hermes.mission.models import Mission, MissionStatus, MissionStep, MissionStepStatus, StepAction
from hermes.mission.planner import MissionPlanner, PlannerResult
from hermes.mission.recovery import FailureKind, RecoveryConfig, RecoveryEngine
from hermes.mission.store import MissionStore
from hermes.mission.validator import validate_mission_steps
from hermes.mission.step_context import (
    LOGICAL_KIND_FORMAT_SYSTEM_INFO,
    find_dependency_output,
    format_system_info_file_content,
    is_placeholder_content,
    missing_system_info_fields,
    prepared_system_info_content_valid,
    record_search_files_context,
    record_step_tool_output,
    resolve_step_tool_arguments,
    resolve_mission_browser_url,
    resolve_search_matches_fallback,
    run_copy_search_matches,
    record_copy_search_context,
    copy_search_result_summary,
    search_empty_user_message,
    search_result_summary,
    system_info_output_valid,
    filter_search_matches_for_pattern,
    resolve_verified_search_matches,
    user_goal_preserves_folder_structure,
)
from hermes.mission.output_tracking import (
    initialize_output_tracking,
    missing_outputs_summary,
    outputs_requirement_met,
    record_output_completion,
)
from hermes.mission.write_content import planner_debug_enabled
from hermes.security.policy_engine import PolicyDecision
from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.tools.executor import ToolApprovalRequiredError, ToolExecutor
from hermes.tools.manifest import LocalToolRequest
from hermes.tools.registry import ToolRegistry
from hermes.tools.verifiers import VerificationStatus, VerifierContext, VerifierRegistry, create_default_verifier_registry
from hermes.tools.verifiers.registry import is_read_only_observe_tool
from hermes.utils.logging import get_logger

logger = get_logger(__name__)

ExecuteLocalTool = Callable[[LocalToolRequest, str], Awaitable[Any]]


@dataclass
class EngineResult:
    handled: bool = False
    success: bool = False
    summary: str = ""
    fallback_recommended: bool = False
    mission_id: str | None = None
    step_summaries: list[str] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    waiting_for_user: bool = False


@dataclass
class _StepRunOutcome:
    continue_plan: bool = True
    step_done: bool = False
    mission_failed: bool = False
    waiting_for_user: bool = False
    summary_line: str = ""
    user_messages: list[str] = field(default_factory=list)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _topological_steps(steps: list[MissionStep]) -> list[MissionStep]:
    by_id = {step.step_id: step for step in steps}
    ordered: list[MissionStep] = []
    seen: set[str] = set()

    def visit(step: MissionStep) -> None:
        if step.step_id in seen:
            return
        for dep in step.depends_on:
            parent = by_id.get(dep)
            if parent is not None:
                visit(parent)
        if step.step_id not in seen:
            ordered.append(step)
            seen.add(step.step_id)

    for step in steps:
        visit(step)
    return ordered


def _merge_working_context_bucket(
    mission: Mission, fresh: Mission, key: str
) -> None:
    merged = dict(fresh.working_context.get(key) or {})
    merged.update(mission.working_context.get(key) or {})
    mission.working_context[key] = merged


class MissionEngine:
    """
    Plans and executes missions through ToolRegistry → ToolExecutor → Policy → Approval → Audit.
    """

    def __init__(
        self,
        store: MissionStore,
        registry: ToolRegistry,
        executor: ToolExecutor,
        *,
        execute_local_tool: ExecuteLocalTool | None = None,
        verifier_registry: VerifierRegistry | None = None,
        verification_timeout: float = 10.0,
        recovery_engine: RecoveryEngine | None = None,
        recovery_config: RecoveryConfig | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._executor = executor
        self._execute_local_tool = execute_local_tool
        self._verifiers = verifier_registry or create_default_verifier_registry()
        self._verification_timeout = verification_timeout
        self._recovery = recovery_engine or RecoveryEngine(
            registry=registry,
            config=recovery_config or RecoveryConfig(),
            evaluate_policy=lambda name, args: executor._policy.evaluate(name, args),  # noqa: SLF001
        )
        self._active_planner: MissionPlanner | None = None

    def _merge_mission_runtime_state(self, mission: Mission) -> None:
        fresh = self._store.load(mission.mission_id)
        if fresh is None:
            return
        _merge_working_context_bucket(mission, fresh, "step_outputs")
        _merge_working_context_bucket(mission, fresh, "prepared_contents")
        _merge_working_context_bucket(mission, fresh, "search_results")
        if len(fresh.tool_results) > len(mission.tool_results):
            mission.tool_results = list(fresh.tool_results)
        fresh_by_id = {item.step_id: item for item in fresh.steps}
        for step in mission.steps:
            fresh_step = fresh_by_id.get(step.step_id)
            if fresh_step is None:
                continue
            for meta_key in ("prepared_content", "tool_output", "logical_kind"):
                if meta_key in fresh_step.metadata and meta_key not in step.metadata:
                    step.metadata[meta_key] = fresh_step.metadata[meta_key]

    def _maybe_reuse_cached_search(
        self, mission: Mission, step: MissionStep, outcome: _StepRunOutcome
    ) -> _StepRunOutcome | None:
        """Avoid re-running search when verified PDF scan results already exist."""
        matches, pattern, _ = resolve_search_matches_fallback(
            mission, preferred_step_ids=(step.step_id,)
        )
        if not matches:
            return None
        payload = {
            "verified": True,
            "pattern": pattern,
            "matches": matches,
            "count": len(matches),
            "reused_from_cache": True,
        }
        record_step_tool_output(mission, step, payload)
        record_search_files_context(mission, step, payload)
        step.execution_status = "succeeded"
        step.verification_status = VerificationStatus.VERIFIED.value
        step.verification_method = "search_files_cached"
        step.status = MissionStepStatus.COMPLETED
        step.completed_at = _utc_now()
        step.result_summary = search_result_summary(payload)
        self._store.save(mission)
        outcome.summary_line = f"- {step.title}: {step.result_summary} (onbellek)"
        outcome.step_done = True
        return outcome

    def _maybe_skip_existing_folder(
        self,
        mission: Mission,
        step: MissionStep,
        resolved_args: dict[str, Any],
        outcome: _StepRunOutcome,
    ) -> _StepRunOutcome | None:
        """Skip create_folder when destination already exists (idempotent PDF prep)."""
        from pathlib import Path as _Path

        from hermes.tools.windows.file_tools import resolve_user_path

        raw = str(resolved_args.get("path") or step.tool_arguments.get("path") or "").strip()
        if not raw:
            return None
        try:
            folder = resolve_user_path(raw)
        except ValueError:
            return None
        if not folder.is_dir():
            return None
        payload = {"path": str(folder.resolve()), "already_existed": True}
        record_step_tool_output(mission, step, payload)
        step.execution_status = "succeeded"
        step.verification_status = VerificationStatus.VERIFIED.value
        step.verification_method = "create_folder_exists"
        step.status = MissionStepStatus.COMPLETED
        step.completed_at = _utc_now()
        step.result_summary = f"Klasor zaten mevcut: {_Path(folder).name}"
        self._store.save(mission)
        outcome.summary_line = f"- {step.title}: {step.result_summary}"
        outcome.step_done = True
        return outcome

    async def run(
        self,
        mission_id: str,
        planner: MissionPlanner,
        *,
        force_replan: bool = False,
    ) -> EngineResult:
        mission = self._store.load(mission_id)
        if mission is None:
            return EngineResult(fallback_recommended=True, summary="Mission bulunamadi.")

        self._active_planner = planner

        from hermes.mission.audit import MissionAuditor

        auditor = MissionAuditor(mission_id)
        auditor.mission_created(mission.user_goal)
        parsed_goal = mission.working_context.get("parsed_goal")
        if isinstance(parsed_goal, dict):
            auditor.goal_parsed(parsed_goal)

        if mission.plan_validated and mission.steps and not force_replan:
            from hermes.mission.planner import normalize_file_operations_plan

            normalized = normalize_file_operations_plan(
                mission.user_goal, mission.steps, self._registry
            )
            if [s.step_id for s in normalized] != [s.step_id for s in mission.steps]:
                mission.steps = normalized
                mission.plan_source = "file_operations_heuristic"
                self._store.save(mission)
            mission.status = MissionStatus.RUNNING
            initialize_output_tracking(mission)
            self._store.save(mission)
            return await self._execute_plan(mission, auditor=auditor)

        mission.status = MissionStatus.PLANNING
        self._store.save(mission)

        plan_result = await planner.create_plan(mission, force_replan=force_replan)
        if not plan_result.success or not plan_result.steps:
            mission.errors.append(
                {
                    "type": "planner_failure",
                    "error": plan_result.error or "Plan olusturulamadi",
                    "at": _utc_now(),
                }
            )
            self._store.save(mission)
            return EngineResult(
                handled=False,
                fallback_recommended=True,
                mission_id=mission_id,
                summary=plan_result.error or "Planner basarisiz",
            )

        validation = validate_mission_steps(plan_result.steps, self._registry)
        if not validation.ok:
            mission.errors.append(
                {
                    "type": "plan_validation",
                    "errors": validation.errors,
                    "at": _utc_now(),
                }
            )
            self._store.save(mission)
            auditor.plan_rejected(errors=validation.errors)
            return EngineResult(
                handled=False,
                fallback_recommended=True,
                mission_id=mission_id,
                summary="Plan dogrulamasi basarisiz: " + "; ".join(validation.errors[:3]),
            )

        mission.steps = validation.steps
        mission.plan_validated = True
        mission.plan_source = plan_result.source
        mission.plan_created_at = _utc_now()
        mission.status = MissionStatus.RUNNING
        mission.current_step_id = validation.steps[0].step_id if validation.steps else None
        if plan_result.raw_response:
            mission.metadata["plan_raw_preview"] = plan_result.raw_response[:2000]
        if plan_result.argument_diagnostics:
            mission.metadata["argument_diagnostics"] = plan_result.argument_diagnostics
            if planner_debug_enabled():
                mission.metadata["plan_debug"] = {
                    "raw_preview": plan_result.raw_response,
                    "argument_diagnostics": plan_result.argument_diagnostics,
                    "normalized_steps": [step.to_dict() for step in validation.steps],
                }
        mission.important_decisions.append(
            {
                "type": "plan_created",
                "source": plan_result.source,
                "step_count": len(validation.steps),
                "at": mission.plan_created_at,
            }
        )
        initialize_output_tracking(mission)
        self._store.save(mission)
        auditor.plan_created(source=plan_result.source, step_count=len(validation.steps))
        auditor.plan_validated(step_count=len(validation.steps))
        return await self._execute_plan(mission, auditor=auditor)

    async def _execute_plan(self, mission: Mission, *, auditor: Any | None = None) -> EngineResult:
        if mission.status == MissionStatus.WAITING_FOR_USER:
            reason = mission.waiting_for_user_reason or "Kullanici yaniti bekleniyor."
            return EngineResult(
                handled=True,
                success=False,
                summary=f"Mission bekliyor: {reason}",
                mission_id=mission.mission_id,
                waiting_for_user=True,
                user_messages=[reason],
            )

        mission.status = MissionStatus.RUNNING
        initialize_output_tracking(mission)
        self._store.save(mission)

        summaries: list[str] = []
        user_messages: list[str] = []
        failed = False
        waiting = False
        run_id = f"mission-{mission.mission_id[:12]}"

        for step in _topological_steps(mission.steps):
            mission = self._store.load(mission.mission_id) or mission
            if mission.status == MissionStatus.CANCELLED:
                return EngineResult(
                    handled=True,
                    success=False,
                    summary="Gorev iptal edildi.",
                    mission_id=mission.mission_id,
                    step_summaries=summaries,
                    user_messages=user_messages,
                )

            fresh_step = next(
                (item for item in mission.steps if item.step_id == step.step_id),
                step,
            )
            if fresh_step.status in (MissionStepStatus.COMPLETED, MissionStepStatus.SKIPPED):
                continue
            step = fresh_step

            if auditor is not None:
                auditor.step_started(step.step_id, step.title)
                if step.tool_name:
                    auditor.tool_selected(step.tool_name, step.step_id)

            outcome = await self._run_step(mission, step, run_id, auditor=auditor)
            user_messages.extend(outcome.user_messages)
            if outcome.summary_line:
                summaries.append(outcome.summary_line)

            if outcome.waiting_for_user:
                waiting = True
                break
            if outcome.mission_failed:
                failed = True
                break
            if not outcome.continue_plan:
                break

        summary_text = "\n".join(summaries) if summaries else "Mission plani calistirildi."
        if waiting:
            reason = mission.waiting_for_user_reason or "Kullanici yaniti bekleniyor."
            mission.summary = summary_text[:4000]
            self._store.save(mission)
            return EngineResult(
                handled=True,
                success=False,
                summary=f"{reason}\n\n{summary_text}",
                mission_id=mission.mission_id,
                step_summaries=summaries,
                user_messages=user_messages,
                waiting_for_user=True,
            )

        if failed:
            replan_decision = None
            failed_step = next(
                (item for item in mission.steps if item.status == MissionStepStatus.FAILED),
                None,
            )
            if failed_step is not None:
                from hermes.mission.replan import (
                    analyze_failure_for_replan,
                    apply_replan,
                    build_partial_success_summary,
                )

                replan_decision = analyze_failure_for_replan(mission, failed_step)
                if replan_decision.should_replan and self._active_planner is not None:
                    apply_replan(mission, replan_decision)
                    self._store.save(mission)
                    replan_result = await self.run(
                        mission.mission_id,
                        self._active_planner,
                        force_replan=True,
                    )
                    if replan_result.handled:
                        return replan_result

            mission.status = MissionStatus.FAILED
            from hermes.agent.mission_progress import format_partial_failure_summary

            natural_fail = format_partial_failure_summary(mission, summaries)
            if replan_decision and replan_decision.should_replan is False and mission.completed_steps > 0:
                from hermes.mission.replan import build_partial_success_summary

                natural_fail = build_partial_success_summary(mission, failed_reason=replan_decision.reason)
            use_natural = bool(
                natural_fail
                and mission.completed_steps > 0
                and (
                    "kopyaladim" in natural_fail.casefold()
                    or "tamamlandi" in natural_fail.casefold()
                )
            )
            if use_natural:
                summary_text = natural_fail
            mission.summary = summary_text[:4000]
            self._store.save(mission)
            if auditor is not None:
                auditor.mission_completed(success=False, summary=summary_text)
            if use_natural:
                return EngineResult(
                    handled=True,
                    success=False,
                    summary=summary_text,
                    mission_id=mission.mission_id,
                    step_summaries=summaries,
                    user_messages=user_messages,
                )
            return EngineResult(
                handled=True,
                success=False,
                summary=f"Mission kismen tamamlandi:\n{summary_text}",
                mission_id=mission.mission_id,
                step_summaries=summaries,
                user_messages=user_messages,
            )

        if not outputs_requirement_met(mission):
            mission.status = MissionStatus.FAILED
            summary_text = f"{summary_text}\n- {missing_outputs_summary(mission)}"
            mission.summary = summary_text[:4000]
            self._store.save(mission)
            return EngineResult(
                handled=True,
                success=False,
                summary=f"Mission tamamlanamadi:\n{summary_text}",
                mission_id=mission.mission_id,
                step_summaries=summaries,
                user_messages=user_messages,
            )

        from hermes.mission.goal_verification import finalize_mission_goal

        goal_result = finalize_mission_goal(mission, all_steps_done=True)
        if goal_result.status != "completed":
            mission.status = MissionStatus.FAILED if goal_result.status == "failed" else MissionStatus.FAILED
            summary_text = goal_result.message or summary_text
            if goal_result.status == "partial":
                summary_text = goal_result.message or "Gorev kismen tamamlandi."
            mission.summary = summary_text[:4000]
            self._store.save(mission)
            if auditor is not None:
                auditor.mission_completed(success=False, summary=summary_text)
            return EngineResult(
                handled=True,
                success=False,
                summary=summary_text,
                mission_id=mission.mission_id,
                step_summaries=summaries,
                user_messages=user_messages or ([summary_text] if summary_text else []),
            )

        mission.status = MissionStatus.COMPLETED
        natural_summary = str(mission.working_context.get("natural_summary") or "").strip()
        if natural_summary:
            summary_text = natural_summary
            user_messages.append(natural_summary)
        mission.summary = summary_text[:4000]
        self._store.save(mission)
        if auditor is not None:
            auditor.mission_completed(success=True, summary=summary_text)
        completion_prefix = "Gorev tamamlandi." if natural_summary else "Mission tamamlandi:"
        completion_body = summary_text if natural_summary else f"\n{summary_text}"
        return EngineResult(
            handled=True,
            success=True,
            summary=f"{completion_prefix}{completion_body}",
            mission_id=mission.mission_id,
            step_summaries=summaries,
            user_messages=user_messages,
        )

    async def _run_step(
        self, mission: Mission, step: MissionStep, run_id: str, *, auditor: Any | None = None
    ) -> _StepRunOutcome:
        self._merge_mission_runtime_state(mission)
        step = next((item for item in mission.steps if item.step_id == step.step_id), step)
        outcome = _StepRunOutcome()
        mission.current_step_id = step.step_id
        step.status = MissionStepStatus.RUNNING
        step.started_at = _utc_now()
        self._store.save(mission)

        if step.action == StepAction.LOGICAL:
            return await self._run_logical_step(mission, step, outcome)

        if not step.tool_name:
            step.execution_status = "failed"
            step.status = MissionStepStatus.FAILED
            step.completed_at = _utc_now()
            step.result_summary = "Tool adimi icin tool_name eksik"
            outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
            outcome.mission_failed = True
            self._store.save(mission)
            return outcome

        resolved_args = resolve_step_tool_arguments(step, mission)
        if step.tool_name in ("write_file", "rename_path", "open_path", "copy_file", "move_file", "read_screen_text"):
            step.tool_arguments = resolved_args

        if step.tool_name == "search_files" and step.step_id in ("scan_pdfs", "search_source_files"):
            cached_outcome = self._maybe_reuse_cached_search(mission, step, outcome)
            if cached_outcome is not None:
                return cached_outcome

        if step.tool_name == "create_folder" and step.step_id == "prepare_important_folder":
            folder_skip = self._maybe_skip_existing_folder(mission, step, resolved_args, outcome)
            if folder_skip is not None:
                return folder_skip

        if step.tool_name == "write_file":
            content = str(resolved_args.get("content") or "")
            path = resolved_args.get("path")
            if is_placeholder_content(content):
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = (
                    "write_file content cozulemedi — onceki tool ciktisi bekleniyordu"
                )
                mission.errors.append(
                    {
                        "type": "unresolved_write_content",
                        "step_id": step.step_id,
                        "depends_on": step.depends_on,
                        "at": _utc_now(),
                    }
                )
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                outcome.user_messages.append(
                    "write_file placeholder ile calistirilmadi; onceki adim ciktisi gerekli."
                )
                return outcome
            step.tool_arguments = resolved_args
            logger.info(
                "write_file_pre_execute",
                mission_id=mission.mission_id,
                step_id=step.step_id,
                path=path,
                content=content,
                content_length=len(content),
            )
            step.metadata["pre_execute_arguments"] = {
                "path": path,
                "content": content,
            }
            if step.step_id == "write_report_file":
                from hermes.mission.report_audit import log_report_chain

                log_report_chain(
                    mission,
                    "plan",
                    INTENT="write_report_file",
                    PLANNED_PATH=str(path or ""),
                    RESOLVED_PATH=str(path or ""),
                    UNIQUE_IF_EXISTS=bool(resolved_args.get("unique_if_exists")),
                )
            self._store.save(mission)
            preview = content if len(content) <= 120 else content[:117] + "..."
            outcome.user_messages.append(
                f"write_file hazir: path={path!s}, content={preview!r}"
            )

        if step.tool_name == "open_path" and not str(resolved_args.get("path") or "").strip():
            step.execution_status = "failed"
            step.status = MissionStepStatus.FAILED
            step.completed_at = _utc_now()
            step.result_summary = "open_path hedefi cozulemedi — onceki adim ciktisi gerekli"
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
            outcome.mission_failed = True
            return outcome

        policy = self._executor._policy.evaluate(step.tool_name, step.tool_arguments)  # noqa: SLF001
        if policy.decision == PolicyDecision.DENY:
            step.execution_status = "failed"
            step.status = MissionStepStatus.BLOCKED
            step.completed_at = _utc_now()
            step.result_summary = f"Policy reddetti: {policy.reason}"
            mission.errors.append(
                {
                    "type": "security_rejection",
                    "step_id": step.step_id,
                    "tool_name": step.tool_name,
                    "reason": policy.reason,
                    "at": _utc_now(),
                }
            )
            outcome.summary_line = f"- {step.title}: GUVENLIK REDDI"
            outcome.mission_failed = True
            self._store.save(mission)
            return outcome

        return await self._execute_verify_recover(mission, step, run_id, auditor=auditor)

    async def _run_logical_step(
        self, mission: Mission, step: MissionStep, outcome: _StepRunOutcome
    ) -> _StepRunOutcome:
        from hermes.mission.compound_goal import (
            LOGICAL_KIND_PRODUCE_FILE_SUMMARY,
            LOGICAL_KIND_READ_VERIFY_FILES,
            build_file_content_summary,
            parse_compound_file_goal,
            run_read_verify_files,
        )

        kind = step.metadata.get("logical_kind")
        if kind == LOGICAL_KIND_READ_VERIFY_FILES:
            file_path = str(step.metadata.get("file_path") or "")
            expected = str(step.metadata.get("expected_content") or "")
            report = run_read_verify_files(file_path, expected)
            bucket = mission.working_context.setdefault("read_verifications", [])
            if isinstance(bucket, list):
                bucket.append(report)
            else:
                mission.working_context["read_verifications"] = [report]
            if not report.get("ok"):
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = str(report.get("error") or "Icerik dogrulanamadi")
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            step.metadata["read_report"] = report
            record_step_tool_output(mission, step, report)
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "compound_read_verify"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            name = str(report.get("name") or "")
            step.result_summary = f"{name}: {report.get('content', '')}"
            self._store.save(mission)
            outcome.user_messages.append(
                build_file_content_summary({"read_verifications": [report]})
            )
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == LOGICAL_KIND_PRODUCE_FILE_SUMMARY:
            reads = mission.working_context.get("read_verifications") or []
            summary = build_file_content_summary({"read_verifications": reads})
            if not summary:
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = "Icerik ozeti olusturulamadi"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            mission.working_context["content_summary"] = summary
            parsed = parse_compound_file_goal(mission.user_goal)
            if parsed is not None:
                from hermes.mission.compound_goal import build_compound_mission_natural_summary

                natural = build_compound_mission_natural_summary(parsed, mission.working_context)
                mission.working_context["natural_summary"] = natural
            step.metadata["prepared_content"] = summary
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "compound_file_summary"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = summary
            self._store.save(mission)
            outcome.user_messages.append(summary)
            outcome.summary_line = f"- {step.title}: {summary}"
            outcome.step_done = True
            return outcome

        if kind == "copy_search_matches":
            from hermes.mission.copy_audit import audit_copy_chain
            from hermes.mission.step_context import resolve_verified_search_matches

            destination = str(step.metadata.get("destination") or "")
            matches, pattern, search_step_id = resolve_verified_search_matches(mission, step)
            audit_copy_chain(
                "copy_search_matches_start",
                mission_id=mission.mission_id,
                step_id=step.step_id,
                depends_on=list(step.depends_on),
                destination=destination or None,
                matched_files=[str(item.get("path") or "") for item in matches],
                result_count=len(matches),
                extra={"search_step_id": search_step_id, "pattern": pattern},
            )
            if search_step_id is None:
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = "Dogrulanmis arama sonucu bulunamadi"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            preserve_structure = step.metadata.get("preserve_folder_structure")
            if preserve_structure is None:
                preserve_structure = user_goal_preserves_folder_structure(mission.user_goal)
            source_root = str(
                (mission.working_context.get("search_results") or {})
                .get(search_step_id, {})
                .get("source_location")
                or ""
            )
            if not destination:
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = "Hedef klasor belirtilmedi"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            if not matches:
                empty_info = {
                    "pattern": pattern,
                    "folder": source_root,
                    "source_location": source_root,
                }
                empty_msg = search_empty_user_message(empty_info)
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "copy_search_matches_empty"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = empty_msg
                self._store.save(mission)
                outcome.user_messages.append(empty_msg)
                outcome.summary_line = f"- {step.title}: {empty_msg}"
                outcome.step_done = True
                return outcome

            report = await run_copy_search_matches(
                matches,
                destination,
                pattern=pattern,
                preserve_structure=bool(preserve_structure),
                source_root=source_root or None,
                mission_id=mission.mission_id,
                step_id=step.step_id,
            )
            record_copy_search_context(mission, step, report)

            if not report.get("ok"):
                missing = list(report.get("missing") or [])
                mismatch = list(report.get("size_mismatch") or [])
                failed_files = list(report.get("failed_files") or [])
                step.metadata["copy_verification"] = {
                    "missing": missing[:20],
                    "size_mismatch": mismatch[:20],
                    "failed_files": failed_files[:20],
                    "skipped_non_pdf": list(report.get("skipped_non_pdf") or [])[:20],
                    "errors": list(report.get("errors") or [])[:10],
                    "partial": bool(report.get("partial")),
                }
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = copy_search_result_summary(report)
                if missing:
                    step.result_summary = f"Kopya dogrulanamadi: {len(missing)} dosya eksik"
                elif mismatch:
                    step.result_summary = f"Kopya dogrulanamadi: {len(mismatch)} dosya boyut uyumsuz"
                elif failed_files:
                    step.result_summary = copy_search_result_summary(report)
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome

            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "copy_search_matches_filesystem"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = copy_search_result_summary(report)
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == LOGICAL_KIND_FORMAT_SYSTEM_INFO:
            info = find_dependency_output(mission, step, tool_name="get_system_info")
            if not info:
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = "get_system_info ciktisi bulunamadi"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            missing = missing_system_info_fields(info)
            if missing:
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = f"Eksik sistem alanlari: {', '.join(missing)}"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            content = format_system_info_file_content(info)
            if not prepared_system_info_content_valid(content, info):
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = "Hazirlanan dosya icerigi dogrulanamadi"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            step.metadata["prepared_content"] = content
            mission.working_context.setdefault("prepared_contents", {})[step.step_id] = content
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "prepared_system_info_content"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = f"Icerik hazir ({len(content)} karakter)"
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == "verify_goal_completion":
            completed = sum(
                1 for item in mission.steps if item.status == MissionStepStatus.COMPLETED
            )
            summary = f"{completed} adimin tamamlandi ve kontrol edildi."
            mission.working_context["natural_summary"] = summary
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "goal_completion"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = summary
            self._store.save(mission)
            outcome.user_messages.append(summary)
            outcome.summary_line = f"- {step.title}: {summary}"
            outcome.step_done = True
            return outcome

        if kind == "produce_creation_audit":
            from pathlib import Path as _Path

            folder_path = str(step.metadata.get("folder_path") or "")
            audit_lines: list[str] = []
            verified_files: list[str] = []

            if folder_path:
                folder = _Path(folder_path)
                if folder.is_dir():
                    audit_lines.append(f"Klasor: {folder.name}")
                    for entry in sorted(folder.iterdir()):
                        if entry.is_file():
                            audit_lines.append(entry.name)
                            verified_files.append(str(entry.resolve()))

            if not audit_lines:
                for prev in mission.steps:
                    if prev.status != MissionStepStatus.COMPLETED:
                        continue
                    if prev.tool_name == "write_file":
                        path = str((prev.tool_arguments or {}).get("path") or "")
                        if path and _Path(path).is_file():
                            audit_lines.append(_Path(path).name)
                            verified_files.append(str(_Path(path).resolve()))
                    if prev.tool_name == "create_folder":
                        path = str((prev.tool_arguments or {}).get("path") or "")
                        if path and _Path(path).is_dir():
                            audit_lines.insert(0, f"Klasor: {_Path(path).name}")

            extensions = sorted({ _Path(name).suffix for name in audit_lines if "." in name })
            type_summary = ", ".join(ext for ext in extensions if ext) if extensions else ""
            summary = (
                f"Olusturduklarim: {audit_lines[0] if audit_lines else 'klasor'}"
                + (f" — dosyalar: {', '.join(audit_lines[1:])}." if len(audit_lines) > 1 else ".")
            )
            if type_summary:
                summary = summary.rstrip(".") + f" (turler: {type_summary})."
            mission.working_context["natural_summary"] = summary
            mission.working_context["verified_created_files"] = verified_files
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "creation_audit"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = summary
            self._store.save(mission)
            outcome.user_messages.append(summary)
            outcome.summary_line = f"- {step.title}: {summary}"
            outcome.step_done = True
            return outcome

        if kind == "observe_state":
            from pathlib import Path as _Path

            from hermes.mission.goal_verification import append_goal_audit
            from hermes.mission.reality_verification import filesystem_snapshot

            observe_type = str(step.metadata.get("observe_type") or "filesystem")
            observed: dict[str, Any] = {"type": observe_type}
            path = str(step.metadata.get("path") or "")
            snapshot_key = str(step.metadata.get("snapshot_key") or "")

            if observe_type in {"filesystem", "filesystem_snapshot"} and path:
                folder = _Path(path)
                snap = filesystem_snapshot(folder)
                observed.update(snap)
                if snapshot_key:
                    mission.working_context.setdefault("filesystem_snapshots", {})[snapshot_key] = snap
                    append_goal_audit(
                        mission,
                        "STATE_BEFORE" if snapshot_key == "before" else "STATE_AFTER",
                        path=snap.get("path"),
                        file_count=snap.get("file_count"),
                    )
            elif observe_type == "search_results":
                for prev in mission.steps:
                    if prev.tool_name == "search_files" and prev.status == MissionStepStatus.COMPLETED:
                        output = (prev.metadata.get("tool_output") or {})
                        if isinstance(output, dict):
                            observed["match_count"] = int(output.get("count") or 0)
                            observed["pattern"] = output.get("pattern")
                        break

            mission.working_context.setdefault("observed_state", {})[step.step_id] = observed
            summary = f"Gozlem tamamlandi ({observe_type})"
            if observed.get("file_count") is not None:
                summary = f"{observed.get('file_count', 0)} dosya incelendi"

            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "observe_state"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = summary
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {summary}"
            outcome.step_done = True
            return outcome

        if kind == "prepare_organize_folders":
            from pathlib import Path as _Path

            from hermes.mission.step_context import extension_destination_map

            source_root = str(step.metadata.get("source_root") or "")
            for folder in extension_destination_map(source_root).values():
                _Path(folder).mkdir(parents=True, exist_ok=True)
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "organize_prepare_folders"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = "Duzenleme klasorleri hazir"
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == "organize_files_by_type":
            from hermes.mission.goal_verification import append_goal_audit
            from hermes.mission import step_context as sc

            source_root = str(step.metadata.get("source_root") or "")
            matches, pattern, search_step_id = sc.resolve_verified_search_matches(mission, step)
            append_goal_audit(mission, "ACT", action="organize_files_by_type", match_count=len(matches))
            report = await sc.run_organize_files_by_type(
                matches,
                source_root,
                mission_id=mission.mission_id,
                step_id=step.step_id,
            )
            mission.working_context.setdefault("organize_results", {})[step.step_id] = report
            mission.working_context.setdefault("mutation_audit", []).extend(report.get("mutations") or [])
            moved = int(report.get("moved") or report.get("copied") or 0)
            if not report.get("ok"):
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                if moved == 0:
                    step.result_summary = "Duzenlenecek uygun dosya tasindi veya hic dosya tasinmadi"
                else:
                    step.result_summary = f"Duzenleme dogrulanamadi ({report.get('failed', 0)} hata)"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "organize_files_by_type_filesystem"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = f"{moved} dosya tasindi"
            append_goal_audit(mission, "VERIFY", action="organize_files_by_type", moved=moved)
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == "verify_desktop_organize":
            from pathlib import Path as _Path

            from hermes.mission.goal_verification import record_goal_check
            from hermes.mission.reality_verification import compare_snapshots

            source_root = str(step.metadata.get("source_root") or "")
            snaps = mission.working_context.get("filesystem_snapshots") or {}
            before = snaps.get("before") or {}
            after = snaps.get("after") or {}
            if not after and source_root:
                from hermes.mission.reality_verification import filesystem_snapshot

                after = filesystem_snapshot(_Path(source_root))
            diff = compare_snapshots(before, after)
            organize = mission.working_context.get("organize_results") or {}
            moved = 0
            for report in organize.values():
                if isinstance(report, dict):
                    moved += int(report.get("moved") or report.get("copied") or 0)
            changed = moved > 0 or bool(diff.removed) or bool(diff.created)
            if not changed:
                message = (
                    "Duzenleme icin uygun dosyalari belirledim ancak hicbir dosya tasinmadi."
                )
                record_goal_check(
                    mission,
                    goal_achieved=False,
                    state_verified=True,
                    message=message,
                    details={"partial": True, "copied": 0},
                )
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = message
                mission.working_context["natural_summary"] = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.mission_failed = True
                return outcome
            names = ", ".join(diff.removed[:6]) if diff.removed else f"{moved} dosya"
            message = f"Masaustu duzenlendi: {names} uygun klasorlere tasindi."
            record_goal_check(
                mission,
                goal_achieved=True,
                state_verified=True,
                message=message,
                details={"moved": moved, "removed_from_root": diff.removed},
            )
            mission.working_context["natural_summary"] = message
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "goal_achievement"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = message
            self._store.save(mission)
            outcome.user_messages.append(message)
            outcome.summary_line = f"- {step.title}: {message}"
            outcome.step_done = True
            return outcome

        if kind == "classify_pdf_importance":
            from pathlib import Path as _Path

            from hermes.mission import step_context as sc
            from hermes.mission.goal_verification import append_goal_audit, record_goal_check
            from hermes.mission.reality_verification import (
                classify_pdf_as_important,
                classify_pdf_by_filename,
                extract_pdf_text,
            )

            strategy = str(
                mission.working_context.get("NEW_STRATEGY")
                or mission.working_context.get("last_replan", {}).get("NEW_STRATEGY")
                or "pdf_regex_extract"
            )
            append_goal_audit(mission, "PLAN", action="classify_pdf_importance", strategy=strategy)

            matches, pattern, search_step_id = sc.resolve_verified_search_matches(mission, step)
            if not matches:
                matches, pattern, search_step_id = sc.resolve_search_matches_fallback(mission)
            search_bucket = (mission.working_context.get("search_results") or {}).get("scan_pdfs")
            if not isinstance(search_bucket, dict) and search_step_id:
                search_bucket = (mission.working_context.get("search_results") or {}).get(search_step_id)
            source_loc = str((search_bucket or {}).get("source_location") or "").strip()
            if source_loc and matches:
                dest_exclude = _Path(source_loc) / "Onemli dosyalar"
                matches = sc.exclude_paths_under_folder(matches, dest_exclude)
            append_goal_audit(
                mission,
                "PDF_STEP",
                step_id=step.step_id,
                tool="classify_pdf_importance",
                strategy=strategy,
                input_count=len(matches),
                search_step_id=search_step_id,
            )
            if not matches:
                last_search = mission.working_context.get("last_search")
                search_bucket = (mission.working_context.get("search_results") or {}).get("scan_pdfs")
                if not isinstance(search_bucket, dict) and search_step_id:
                    search_bucket = (mission.working_context.get("search_results") or {}).get(search_step_id)
                result_count = -1
                if isinstance(search_bucket, dict):
                    result_count = int(
                        search_bucket.get("result_count") or len(search_bucket.get("matched_files") or [])
                    )
                elif isinstance(last_search, dict):
                    result_count = int(
                        last_search.get("result_count") or len(last_search.get("matched_files") or [])
                    )
                if result_count == 0:
                    empty_info = {
                        "pattern": pattern,
                        "folder": str((search_bucket or {}).get("source_location") or ""),
                        "source_location": str((search_bucket or {}).get("source_location") or ""),
                    }
                    message = sc.search_empty_user_message(empty_info)
                    record_goal_check(
                        mission,
                        goal_achieved=True,
                        state_verified=True,
                        message=message,
                        details={"partial": True, "examined": 0, "important": 0},
                    )
                    mission.working_context["pdf_examined_count"] = 0
                    mission.working_context["important_pdf_count"] = 0
                    mission.working_context["important_pdf_matches"] = []
                    mission.working_context["natural_summary"] = message
                    step.execution_status = "succeeded"
                    step.verification_status = VerificationStatus.VERIFIED.value
                    step.verification_method = "pdf_search_empty"
                    step.status = MissionStepStatus.COMPLETED
                    step.completed_at = _utc_now()
                    step.result_summary = message
                    self._store.save(mission)
                    outcome.user_messages.append(message)
                    outcome.summary_line = f"- {step.title}: {message}"
                    outcome.step_done = True
                    return outcome
                folder_raw = str(
                    (search_bucket or {}).get("source_location")
                    or (last_search or {}).get("source_location")
                    or ""
                ).strip()
                if folder_raw:
                    scanned = sc.scan_files_on_filesystem(folder_raw, pattern or "*.pdf")
                    if not scanned.get("error"):
                        rescanned = sc.filter_search_matches_for_pattern(
                            list(scanned.get("matches") or []),
                            pattern or "*.pdf",
                        )
                        if rescanned:
                            matches = rescanned
                            append_goal_audit(
                                mission,
                                "OBSERVE",
                                action="pdf_search_rescan",
                                input_count=len(matches),
                                folder=folder_raw,
                            )
                if not matches:
                    message = "PDF dosyasi bulunamadi."
                    record_goal_check(mission, goal_achieved=False, state_verified=True, message=message)
                    step.execution_status = "succeeded"
                    step.verification_status = VerificationStatus.VERIFIED.value
                    step.verification_method = "pdf_search_empty"
                    step.status = MissionStepStatus.COMPLETED
                    step.completed_at = _utc_now()
                    step.result_summary = message
                    mission.working_context["pdf_examined_count"] = 0
                    mission.working_context["important_pdf_count"] = 0
                    mission.working_context["important_pdf_matches"] = []
                    mission.working_context["natural_summary"] = message
                    self._store.save(mission)
                    outcome.user_messages.append(message)
                    outcome.summary_line = f"- {step.title}: {message}"
                    outcome.step_done = True
                    return outcome

            if strategy == "ask_user_pdf":
                mission.status = MissionStatus.WAITING_FOR_USER
                mission.waiting_for_user_reason = (
                    "PDF icerigini okuyamadim. Hangi dosyalarin onemli oldugunu belirtir misin?"
                )
                step.execution_status = "blocked"
                step.status = MissionStepStatus.BLOCKED
                step.completed_at = _utc_now()
                step.result_summary = mission.waiting_for_user_reason
                self._store.save(mission)
                outcome.waiting_for_user = True
                outcome.user_messages.append(mission.waiting_for_user_reason)
                outcome.summary_line = f"- {step.title}: kullanici yaniti bekleniyor"
                return outcome

            classified: list[dict[str, Any]] = []
            readable = 0
            for item in matches:
                path = str(item.get("path") or "")
                if not path:
                    continue
                name = _Path(path).name
                if strategy == "pdf_filename_heuristic":
                    important = classify_pdf_by_filename(name)
                    classified.append(
                        {
                            "path": path,
                            "name": name,
                            "important": important,
                            "text_preview": "",
                            "readable": False,
                            "classification_method": "filename_heuristic",
                            "error": "",
                        }
                    )
                    append_goal_audit(
                        mission,
                        "OBSERVE",
                        action="pdf_filename_heuristic",
                        path=path,
                        important=important,
                    )
                    continue

                extracted = extract_pdf_text(path)
                append_goal_audit(
                    mission,
                    "OBSERVE",
                    action="pdf_extract",
                    path=path,
                    ok=extracted.ok,
                    strategy=strategy,
                )
                if extracted.ok:
                    readable += 1
                important = extracted.ok and classify_pdf_as_important(extracted.text, name)
                classified.append(
                    {
                        "path": path,
                        "name": name,
                        "important": important,
                        "text_preview": extracted.text[:200],
                        "readable": extracted.ok,
                        "classification_method": "content",
                        "error": extracted.error,
                    }
                )

            mission.working_context["pdf_examined_count"] = len(classified)
            mission.working_context["pdf_readable_count"] = readable
            unreadable = [item for item in classified if not item.get("readable") and item.get("classification_method") == "content"]
            mission.working_context["pdf_unreadable_count"] = len(unreadable)
            mission.working_context["pdf_unreadable_names"] = [str(item.get("name") or "") for item in unreadable][:20]

            if readable == 0 and strategy == "pdf_regex_extract":
                append_goal_audit(mission, "PLAN", action="classify_pdf_importance", strategy="pdf_filename_heuristic")
                for item in classified:
                    path = str(item.get("path") or "")
                    if not path:
                        continue
                    name = _Path(path).name
                    important = classify_pdf_by_filename(name)
                    item["important"] = important
                    item["classification_method"] = "filename_heuristic"
                    item["readable"] = False
                    append_goal_audit(
                        mission,
                        "OBSERVE",
                        action="pdf_filename_heuristic",
                        path=path,
                        important=important,
                    )
                important_matches = [item for item in classified if item.get("important")]
                mission.working_context["classified_pdfs"] = classified
                mission.working_context["important_pdf_matches"] = important_matches
                mission.working_context["important_pdf_count"] = len(important_matches)
                examined = len(classified)
                important_count = len(important_matches)
                if important_count == 0:
                    message = (
                        f"{examined} PDF incelendi; icerik okunamadi, dosya adi ile de onemli bulunamadi."
                    )
                    record_goal_check(
                        mission,
                        goal_achieved=False,
                        state_verified=True,
                        message=message,
                        details={"partial": True, "examined": examined, "readable": 0},
                    )
                    step.execution_status = "succeeded"
                    step.verification_status = VerificationStatus.VERIFIED.value
                    step.verification_method = "pdf_partial_unreadable"
                    step.status = MissionStepStatus.COMPLETED
                    step.completed_at = _utc_now()
                    step.result_summary = message
                    self._store.save(mission)
                    outcome.user_messages.append(message)
                    outcome.summary_line = f"- {step.title}: {message}"
                    outcome.step_done = True
                    return outcome
                summary = f"{examined} PDF incelendi, {important_count} onemli bulundu (dosya adi)"
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "pdf_content_classification"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = summary
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: {step.result_summary}"
                outcome.step_done = True
                return outcome

            if readable == 0 and strategy == "pdf_filename_heuristic":
                message = (
                    f"{len(classified)} PDF incelendi; icerik okunamadi, dosya adi ile de onemli bulunamadi."
                )
                record_goal_check(
                    mission,
                    goal_achieved=False,
                    state_verified=True,
                    message=message,
                    details={"partial": True, "examined": len(classified), "readable": 0},
                )
                mission.working_context["important_pdf_matches"] = []
                mission.working_context["important_pdf_count"] = 0
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "pdf_partial_unreadable"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            important_matches = [item for item in classified if item.get("important")]
            mission.working_context["classified_pdfs"] = classified
            mission.working_context["important_pdf_matches"] = important_matches
            mission.working_context["important_pdf_count"] = len(important_matches)

            examined = len(classified)
            important_count = len(important_matches)
            unreadable_count = len(unreadable)
            summary = f"{examined} PDF incelendi, {important_count} onemli bulundu"
            if unreadable_count:
                summary += f", {unreadable_count} okunamadi"
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "pdf_content_classification"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = summary
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == "copy_classified_pdfs":
            from hermes.mission import step_context as sc
            from hermes.mission.goal_verification import record_goal_check

            destination = str(step.metadata.get("destination") or "")
            important = list(mission.working_context.get("important_pdf_matches") or [])
            report = await sc.run_copy_classified_pdfs(
                important,
                destination,
                mission_id=mission.mission_id,
                step_id=step.step_id,
            )
            mission.working_context.setdefault("mutation_audit", []).extend(report.get("mutations") or [])
            mission.working_context["pdf_move_report"] = report
            moved = int(report.get("moved") or report.get("copied") or 0)
            failed = int(report.get("failed") or 0)
            examined = int(mission.working_context.get("pdf_examined_count") or 0)
            important_count = len(important)

            if report.get("partial") and moved == 0 and important_count == 0:
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "copy_classified_pdfs_skipped"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = "Onemli PDF bulunamadi; tasima yapilmadi"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: {step.result_summary}"
                outcome.step_done = True
                return outcome

            if important_count > 0 and moved == 0:
                message = (
                    f"{examined} PDF incelendi, {important_count} onemli bulundu; "
                    f"dosyalar tasinmadi ({failed} basarisiz)."
                )
                record_goal_check(
                    mission,
                    goal_achieved=False,
                    state_verified=True,
                    message=message,
                    details={
                        "partial": True,
                        "examined": examined,
                        "important": important_count,
                        "moved": 0,
                        "failed": failed,
                    },
                )
                mission.working_context["natural_summary"] = message
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "copy_classified_pdfs_partial"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            if failed > 0 and moved > 0:
                message = (
                    f"{important_count} onemli PDF bulundu; {moved} tanesi ayrildi, "
                    f"{failed} tanesi tasinamadi."
                )
                mission.working_context["pdf_move_partial"] = True
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "copy_classified_pdfs_partial"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            if not report.get("ok") and moved == 0:
                message = (
                    f"{examined} PDF incelendi, {important_count} onemli bulundu; "
                    "dosyalar tasinamadi."
                )
                record_goal_check(
                    mission,
                    goal_achieved=False,
                    state_verified=True,
                    message=message,
                    details={"partial": True, "important": important_count, "moved": 0},
                )
                mission.working_context["natural_summary"] = message
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "copy_classified_pdfs_partial"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "copy_classified_pdfs_filesystem"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = f"{moved} onemli PDF ayirildi"
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == "verify_pdf_separation":
            from pathlib import Path as _Path

            from hermes.mission.goal_verification import record_goal_check
            from hermes.mission.reality_verification import verify_file_exists, verify_move_result

            destination = str(step.metadata.get("destination") or "")
            important_count = int(mission.working_context.get("important_pdf_count") or 0)
            examined = int(mission.working_context.get("pdf_examined_count") or 0)
            readable = int(mission.working_context.get("pdf_readable_count") or 0)
            move_report = mission.working_context.get("pdf_move_report") or {}
            verified_files = list(move_report.get("verified_files") or [])
            moved = int(move_report.get("moved") or move_report.get("copied") or 0)
            failed_moves = int(move_report.get("failed") or 0)
            dest_path = _Path(destination)

            if important_count <= 0:
                if examined > 0 and readable >= 0:
                    message = (
                        f"{examined} PDF incelendi, onemli bulunamadi; hicbir dosya ayrilmadi."
                    )
                    record_goal_check(
                        mission,
                        goal_achieved=True,
                        state_verified=True,
                        message=message,
                        details={"examined": examined, "important": 0, "separated": 0, "no_mutation": True},
                    )
                    mission.working_context["natural_summary"] = message
                    step.execution_status = "succeeded"
                    step.verification_status = VerificationStatus.VERIFIED.value
                    step.verification_method = "goal_partial"
                    step.status = MissionStepStatus.COMPLETED
                    step.completed_at = _utc_now()
                    step.result_summary = message
                    self._store.save(mission)
                    outcome.user_messages.append(message)
                    outcome.summary_line = f"- {step.title}: {message}"
                    outcome.step_done = True
                    return outcome
                from hermes.mission import step_context as sc

                classify_step = next(
                    (item for item in mission.steps if item.step_id == "classify_pdf_importance"),
                    None,
                )
                prior_check = mission.working_context.get("goal_check") or {}
                candidates = [
                    str(mission.working_context.get("natural_summary") or "").strip(),
                    str(prior_check.get("message") or "").strip(),
                    str(classify_step.result_summary or "").strip() if classify_step else "",
                ]
                message = next(
                    (
                        item
                        for item in candidates
                        if item and "dogrulanamadi" not in item.casefold()
                    ),
                    "",
                )
                if not message:
                    search_bucket = (mission.working_context.get("search_results") or {}).get("scan_pdfs")
                    if not isinstance(search_bucket, dict):
                        search_bucket = mission.working_context.get("last_search")
                    empty_info = {
                        "pattern": "*.pdf",
                        "folder": str((search_bucket or {}).get("source_location") or ""),
                        "source_location": str((search_bucket or {}).get("source_location") or ""),
                    }
                    message = sc.search_empty_user_message(empty_info)
                record_goal_check(
                    mission,
                    goal_achieved=True,
                    state_verified=True,
                    message=message,
                    details={"examined": examined, "important": 0, "separated": 0, "no_mutation": True},
                )
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "goal_partial"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                mission.working_context["natural_summary"] = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            if moved <= 0:
                important_matches = list(mission.working_context.get("important_pdf_matches") or [])
                already_in_dest: list[str] = []
                if dest_path.is_dir():
                    for item in important_matches:
                        if not isinstance(item, dict):
                            continue
                        name = _Path(str(item.get("path") or "")).name
                        if name and (dest_path / name).is_file():
                            already_in_dest.append(name)
                if important_count > 0 and len(already_in_dest) >= important_count:
                    names = ", ".join(already_in_dest[:6])
                    message = (
                        f"{examined} PDF incelendi, {important_count} onemli bulundu; "
                        f"dosyalar zaten ayrilmis: {names}."
                    )
                    record_goal_check(
                        mission,
                        goal_achieved=True,
                        state_verified=True,
                        message=message,
                        details={
                            "examined": examined,
                            "important": important_count,
                            "separated": len(already_in_dest),
                            "already_separated": True,
                        },
                    )
                    mission.working_context["natural_summary"] = message
                    step.execution_status = "succeeded"
                    step.verification_status = VerificationStatus.VERIFIED.value
                    step.verification_method = "goal_achievement"
                    step.status = MissionStepStatus.COMPLETED
                    step.completed_at = _utc_now()
                    step.result_summary = message
                    self._store.save(mission)
                    outcome.user_messages.append(message)
                    outcome.summary_line = f"- {step.title}: {message}"
                    outcome.step_done = True
                    return outcome
                message = (
                    f"{examined} PDF incelendi, {important_count} onemli bulundu; "
                    "hicbir dosya ayrilmadi."
                )
                record_goal_check(
                    mission,
                    goal_achieved=False,
                    state_verified=True,
                    message=message,
                    details={
                        "partial": True,
                        "examined": examined,
                        "important": important_count,
                        "separated": 0,
                    },
                )
                mission.working_context["natural_summary"] = message
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "goal_partial"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            verified_names: list[str] = []
            mutations = list(move_report.get("mutations") or [])
            important_matches = list(mission.working_context.get("important_pdf_matches") or [])
            for file_path in verified_files:
                dest_file = _Path(file_path)
                if not verify_file_exists(dest_file).get("ok"):
                    continue
                source_still_exists = False
                for mutation in mutations:
                    if not isinstance(mutation, dict):
                        continue
                    if str(mutation.get("destination") or "") != str(dest_file.resolve()):
                        continue
                    source_raw = str(mutation.get("source") or "")
                    if source_raw and _Path(source_raw).is_file():
                        source_still_exists = True
                    break
                if not source_still_exists:
                    verified_names.append(dest_file.name)

            if not verified_names and dest_path.is_dir():
                for item in important_matches:
                    if not isinstance(item, dict):
                        continue
                    name = _Path(str(item.get("path") or "")).name
                    candidate = dest_path / name
                    if not candidate.is_file():
                        continue
                    source_raw = str(item.get("path") or "")
                    move_check = verify_move_result(_Path(source_raw), candidate)
                    if move_check.get("ok") or verify_file_exists(candidate).get("ok"):
                        verified_names.append(name)

            if failed_moves > 0 and verified_names:
                names = ", ".join(verified_names[:6])
                message = (
                    f"{examined} PDF incelendi, {important_count} onemli bulundu, "
                    f"{len(verified_names)} dosya ayrildi ({failed_moves} tasinamadi): {names}."
                )
                record_goal_check(
                    mission,
                    goal_achieved=False,
                    state_verified=True,
                    message=message,
                    details={
                        "partial": True,
                        "examined": examined,
                        "important": important_count,
                        "separated": len(verified_names),
                        "failed_moves": failed_moves,
                    },
                )
                mission.working_context["natural_summary"] = message
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "goal_partial"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            if len(verified_names) < moved:
                message = (
                    f"{examined} PDF incelendi, {important_count} onemli bulundu; "
                    f"yalnizca {len(verified_names)}/{moved} tasima dogrulandi."
                )
                record_goal_check(
                    mission,
                    goal_achieved=False,
                    state_verified=True,
                    message=message,
                    details={"partial": True, "separated": len(verified_names), "expected": moved},
                )
                mission.working_context["natural_summary"] = message
                step.execution_status = "succeeded"
                step.verification_status = VerificationStatus.VERIFIED.value
                step.verification_method = "goal_partial"
                step.status = MissionStepStatus.COMPLETED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.user_messages.append(message)
                outcome.summary_line = f"- {step.title}: {message}"
                outcome.step_done = True
                return outcome

            names = ", ".join(verified_names[:6])
            message = (
                f"{examined} PDF incelendi, {important_count} onemli bulundu, "
                f"{len(verified_names)} dosya ayrildi: {names}."
            )
            record_goal_check(
                mission,
                goal_achieved=True,
                state_verified=True,
                message=message,
                details={"examined": examined, "important": important_count, "separated": len(verified_names)},
            )
            mission.working_context["natural_summary"] = message
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "goal_achievement"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = message
            self._store.save(mission)
            outcome.user_messages.append(message)
            outcome.summary_line = f"- {step.title}: {message}"
            outcome.step_done = True
            return outcome

        if kind == "verify_page_content":
            from hermes.mission.reality_verification import normalize_visible_page_text

            screen_output = find_dependency_output(mission, step, tool_name="read_screen_text") or {}
            text, error = normalize_visible_page_text(screen_output if isinstance(screen_output, dict) else {})
            if not text:
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = error or "Sayfa metni okunamadi"
                self._store.save(mission)
                outcome.user_messages.append(step.result_summary)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                outcome.mission_failed = True
                return outcome
            mission.working_context["verified_page_text"] = text
            page_url = resolve_mission_browser_url(mission, step)
            mission.working_context["browser_page_content"] = {
                "text": text,
                "content_type": "screen_text",
                "url": page_url,
            }
            if page_url:
                mission.working_context["last_browser_url"] = page_url
                mission.working_context["last_url"] = page_url
            step.metadata["prepared_content"] = text
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "page_content_verified"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = f"Sayfa metni okundu ({len(text)} karakter)"
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        if kind == "summarize_page_content":
            from hermes.mission.goal_verification import record_goal_check

            text = str(mission.working_context.get("verified_page_text") or "").strip()
            if not text:
                message = "Sayfa icerigi dogrulanamadi."
                record_goal_check(mission, goal_achieved=False, state_verified=False, message=message)
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({message})"
                outcome.mission_failed = True
                return outcome
            preview = text if len(text) <= 1200 else text[:1197] + "..."
            message = f"Sayfada gorduklerim:\n{preview}"
            record_goal_check(mission, goal_achieved=True, state_verified=True, message=message)
            mission.working_context["natural_summary"] = message
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "goal_achievement"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = "Sayfa ozeti hazir"
            self._store.save(mission)
            outcome.user_messages.append(message)
            outcome.summary_line = f"- {step.title}: ozetlendi"
            outcome.step_done = True
            return outcome

        if kind == "verify_report_file":
            from pathlib import Path as _Path

            from hermes.mission.goal_verification import record_goal_check
            from hermes.mission.reality_verification import verify_file_exists
            from hermes.mission.step_context import get_step_tool_output

            write_output = get_step_tool_output(mission, "write_report_file") or {}
            report_path = str(
                (write_output.get("path") if isinstance(write_output, dict) else "")
                or mission.working_context.get("last_created_file")
                or step.metadata.get("report_path")
                or ""
            ).strip()
            prepared = str(mission.working_context.get("prepared_contents", {}).get("prepare_report_content") or "")
            if not prepared:
                for prev in mission.steps:
                    if prev.step_id == "prepare_report_content":
                        prepared = str(prev.metadata.get("prepared_content") or "")
            check = verify_file_exists(report_path, min_size=20)
            if not check.get("ok"):
                message = f"Rapor dosyasi dogrulanamadi: {check.get('reason')}"
                record_goal_check(mission, goal_achieved=False, state_verified=False, message=message)
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({message})"
                outcome.mission_failed = True
                return outcome
            try:
                disk_content = _Path(report_path).read_text(encoding="utf-8")
            except OSError:
                disk_content = ""
            if not disk_content.strip():
                message = "Rapor dosyasi bos; icerik dogrulanamadi."
                record_goal_check(mission, goal_achieved=False, state_verified=False, message=message)
                step.execution_status = "failed"
                step.status = MissionStepStatus.FAILED
                step.completed_at = _utc_now()
                step.result_summary = message
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: BASARISIZ ({message})"
                outcome.mission_failed = True
                return outcome
            if prepared and prepared.strip() != disk_content.strip():
                if prepared[:80] not in disk_content and "## Gorunen metin" not in disk_content:
                    message = "Rapor dosyasi var ancak icerik dogrulanamadi."
                    record_goal_check(mission, goal_achieved=False, state_verified=False, message=message)
                    step.execution_status = "failed"
                    step.status = MissionStepStatus.FAILED
                    step.completed_at = _utc_now()
                    step.result_summary = message
                    self._store.save(mission)
                    outcome.summary_line = f"- {step.title}: BASARISIZ ({message})"
                    outcome.mission_failed = True
                    return outcome
            message = f"Rapor kaydedildi: {_Path(report_path).name}"
            record_goal_check(mission, goal_achieved=True, state_verified=True, message=message)
            mission.working_context["last_created_file"] = report_path
            mission.working_context.setdefault("resolved_references", {})["last_created_file"] = report_path
            created = mission.working_context.setdefault("created_files", [])
            if isinstance(created, list) and report_path not in created:
                created.insert(0, report_path)
                mission.working_context["created_files"] = created[:20]
            step.metadata["report_path"] = report_path
            from hermes.mission.report_audit import log_report_chain

            log_report_chain(
                mission,
                "verify",
                VERIFIED_PATH=report_path,
                LAST_CREATED_FILE=report_path,
                FILE_SIZE=check.get("size"),
                FILE_EXISTS=True,
            )
            mission.working_context["natural_summary"] = message
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "report_file_verified"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = message
            self._store.save(mission)
            outcome.user_messages.append(message)
            outcome.summary_line = f"- {step.title}: {message}"
            outcome.step_done = True
            return outcome

        if kind == "produce_information_summary":
            summary_kind = str(step.metadata.get("summary_kind") or "general")
            summary = ""
            if summary_kind == "disk_info":
                disk_output = find_dependency_output(mission, step, tool_name="get_disk_info")
                if isinstance(disk_output, dict):
                    drives = disk_output.get("disks") or disk_output.get("drives") or []
                    if isinstance(drives, list) and drives:
                        parts = []
                        for drive in drives[:6]:
                            if not isinstance(drive, dict):
                                continue
                            label = str(drive.get("drive") or drive.get("name") or "?")
                            free = drive.get("free_gb") or drive.get("free") or drive.get("free_space")
                            total = drive.get("total_gb") or drive.get("total") or drive.get("size")
                            if free is not None and total is not None:
                                parts.append(f"{label}: {free} GB bos / {total} GB toplam")
                            elif free is not None:
                                parts.append(f"{label}: {free} GB bos")
                        summary = "Disk durumu: " + "; ".join(parts) if parts else str(disk_output)
                    else:
                        summary = f"Disk durumu: {disk_output}"
                if not summary:
                    summary = "Disk bilgisi alinamadi."

            if not summary:
                summary = step.expected_result or "Bilgi ozeti hazirlandi."

            mission.working_context["natural_summary"] = summary
            if summary_kind == "disk_info" and summary and "alinamadi" not in summary.casefold():
                from hermes.mission.goal_verification import record_goal_check

                record_goal_check(
                    mission,
                    goal_achieved=True,
                    state_verified=True,
                    message=summary,
                )
            step.metadata["prepared_content"] = summary
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "information_summary"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = summary
            self._store.save(mission)
            outcome.user_messages.append(summary)
            outcome.summary_line = f"- {step.title}: {summary}"
            outcome.step_done = True
            return outcome

        if kind == "prepare_screen_report":
            from datetime import datetime, timezone

            from hermes.mission.reality_verification import build_report_content, normalize_visible_page_text

            report_path = str(step.metadata.get("report_path") or "")
            require_text = bool(step.metadata.get("require_substantive_text", True))
            screen_output = find_dependency_output(mission, step, tool_name="read_screen_text")
            browser_page = mission.working_context.get("browser_page_content") or {}

            url = resolve_mission_browser_url(mission, step)
            page_title = ""
            visible_text = ""
            if isinstance(screen_output, dict):
                if not url:
                    url = str(screen_output.get("url") or "")
                page_title = str(screen_output.get("window_title") or screen_output.get("title") or "")
                visible_text, error = normalize_visible_page_text(screen_output)
                if not visible_text and require_text:
                    step.execution_status = "failed"
                    step.status = MissionStepStatus.FAILED
                    step.completed_at = _utc_now()
                    step.result_summary = error or "Sayfa metni okunamadi; rapor olusturulamadi."
                    self._store.save(mission)
                    outcome.user_messages.append(step.result_summary)
                    outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
                    outcome.mission_failed = True
                    return outcome
            elif isinstance(browser_page, dict) and browser_page.get("text"):
                visible_text = str(browser_page.get("text") or "")
                if not url:
                    url = str(browser_page.get("url") or "")

            from hermes.mission.reality_verification import strip_window_title_noise

            if page_title:
                page_title = strip_window_title_noise(page_title)
            elif url:
                from urllib.parse import urlparse

                host = urlparse(url).netloc.replace("www.", "")
                page_title = host or url

            content = build_report_content(
                url=url,
                page_title=page_title,
                visible_text=visible_text,
                extraction_timestamp=datetime.now(timezone.utc).isoformat(),
            )
            step.metadata["prepared_content"] = content
            mission.working_context.setdefault("prepared_contents", {})[step.step_id] = content
            step.execution_status = "succeeded"
            step.verification_status = VerificationStatus.VERIFIED.value
            step.verification_method = "prepared_screen_report"
            step.status = MissionStepStatus.COMPLETED
            step.completed_at = _utc_now()
            step.result_summary = f"Rapor icerigi hazir ({len(content)} karakter)"
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        step.execution_status = "succeeded"
        step.verification_status = VerificationStatus.NOT_REQUIRED.value
        step.verification_method = "logical_step"
        step.status = MissionStepStatus.COMPLETED
        step.completed_at = _utc_now()
        step.result_summary = step.expected_result or "Mantiksal adim tamamlandi"
        self._store.save(mission)
        outcome.summary_line = f"- {step.title}: {step.result_summary}"
        outcome.step_done = True
        return outcome

    async def _execute_verify_recover(
        self, mission: Mission, step: MissionStep, run_id: str, *, auditor: Any | None = None
    ) -> _StepRunOutcome:
        outcome = _StepRunOutcome()

        async def execute_tool(name: str, arguments: dict, rid: str):
            local = LocalToolRequest(name=name, arguments=arguments)
            return await self._execute_step_tool(
                MissionStep(step_id=step.step_id, title=step.title, tool_name=name, tool_arguments=arguments),
                rid,
            )

        result = await execute_tool(step.tool_name or "", step.tool_arguments, run_id)
        execution_success = bool(getattr(result, "success", False))
        step.execution_status = "succeeded" if execution_success else "failed"
        if auditor is not None and step.tool_name:
            auditor.tool_executed(step.tool_name, success=execution_success, step_id=step.step_id)
        if execution_success:
            record_step_tool_output(mission, step, getattr(result, "output", None))
            if step.tool_name == "open_url":
                output = getattr(result, "output", None)
                url = ""
                if isinstance(output, dict):
                    url = str(output.get("url") or "").strip()
                if not url:
                    url = str(step.tool_arguments.get("url") or "").strip()
                if url:
                    mission.working_context["last_browser_url"] = url
                    mission.working_context["last_url"] = url
                    refs = mission.working_context.setdefault("resolved_references", {})
                    refs["last_url"] = url
                    refs["last_browser_url"] = url
            if step.tool_name == "write_file":
                output = getattr(result, "output", None)
                if isinstance(output, dict):
                    actual_path = str(output.get("path") or "").strip()
                    planned_path = str(step.tool_arguments.get("path") or "")
                    if actual_path:
                        step.tool_arguments = dict(step.tool_arguments or {})
                        step.tool_arguments["path"] = actual_path
                    if step.step_id == "write_report_file":
                        import hashlib

                        from hermes.mission.report_audit import log_report_chain

                        content = str(step.tool_arguments.get("content") or "")
                        log_report_chain(
                            mission,
                            "write_result",
                            PLANNED_PATH=planned_path,
                            ACTUAL_WRITE_PATH=actual_path or planned_path,
                            FILE_EXISTS=bool(output.get("exists")),
                            FILE_SIZE=output.get("size"),
                            CONTENT_HASH=(
                                hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                                if content
                                else None
                            ),
                        )
                    if actual_path:
                        mission.working_context["last_created_file"] = actual_path
                        mission.working_context.setdefault("resolved_references", {})[
                            "last_created_file"
                        ] = actual_path
                        created = mission.working_context.setdefault("created_files", [])
                        if isinstance(created, list) and actual_path not in created:
                            created.insert(0, actual_path)
                            mission.working_context["created_files"] = created[:20]
        self._store.record_tool_result(
            mission.mission_id,
            step.tool_name or "",
            success=execution_success,
            output=getattr(result, "output", None),
            error=getattr(result, "error", None),
            mission=mission,
        )

        if not execution_success:
            error_text = str(getattr(result, "error", "") or "Basarisiz")
            mission.status = MissionStatus.RECOVERING
            mission.recovery_count += 1
            mission.failed_step_id = step.step_id
            mission.last_error = error_text[:500]
            self._store.save(mission)
            if auditor is not None:
                auditor.recovery_started(step.step_id, strategy="execution_failure")
            recovery = await self._recovery.attempt_recovery(
                mission,
                step,
                failure_kind=FailureKind.EXECUTION,
                error_text=error_text,
                last_result=result,
                verification_details=None,
                run_id=run_id,
                execute_tool=execute_tool,
                verify=self._observe_and_verify,
                observe_tool=self._observe_via_tool,
            )
            outcome.user_messages.extend(recovery.user_messages)
            if recovery.recovered:
                mission.status = MissionStatus.RUNNING
                if auditor is not None:
                    auditor.recovery_completed(step.step_id)
                return await self._finalize_recovered_step(mission, step, result, run_id, outcome, auditor=auditor)
            if recovery.waiting_for_user:
                mission.status = MissionStatus.WAITING_FOR_USER
                if auditor is not None:
                    auditor.recovery_failed(step.step_id, reason=recovery.waiting_reason)
                outcome.waiting_for_user = True
                outcome.continue_plan = False
                return outcome
            if recovery.non_fatal_skip:
                mission.status = MissionStatus.RUNNING
                step.status = MissionStepStatus.SKIPPED
                step.completed_at = _utc_now()
                step.result_summary = f"Atlandi: {error_text[:200]}"
                self._store.save(mission)
                outcome.summary_line = f"- {step.title}: ATLANDI ({step.result_summary})"
                return outcome
            mission.status = MissionStatus.FAILED
            if auditor is not None:
                auditor.step_failed(step.step_id, reason=error_text)
                auditor.recovery_failed(step.step_id, reason=error_text)
            step.status = MissionStepStatus.FAILED
            step.result_summary = error_text[:500]
            step.completed_at = _utc_now()
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: BASARISIZ ({step.result_summary})"
            outcome.mission_failed = True
            return outcome

        verify_outcome = await self._apply_verification(step, result, run_id, auditor=auditor)
        v_status = step.verification_status or VerificationStatus.UNKNOWN.value
        if v_status in (VerificationStatus.VERIFIED.value, VerificationStatus.NOT_REQUIRED.value):
            step.status = MissionStepStatus.COMPLETED
            if step.tool_name == "search_files":
                observation = step.observation if isinstance(step.observation, dict) else {}
                obs_data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
                verified_output = obs_data.get("verified_output")
                if not isinstance(verified_output, dict):
                    verified_output = getattr(result, "output", None)
                if isinstance(verified_output, dict):
                    record_search_files_context(mission, step, verified_output)
                step.result_summary = (
                    search_result_summary(verified_output)
                    if isinstance(verified_output, dict)
                    else "Arama dogrulandi"
                )
                if isinstance(verified_output, dict) and int(verified_output.get("count") or 0) == 0:
                    outcome.user_messages.append(search_empty_user_message(verified_output))
            else:
                preview = str(getattr(result, "output", "") or "")[:200]
                step.result_summary = preview or "Dogulandi"
            step.completed_at = _utc_now()
            record_output_completion(mission, step)
            self._store.save(mission)
            if auditor is not None:
                auditor.step_completed(step.step_id, summary=step.result_summary or "")
            outcome.summary_line = f"- {step.title}: {step.result_summary}"
            outcome.step_done = True
            return outcome

        error_text = str(
            step.verification_details.get("reason")
            or step.verification_details.get("error")
            or "Dogrulama basarisiz"
        )
        mission.status = MissionStatus.RECOVERING
        mission.recovery_count += 1
        mission.failed_step_id = step.step_id
        mission.last_error = error_text[:500]
        self._store.save(mission)
        if auditor is not None:
            auditor.recovery_started(step.step_id, strategy="verification_failure")
        recovery = await self._recovery.attempt_recovery(
            mission,
            step,
            failure_kind=FailureKind.VERIFICATION,
            error_text=error_text,
            last_result=result,
            verification_details=step.verification_details,
            run_id=run_id,
            execute_tool=execute_tool,
            verify=self._observe_and_verify,
            observe_tool=self._observe_via_tool,
        )
        outcome.user_messages.extend(recovery.user_messages)
        if recovery.recovered:
            mission.status = MissionStatus.RUNNING
            if auditor is not None:
                auditor.recovery_completed(step.step_id)
            return await self._finalize_recovered_step(mission, step, result, run_id, outcome, auditor=auditor)
        if recovery.waiting_for_user:
            mission.status = MissionStatus.WAITING_FOR_USER
            if auditor is not None:
                auditor.recovery_failed(step.step_id, reason=recovery.waiting_reason)
            outcome.waiting_for_user = True
            outcome.continue_plan = False
            return outcome
        if recovery.non_fatal_skip:
            mission.status = MissionStatus.RUNNING
            step.status = MissionStepStatus.SKIPPED
            step.completed_at = _utc_now()
            step.result_summary = f"Dogrulama atlandi: {error_text[:200]}"
            self._store.save(mission)
            outcome.summary_line = f"- {step.title}: ATLANDI ({step.result_summary})"
            return outcome

        step.status = MissionStepStatus.VERIFICATION_FAILED
        step.result_summary = error_text[:500]
        step.completed_at = _utc_now()
        mission.status = MissionStatus.FAILED
        if auditor is not None:
            auditor.step_failed(step.step_id, reason=error_text)
            auditor.recovery_failed(step.step_id, reason=error_text)
        mission.recovery_attempts.append(
            {
                "type": "verification_failed",
                "step_id": step.step_id,
                "tool_name": step.tool_name,
                "execution_success": True,
                "verification_status": v_status,
                "verification_method": step.verification_method,
                "verification_details": step.verification_details,
                "observation": step.observation,
                "at": _utc_now(),
            }
        )
        self._store.save(mission)
        outcome.summary_line = f"- {step.title}: DOGRULAMA BASARISIZ ({step.result_summary})"
        outcome.mission_failed = True
        return outcome

    async def _finalize_recovered_step(
        self,
        mission: Mission,
        step: MissionStep,
        result: Any,
        run_id: str,
        outcome: _StepRunOutcome,
        *,
        auditor: Any | None = None,
    ) -> _StepRunOutcome:
        del result, run_id
        step.status = MissionStepStatus.COMPLETED
        step.verification_status = VerificationStatus.VERIFIED.value
        step.result_summary = "Recovery basarili"
        step.completed_at = _utc_now()
        mission.status = MissionStatus.RUNNING
        self._store.save(mission)
        if auditor is not None:
            auditor.step_completed(step.step_id, summary=step.result_summary)
        outcome.summary_line = f"- {step.title}: {step.result_summary}"
        outcome.step_done = True
        return outcome

    async def _apply_verification(
        self, step: MissionStep, result: Any, run_id: str, *, auditor: Any | None = None
    ) -> dict[str, Any]:
        if auditor is not None and step.tool_name:
            auditor.verification_started(step.tool_name, method=step.verification_method or "")
        verify_outcome = await self._observe_and_verify(step, result, run_id)
        step.observation = verify_outcome.get("observation", {})
        step.verification_status = verify_outcome.get("verification_status")
        step.verification_method = verify_outcome.get("verification_method")
        step.verification_details = verify_outcome.get("verification_details", {})
        step.verified_at = verify_outcome.get("verified_at")
        if verify_outcome.get("observation_record"):
            step.observations.append(verify_outcome["observation_record"])
        if auditor is not None and step.tool_name:
            v_status = step.verification_status or ""
            if v_status == VerificationStatus.VERIFIED.value:
                auditor.verification_passed(step.tool_name, method=step.verification_method or "")
            elif v_status not in (VerificationStatus.NOT_REQUIRED.value, VerificationStatus.UNKNOWN.value):
                reason = str(step.verification_details.get("reason") or "verification_failed")
                auditor.verification_failed(step.tool_name, reason=reason)
        return verify_outcome

    async def _execute_step_tool(self, step: MissionStep, run_id: str) -> ToolResultPayload | Any:
        local = LocalToolRequest(name=step.tool_name or "", arguments=step.tool_arguments)
        if self._execute_local_tool is not None:
            try:
                return await self._execute_local_tool(local, run_id)
            except ToolApprovalRequiredError:
                raise
            except Exception as exc:
                return ToolResultPayload(success=False, error=str(exc))

        tool_call = ToolCallRequest(name=local.name, arguments=local.arguments)
        return await self._executor.execute_tool_call(tool_call, run_id=run_id)

    async def _observe_via_tool(
        self, tool_name: str, arguments: dict[str, Any], run_id: str
    ) -> ToolResultPayload | None:
        tool = self._registry.get(tool_name)
        if tool is None:
            return None
        definition = tool.get_definition()
        if not is_read_only_observe_tool(tool_name, definition.risk_level):
            return None
        policy = self._executor._policy.evaluate(tool_name, arguments)  # noqa: SLF001
        if policy.decision == PolicyDecision.DENY:
            return None
        call = ToolCallRequest(name=tool_name, arguments=arguments)
        return await self._executor.execute_tool_call(call, run_id=run_id, skip_approval=True)

    async def _observe_and_verify(
        self,
        step: MissionStep,
        result: ToolResultPayload | Any,
        run_id: str,
    ) -> dict[str, Any]:
        ctx = VerifierContext(
            tool_name=step.tool_name or "",
            tool_arguments=step.tool_arguments,
            execution_success=bool(getattr(result, "success", False)),
            execution_output=getattr(result, "output", None),
            execution_error=getattr(result, "error", None),
            step=step,
            run_id=run_id,
            observe_tool=self._observe_via_tool,
            timeout_seconds=self._verification_timeout,
        )
        verification = await self._verifiers.verify(ctx)
        observation_record = (
            {
                "source": verification.observation.source,
                "data": verification.observation.data,
                "observed_at": verification.observation.observed_at,
            }
            if verification.observation
            else None
        )
        observation_payload = observation_record or {}
        return {
            "verification_status": verification.status.value,
            "verification_method": verification.method,
            "verification_details": verification.details,
            "verified_at": verification.verified_at,
            "observation": observation_payload if isinstance(observation_payload, dict) else {},
            "observation_record": observation_record,
        }
