"""Phase 9 — goal analysis and dynamic heuristic plans."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hermes.agent.plan_models import GoalAnalysis, TaskCategory
from hermes.context.conversational_context import ConversationalContext
from hermes.context.system_paths import resolve_known_folder
from hermes.mission.models import MissionStep, StepAction
from hermes.tools.registry import ToolRegistry

_ORGANIZE = re.compile(
    r"\b(?:duzenle|düzenle|organize|ayir|ayır|sirala|sırala|grupla|tas[iı]|taşı)\b",
    re.IGNORECASE,
)
_CREATE_AUDIT = re.compile(
    r"\b(?:olustur|oluştur|yarat|create)\b.*\b(?:kontrol|dogrula|doğrula|rapor|soyle|söyle|ne\s+yapt|ne\s+olusturd|ne\s+oluşturd)\b",
    re.IGNORECASE,
)
_MULTI_TYPE_FILES = re.compile(
    r"\b(?:uc|üç|3|farkli|farklı)\s+(?:tur|tür|cesit|çeşit|dosya)\b",
    re.IGNORECASE,
)
_CONTEXT_FIX = re.compile(
    r"\b(?:son\s+olusturdugun|son\s+oluşturduğun|son\s+dosya)\b.*"
    r"\b(?:kontrol|adini|adını|duzelt|düzelt|rename)\b",
    re.IGNORECASE,
)
_REPORT_FILES = re.compile(r"\b(?:rapor|report)(?:lar|ları|lari|larni|lari|leri)?\b", re.IGNORECASE)


def build_agent_state_snapshot(ctx: ConversationalContext) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "current_objective": ctx.current_objective,
        "active_mission_id": ctx.active_mission_id,
        "suspended_mission_ids": list(ctx.suspended_mission_ids[:5]),
        "last_successful_mission_id": ctx.last_successful_mission_id,
        "last_failed_mission_id": ctx.last_failed_mission_id,
        "verified_file": ctx.last_verified_file,
        "verified_folder": ctx.last_verified_folder,
        "active_file": ctx.active_file,
        "active_folder": ctx.active_folder,
        "last_created_file": ctx.last_created_file,
        "last_created_folder": ctx.last_created_folder,
        "last_modified_file": ctx.last_modified_file,
        "last_renamed_file": ctx.last_renamed_file,
        "last_opened_file": ctx.last_opened_file,
        "recent_files": list(ctx.recent_files[:6]),
        "recent_folders": list(ctx.recent_folders[:6]),
        "recent_actions": list(ctx.recent_actions[:6]),
    }
    verified_paths: list[str] = []
    for path in (
        ctx.last_verified_file,
        ctx.last_created_file,
        ctx.last_modified_file,
        *ctx.recent_files[:3],
    ):
        if path and path_exists(path):
            verified_paths.append(str(Path(path).resolve()))
    snapshot["verified_paths"] = list(dict.fromkeys(verified_paths))
    return snapshot


def path_exists(path: str | None) -> bool:
    if not path:
        return False
    try:
        return Path(str(path)).exists()
    except OSError:
        return False


def analyze_goal(
    message: str,
    ctx: ConversationalContext,
    *,
    parsed_goal_dict: dict[str, Any] | None = None,
) -> GoalAnalysis:
    text = (message or "").strip()
    lower = text.casefold()
    state = build_agent_state_snapshot(ctx)
    analysis = GoalAnalysis(user_intent=text)

    if _ORGANIZE.search(text):
        analysis.desired_state = "Dosyalar turlerine gore duzenli klasorlerde"
        analysis.requires_filesystem_probe = True
        analysis.is_compound = True
        analysis.confidence = 0.85

    if _CREATE_AUDIT.search(text) or _MULTI_TYPE_FILES.search(text):
        analysis.desired_state = "Olusturulan dosyalar dogrulanmis ve raporlanmis"
        analysis.is_compound = True
        analysis.confidence = max(analysis.confidence, 0.9)

    if _CONTEXT_FIX.search(text) and state.get("verified_file"):
        analysis.desired_state = "Son verified dosya kontrol edilmis ve gerekirse duzeltilmis"
        analysis.confidence = max(analysis.confidence, 0.88)

    if parsed_goal_dict:
        if parsed_goal_dict.get("is_multi_step"):
            analysis.is_compound = True
        if parsed_goal_dict.get("desired_result"):
            analysis.desired_state = str(parsed_goal_dict["desired_result"])
        analysis.confidence = max(analysis.confidence, float(parsed_goal_dict.get("confidence") or 0))

    parts: list[str] = []
    if state.get("verified_folder"):
        parts.append(f"verified_folder={Path(str(state['verified_folder'])).name}")
    if state.get("verified_file"):
        parts.append(f"verified_file={Path(str(state['verified_file'])).name}")
    if state.get("recent_files"):
        parts.append(f"recent_files={len(state['recent_files'])}")
    analysis.current_state_summary = ", ".join(parts) if parts else "no verified context"

    if analysis.requires_filesystem_probe and not state.get("verified_folder"):
        analysis.missing_information.append("target_location_contents")

    from hermes.agent.general_goal import classify_generic_goal

    generic = classify_generic_goal(text, ctx, parsed_goal_dict=parsed_goal_dict, agent_state=state)
    if generic.category != TaskCategory.UNKNOWN:
        analysis.task_category = generic.category
        analysis.required_capabilities = list(generic.required_capabilities)
        analysis.verification_criteria = list(generic.verification_criteria)
        if generic.desired_outcome:
            analysis.desired_state = generic.desired_outcome
        if generic.is_compound:
            analysis.is_compound = True
        if generic.requires_observe:
            analysis.requires_filesystem_probe = True
        analysis.confidence = max(analysis.confidence, generic.confidence)

    return analysis


def is_organize_files_goal(message: str) -> bool:
    text = (message or "").strip()
    if not _ORGANIZE.search(text):
        return False
    return bool(_REPORT_FILES.search(text) or re.search(r"\b(?:dosya|file|pdf|txt)\b", text, re.IGNORECASE))


def is_create_and_audit_goal(message: str) -> bool:
    from hermes.agent.implicit_file_content import requires_implicit_content_generation
    from hermes.mission.write_content import requires_tool_output_dependency

    text = (message or "").strip()
    if requires_tool_output_dependency(text):
        return False
    if requires_implicit_content_generation(text):
        return True
    return bool(_CREATE_AUDIT.search(text) or (_MULTI_TYPE_FILES.search(text) and re.search(r"\bklas", text, re.IGNORECASE)))


def is_context_file_fix_goal(message: str) -> bool:
    return bool(_CONTEXT_FIX.search(message or ""))


def build_organize_files_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    if not is_organize_files_goal(user_goal):
        return []
    if not all(name in registry for name in ("list_directory", "create_folder", "search_files")):
        return []

    from hermes.context.goal_resolution import extract_location_path

    location = extract_location_path(user_goal) or resolve_known_folder(user_goal)
    source = str(location or (Path.home() / "Desktop"))
    lower = user_goal.casefold()
    pdf_folder = str(Path(source) / "PDF")
    txt_folder = str(Path(source) / "Metin")

    pattern = "*.pdf" if "pdf" in lower else "*"
    if "txt" in lower and "pdf" not in lower:
        pattern = "*.txt"

    steps = [
        MissionStep(
            step_id="scan_source_location",
            title="Dosyalari tarıyorum",
            action=StepAction.TOOL,
            tool_name="list_directory",
            tool_arguments={"path": source},
            depends_on=[],
            risk_level="read_only",
            expected_result="Kaynak konumdaki dosyalar listelendi",
            verification={"required": True, "method": "output_present"},
        ),
        MissionStep(
            step_id="search_target_files",
            title="Hedef dosyalari buluyorum",
            action=StepAction.TOOL,
            tool_name="search_files",
            tool_arguments={"path": source, "pattern": pattern},
            depends_on=["scan_source_location"],
            risk_level="read_only",
            expected_result="Eslesen dosyalar bulundu",
            verification={"required": True, "method": "search_files_filesystem"},
        ),
        MissionStep(
            step_id="prepare_pdf_folder",
            title="PDF klasorunu hazirliyorum",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": pdf_folder},
            depends_on=["search_target_files"],
            risk_level="normal_modification",
            expected_result=f"PDF klasoru: {pdf_folder}",
            verification={"required": True, "method": "output_present"},
        ),
        MissionStep(
            step_id="prepare_txt_folder",
            title="Metin klasorunu hazirliyorum",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": txt_folder},
            depends_on=["search_target_files"],
            risk_level="normal_modification",
            expected_result=f"Metin klasoru: {txt_folder}",
            verification={"required": True, "method": "output_present"},
        ),
        MissionStep(
            step_id="organize_matched_files",
            title="Dosyalari uygun klasorlere tasiyorum",
            action=StepAction.LOGICAL,
            depends_on=["prepare_pdf_folder", "prepare_txt_folder", "search_target_files"],
            expected_result="Dosyalar duzenlendi",
            verification={"required": True, "method": "copy_search_matches_filesystem"},
            metadata={
                "logical_kind": "copy_search_matches",
                "destination": pdf_folder,
                "organize_mode": True,
            },
        ),
        MissionStep(
            step_id="verify_organization",
            title="Duzenlemeyi kontrol ediyorum",
            action=StepAction.LOGICAL,
            depends_on=["organize_matched_files"],
            expected_result="Duzenleme dogrulandi",
            verification={"required": True, "method": "manual_review"},
            metadata={"logical_kind": "verify_goal_completion"},
        ),
    ]
    return steps


def build_create_and_audit_plan(
    user_goal: str,
    registry: ToolRegistry,
    *,
    agent_context: dict[str, Any] | None = None,
) -> list[MissionStep]:
    if not is_create_and_audit_goal(user_goal):
        return []
    if not all(name in registry for name in ("create_folder", "write_file", "list_directory")):
        return []

    import re as _re

    from hermes.context.folder_reference import resolve_create_folder_path

    folder_path = resolve_create_folder_path(user_goal)
    if not folder_path:
        folder_match = _re.search(
            r"([\w\d_-]+)\s+klas(?:o|ö)r(?:u|ü)?",
            user_goal,
            _re.IGNORECASE,
        )
        if folder_match:
            folder_path = str(Path.home() / "Desktop" / folder_match.group(1))

    if not folder_path:
        return []

    folder = Path(str(folder_path))
    from hermes.agent.implicit_file_content import generate_implicit_file_specs

    file_specs = generate_implicit_file_specs(user_goal, folder)
    if not file_specs:
        return []

    steps: list[MissionStep] = [
        MissionStep(
            step_id="create_target_folder",
            title="Hedef klasoru olusturuyorum",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": str(folder)},
            depends_on=[],
            risk_level="normal_modification",
            expected_result=f"Klasor hazir: {folder}",
            verification={"required": True, "method": "output_present"},
        ),
    ]

    prev = "create_target_folder"
    for index, spec in enumerate(file_specs, start=1):
        step_id = f"create_file_{index}"
        steps.append(
            MissionStep(
                step_id=step_id,
                title=f"Dosya olusturuyorum: {spec.path.name}",
                action=StepAction.TOOL,
                tool_name="write_file",
                tool_arguments={"path": str(spec.path), "content": spec.content},
                depends_on=[prev],
                risk_level="normal_modification",
                expected_result=f"Dosya yazildi: {spec.path.name}",
                verification={"required": True, "method": "output_present"},
                metadata={"implicit_content": True, "extension": spec.extension},
            )
        )
        prev = step_id

    steps.append(
        MissionStep(
            step_id="audit_created_files",
            title="Olusturulan dosyalari kontrol ediyorum",
            action=StepAction.TOOL,
            tool_name="list_directory",
            tool_arguments={"path": str(folder)},
            depends_on=[prev],
            risk_level="read_only",
            expected_result="Dosya listesi alindi",
            verification={"required": True, "method": "output_present"},
        )
    )
    steps.append(
        MissionStep(
            step_id="summarize_created_files",
            title="Olusturulanlari ozetliyorum",
            action=StepAction.LOGICAL,
            depends_on=["audit_created_files"],
            expected_result="Olusturulan dosyalar raporlandi",
            verification={"required": True, "method": "creation_audit_filesystem"},
            metadata={"logical_kind": "produce_creation_audit", "folder_path": str(folder)},
        )
    )
    return steps


def build_context_file_fix_plan(
    user_goal: str,
    registry: ToolRegistry,
    *,
    agent_context: dict[str, Any] | None = None,
) -> list[MissionStep]:
    if not is_context_file_fix_goal(user_goal):
        return []
    ctx_data = agent_context or {}
    file_path = (
        ctx_data.get("verified_file")
        or ctx_data.get("last_created_file")
        or ctx_data.get("active_file")
    )
    if not file_path or not path_exists(file_path):
        return []
    if not all(name in registry for name in ("write_file", "rename_path")):
        return []

    path = Path(str(file_path))
    needs_rename = bool(re.search(r"\b(?:adini|adını|duzelt|düzelt|rename)\b", user_goal, re.IGNORECASE))
    steps: list[MissionStep] = [
        MissionStep(
            step_id="verify_target_file",
            title="Hedef dosyayi kontrol ediyorum",
            action=StepAction.LOGICAL,
            depends_on=[],
            expected_result=f"Dosya dogrulandi: {path.name}",
            verification={"required": True, "method": "manual_review"},
            metadata={
                "logical_kind": "read_verify_files",
                "file_path": str(path),
                "expected_content": "",
            },
        ),
    ]
    if needs_rename and not path.name.endswith(".txt"):
        dest = str(path.with_suffix(".txt"))
        steps.append(
            MissionStep(
                step_id="rename_if_needed",
                title="Dosya adini duzeltiyorum",
                action=StepAction.TOOL,
                tool_name="rename_path",
                tool_arguments={"source": str(path), "destination": dest},
                depends_on=["verify_target_file"],
                risk_level="normal_modification",
                expected_result=f"Dosya yeniden adlandirildi: {Path(dest).name}",
                verification={"required": True, "method": "output_present"},
            )
        )
    return steps


def build_agent_dynamic_plan(
    user_goal: str,
    registry: ToolRegistry,
    *,
    agent_context: dict[str, Any] | None = None,
) -> list[MissionStep]:
    from hermes.agent.general_task_planner import build_general_task_plan

    general_plan = build_general_task_plan(user_goal, registry, agent_context=agent_context)
    if general_plan:
        return general_plan

    for builder in (
        build_organize_files_plan,
        build_create_and_audit_plan,
        build_context_file_fix_plan,
    ):
        if builder is build_create_and_audit_plan or builder is build_context_file_fix_plan:
            plan = builder(user_goal, registry, agent_context=agent_context)
        else:
            plan = builder(user_goal, registry)
        if plan:
            return plan
    return []
