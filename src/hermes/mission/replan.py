"""Phase 10.1 — adaptive replan with alternate strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hermes.mission.models import Mission, MissionStep, MissionStepStatus

MAX_REPLAN_ATTEMPTS = 2

PDF_INSPECT_PLAN_SOURCE = "pdf_inspect_heuristic"


def is_pdf_inspect_mission(mission: Mission) -> bool:
    return str(mission.plan_source or "") == PDF_INSPECT_PLAN_SOURCE


@dataclass
class ReplanDecision:
    should_replan: bool = False
    reason: str = ""
    remaining_summary: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    new_strategy: str = ""


def _completed_step_ids(mission: Mission) -> list[str]:
    return [
        step.step_id
        for step in mission.steps
        if step.status == MissionStepStatus.COMPLETED
    ]


def _failed_step_ids(mission: Mission) -> list[str]:
    return [
        step.step_id
        for step in mission.steps
        if step.status in (MissionStepStatus.FAILED, MissionStepStatus.BLOCKED)
    ]


def _prior_strategies(mission: Mission) -> set[str]:
    seen: set[str] = set()
    for entry in mission.working_context.get("replan_history") or []:
        if isinstance(entry, dict) and entry.get("strategy"):
            seen.add(str(entry["strategy"]))
    last = mission.working_context.get("last_replan") or {}
    if isinstance(last, dict) and last.get("new_strategy"):
        seen.add(str(last["new_strategy"]))
    return seen


def _choose_alternate_strategy(failed_step: MissionStep, failure_reason: str, tried: set[str]) -> str:
    kind = str(failed_step.metadata.get("logical_kind") or "")
    tool = failed_step.tool_name or ""
    lower = failure_reason.casefold()

    if kind == "classify_pdf_importance" or "pdf" in lower:
        for strategy in ("pdf_regex_extract", "pdf_filename_heuristic", "ask_user_pdf"):
            if strategy not in tried:
                return strategy
    if kind in {"organize_files_by_type", "copy_search_matches"} or tool == "copy_file":
        for strategy in ("organize_by_extension", "copy_only_pdfs", "skip_empty_organize"):
            if strategy not in tried:
                return strategy
    if tool == "read_screen_text" or kind in {"verify_page_content", "prepare_screen_report"}:
        for strategy in ("retry_screen_ocr", "focus_browser_retry", "report_without_page_text"):
            if strategy not in tried:
                return strategy
    return "generic_retry"


def analyze_failure_for_replan(mission: Mission, failed_step: MissionStep) -> ReplanDecision:
    failure_reason = failed_step.result_summary or failed_step.execution_status or "bilinmeyen hata"
    if is_pdf_inspect_mission(mission):
        return ReplanDecision(reason=failure_reason)

    replan_count = int(mission.working_context.get("replan_count") or 0)
    if replan_count >= MAX_REPLAN_ATTEMPTS:
        return ReplanDecision(reason="Replan limiti doldu")

    from hermes.mission.execution_guard import ExecutionGuard

    budget = ExecutionGuard(mission).check_budget()
    if not budget.allowed:
        return ReplanDecision(reason=budget.reason)

    completed = _completed_step_ids(mission)
    if not completed:
        return ReplanDecision(reason="Henuz basarili adim yok")

    failure_lower = failure_reason.casefold()
    failed_tool = failed_step.tool_name or str(failed_step.metadata.get("logical_kind") or "")
    last_failed = str(mission.working_context.get("last_failed_tool") or "")
    pdf_loop_tools = frozenset(
        {"search_files", "create_folder", "classify_pdf_importance", "copy_classified_pdfs"}
    )
    if (
        replan_count >= 1
        and last_failed
        and last_failed == failed_tool
        and failed_tool in pdf_loop_tools
    ):
        return ReplanDecision(reason=f"Ayni adim tekrar basarisiz: {failed_tool}")

    remaining = [step for step in mission.steps if step.status not in (MissionStepStatus.COMPLETED, MissionStepStatus.SKIPPED)]
    tried = _prior_strategies(mission)
    new_strategy = _choose_alternate_strategy(failed_step, failure_reason, tried)
    if new_strategy in tried:
        return ReplanDecision(reason=f"Ayni strateji tekrar denendi: {new_strategy}")

    non_recoverable_markers = (
        "onemli bulunamadi",
        "hicbir dosya ayrilmadi",
        "onemli olarak siniflandirabilecegim dosya bulamadim",
        "duzenleme icin uygun dosyalari belirledim",
        "hicbir dosya tasinmadi",
        "dosya adi ile de onemli bulunamadi",
        "icerik okunamadi, dosya adi ile de",
        "pdf dosyasi bulunamadi",
        "pdf bulunamadi",
        "klasorunde pdf dosyasi bulamadim",
        "incelendi, onemli bulunamadi",
        "incelendi; icerik okunamadi",
        "onemli pdf dosyalari kopyalanamadi",
        "ayni adim tekrar basarisiz",
    )
    if any(marker in failure_lower for marker in non_recoverable_markers):
        return ReplanDecision(reason=failure_reason)

    context: dict[str, Any] = {
        "failed_step_id": failed_step.step_id,
        "failed_tool": failed_step.tool_name,
        "failed_tool_name": failed_tool,
        "failure_reason": failure_reason,
        "completed_steps": completed,
        "remaining_step_ids": [step.step_id for step in remaining],
        "search_results": mission.working_context.get("search_results"),
        "verified_paths": mission.working_context.get("agent_state", {}).get("verified_paths", []),
        "PREVIOUS_FAILURE": failure_reason,
        "NEW_STRATEGY": new_strategy,
        "REPLAN_REASON": failure_reason,
    }

    if failed_step.metadata.get("logical_kind") == "copy_search_matches":
        return ReplanDecision(reason=failure_reason)

    recoverable = any(
        token in failure_reason.casefold()
        for token in (
            "kilitli",
            "locked",
            "bulunamadi",
            "not found",
            "timeout",
            "kismen",
            "partial",
            "okunamadi",
            "tasinmadi",
            "dogrulanamadi",
        )
    )
    if failed_step.tool_name in ("copy_file", "move_file") or failed_step.metadata.get("logical_kind") in {
        "copy_search_matches",
        "organize_files_by_type",
    }:
        recoverable = True
    if failed_step.metadata.get("logical_kind") == "classify_pdf_importance":
        recoverable = (
            new_strategy not in tried
            and new_strategy != "generic_retry"
            and not any(marker in failure_lower for marker in non_recoverable_markers)
        )
    if failed_step.tool_name == "read_screen_text":
        recoverable = new_strategy != "report_without_page_text"

    if not recoverable and failed_step.status == MissionStepStatus.BLOCKED:
        return ReplanDecision(reason="Guvenlik/policy engeli — replan yok")

    if recoverable and new_strategy != "generic_retry":
        return ReplanDecision(
            should_replan=True,
            reason=failure_reason,
            remaining_summary=f"{len(remaining)} adim kaldi",
            context=context,
            new_strategy=new_strategy,
        )

    return ReplanDecision(reason="Kurtarilamaz hata")


def apply_replan(mission: Mission, decision: ReplanDecision) -> None:
    mission.working_context["replan_count"] = int(mission.working_context.get("replan_count") or 0) + 1
    mission.working_context["last_replan"] = decision.context
    mission.working_context["replan_reason"] = decision.reason
    mission.working_context["REPLAN_REASON"] = decision.reason
    mission.working_context["PREVIOUS_FAILURE"] = decision.context.get("PREVIOUS_FAILURE")
    mission.working_context["NEW_STRATEGY"] = decision.new_strategy or decision.context.get("NEW_STRATEGY")
    mission.working_context["last_failed_tool"] = (
        decision.context.get("failed_tool_name")
        or decision.context.get("failed_tool")
        or decision.context.get("logical_kind")
    )
    history = mission.working_context.setdefault("replan_history", [])
    history.append(
        {
            "reason": decision.reason,
            "strategy": mission.working_context["NEW_STRATEGY"],
            "failed_step_id": decision.context.get("failed_step_id"),
        }
    )
    mission.plan_validated = False
    mission.metadata["force_replan"] = True

    for step in mission.steps:
        if step.status not in (MissionStepStatus.COMPLETED, MissionStepStatus.SKIPPED):
            step.status = MissionStepStatus.SKIPPED
            step.result_summary = (step.result_summary or "") + " [replan]"


def build_partial_success_summary(mission: Mission, *, failed_reason: str = "") -> str:
    completed = [step for step in mission.steps if step.status == MissionStepStatus.COMPLETED]
    if not completed:
        return failed_reason or "Gorev tamamlanamadi."

    labels = [step.title for step in completed[:6]]
    summary = f"{len(completed)} adim tamamlandi: {', '.join(labels)}."
    if failed_reason:
        summary += f" Kalan islem: {failed_reason}"
    return summary
