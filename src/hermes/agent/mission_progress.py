"""Phase 8 — natural mission progress and partial-failure summaries."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hermes.mission.models import Mission, MissionStatus, MissionStepStatus

_INTERNAL_TOOL = re.compile(
    r"\b(copy_search_matches|search_files|write_file|create_folder|"
    r"rename_path|open_path|open_app|list_directory|tool)\b",
    re.IGNORECASE,
)


def _step_label(step: Any) -> str:
    title = str(getattr(step, "title", "") or "").strip()
    if title and not _INTERNAL_TOOL.search(title):
        return title
    tool = str(getattr(step, "tool_name", "") or "")
    mapping = {
        "create_folder": "Klasor olusturma",
        "write_file": "Dosya yazma",
        "copy_file": "Dosya kopyalama",
        "search_files": "Dosya arama",
        "rename_path": "Yeniden adlandirma",
        "open_path": "Acma",
    }
    return mapping.get(tool, title or "Adim")


def format_completed_steps(mission: Mission) -> list[str]:
    lines: list[str] = []
    for step in mission.steps:
        if step.status != MissionStepStatus.COMPLETED:
            continue
        summary = (step.result_summary or step.title or "").strip()
        if summary and not _INTERNAL_TOOL.search(summary):
            lines.append(summary)
        elif step.title:
            lines.append(f"{_step_label(step)} tamamlandi")
    return lines


def format_pending_steps(mission: Mission) -> list[str]:
    pending: list[str] = []
    for step in mission.steps:
        if step.status in (
            MissionStepStatus.PENDING,
            MissionStepStatus.RUNNING,
            MissionStepStatus.BLOCKED,
        ):
            pending.append(_step_label(step))
    return pending


def format_failed_step(mission: Mission) -> str:
    failed = mission.failed_step
    if failed is None:
        return ""
    label = _step_label(failed)
    detail = (failed.result_summary or mission.last_error or "").strip()
    if detail and not _INTERNAL_TOOL.search(detail):
        return f"{label}: {detail[:200]}"
    return f"{label} basarisiz oldu"


def format_mission_progress(mission: Mission) -> str:
    """Answer 'Neredeyiz?' / 'Görev ne durumda?'"""
    goal = (mission.user_goal or "").strip()
    preview = goal[:100] + ("..." if len(goal) > 100 else "")
    total = mission.total_steps
    done = mission.completed_steps
    status = mission.status.value

    if mission.status == MissionStatus.COMPLETED:
        natural = str(mission.working_context.get("natural_summary") or mission.summary or "").strip()
        if natural and not _INTERNAL_TOOL.search(natural):
            return natural
        return f"Gorev tamamlandi: {preview}"

    if mission.status == MissionStatus.FAILED:
        partial = format_partial_failure_summary(mission)
        if partial:
            return partial
        return f"Gorev basarisiz: {preview}. {format_failed_step(mission)}"

    if mission.status == MissionStatus.WAITING_FOR_USER:
        reason = (mission.waiting_for_user_reason or "Yanitini bekliyorum").strip()
        return f"Gorev bekliyor ({done}/{total} adim): {reason}"

    pending = format_pending_steps(mission)
    if pending:
        next_step = pending[0]
        return f"Gorev devam ediyor ({done}/{total} adim). Siradaki: {next_step}. Hedef: {preview}"
    return f"Gorev durumu: {status} ({done}/{total} adim). Hedef: {preview}"


def format_mission_remaining(mission: Mission) -> str:
    """Answer 'Ne kaldı?' / 'Neyi tamamladın?'"""
    done_lines = format_completed_steps(mission)
    pending = format_pending_steps(mission)
    parts: list[str] = []

    if done_lines:
        parts.append("Tamamlanan: " + "; ".join(done_lines[:4]))
    elif mission.completed_steps:
        parts.append(f"{mission.completed_steps} adim tamamlandi")

    if pending:
        parts.append("Kalan: " + ", ".join(pending[:4]))
    elif mission.status == MissionStatus.COMPLETED:
        parts.append("Gorev tamamlandi")
    elif mission.status == MissionStatus.FAILED:
        failed = format_failed_step(mission)
        if failed:
            parts.append(f"Hata: {failed}")

    if not parts:
        goal = (mission.user_goal or "")[:80]
        return f"Gorev: {goal}" if goal else "Aktif gorev adimi bulunamadi"
    return ". ".join(parts) + "."


def format_partial_failure_summary(mission: Mission, step_summaries: list[str] | None = None) -> str:
    """Natural partial success message for failed missions."""
    done = mission.completed_steps
    total = mission.total_steps
    failed = format_failed_step(mission)

    copy_stats = _extract_copy_stats(mission, step_summaries or [])
    if copy_stats:
        copied, errors = copy_stats
        if errors:
            return (
                f"{copied} dosyayi kopyaladim, {errors} dosyada hata olustu."
                + (f" {failed}" if failed else "")
            )
        return f"{copied} dosyayi kopyaladim ve kontrol ettim."

    if done > 0 and total > done:
        completed = format_completed_steps(mission)
        hint = completed[0] if completed else f"{done} adim tamamlandi"
        return f"{hint}. Ancak gorev tamamlanamadi" + (f": {failed}" if failed else ".")

    natural = str(mission.working_context.get("natural_summary") or "").strip()
    if natural and not _INTERNAL_TOOL.search(natural):
        return natural
    return failed or f"Gorev {done}/{total} adimda durdu."


def build_natural_mission_summary(mission: Mission) -> str:
    """User-facing summary without internal tool names."""
    existing = str(mission.working_context.get("natural_summary") or mission.summary or "").strip()
    if existing and not _INTERNAL_TOOL.search(existing):
        return existing

    if mission.status == MissionStatus.COMPLETED:
        done = format_completed_steps(mission)
        if done:
            return ". ".join(done[:5]) + "."
        goal = (mission.user_goal or "")[:120]
        return f"Gorevi tamamladim: {goal}" if goal else "Gorev tamamlandi."

    if mission.status == MissionStatus.FAILED:
        return format_partial_failure_summary(mission)

    return format_mission_progress(mission)


def _extract_copy_stats(
    mission: Mission, step_summaries: list[str]
) -> tuple[int, int] | None:
    ctx = mission.working_context.get("copy_results") or {}
    if isinstance(ctx, dict):
        copied = ctx.get("copied_count") or ctx.get("success_count")
        failed = ctx.get("failed_count") or ctx.get("error_count")
        if copied is not None:
            return int(copied), int(failed or 0)

    copied = 0
    errors = 0
    for step in mission.steps:
        if step.tool_name not in ("copy_file", "copy_search_matches"):
            continue
        output = step.metadata.get("tool_output") or {}
        if isinstance(output, dict):
            copied += int(output.get("copied") or output.get("copied_count") or 0)
            errors += int(output.get("failed") or output.get("error_count") or 0)
    if copied or errors:
        return copied, errors

    for line in step_summaries:
        match = re.search(r"(\d+)\s+dosya.*(\d+)\s+hata", line, re.IGNORECASE)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def format_last_operation(ctx: Any) -> str:
    """Answer 'Son işlem neydi?' from conversational context."""
    actions = getattr(ctx, "recent_verified_actions", None) or []
    if actions:
        return str(actions[0])
    summary = str(getattr(ctx, "last_action_summary", "") or "").strip()
    if summary and not _INTERNAL_TOOL.search(summary):
        return summary
    mission_summary = str(getattr(ctx, "last_mission_summary", "") or "").strip()
    if mission_summary and not _INTERNAL_TOOL.search(mission_summary):
        return mission_summary
    path = getattr(ctx, "active_file", None) or getattr(ctx, "last_opened_file", None)
    if path:
        return f"Son islem: {Path(str(path)).name} uzerinde calistim"
    return "Henuz kaydedilmis bir islem yok"
