"""Phase 10 — general autonomous task planners (observe → act → verify)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hermes.context.system_paths import resolve_known_folder
from hermes.mission.models import MissionStep, StepAction
from hermes.tools.manifest import extract_url_hint
from hermes.tools.registry import ToolRegistry

_ORGANIZE = re.compile(
    r"\b(?:duzenle|düzenle|organize|ayir|ayır|sirala|sırala|grupla|topla|temizle)\b",
    re.IGNORECASE,
)
_PDF_INSPECT = re.compile(
    r"\b(?:pdf|pdfl)\w*\b.*\b(?:incele|ayir|ayır|onemli|önemli|sec|seç|siniflandir|sınıflandır)\b|"
    r"\b(?:incele|ayir|ayır|onemli|önemli|sec|seç).*\bpdf\b",
    re.IGNORECASE,
)
_DISK_INFO = re.compile(
    r"\b(?:bos\s*alan|boş\s*alan|disk|depolama|storage|yer\s+kaldi|yer\s+kaldı)\b",
    re.IGNORECASE,
)
_BROWSER_REPORT = re.compile(
    r"\b(?:chrome|tarayici|tarayıcı|browser|web\s*sayfa|siteye\s*git)\b.*"
    r"\b(?:rapor|kaydet|yaz|masaust|masaüst|dosya)\b|"
    r"\b(?:sayfa(?:daki|daki)?|web).*\b(?:rapor|kaydet|masaust|masaüst)\b|"
    r"\b(?:bunu|şunu|sunu)\b.*\b(?:rapor|kaydet|masaust|masaüst)\b",
    re.IGNORECASE,
)
_BROWSER_PAGE_CHECK = re.compile(
    r"\b(?:sayfa(?:y[ıi])?|web\s*sayfa(?:s[ıi])?|actigin|açtığın|actigin|acilan|açılan)\b.*"
    r"\b(?:kontrol|ne\s+gord|ne\s+görd|soyle|söyle|oku)\b|"
    r"\b(?:ne\s+gord|ne\s+görd).*\b(?:sayfa|ekran)\b",
    re.IGNORECASE,
)


def _resolve_source_path(user_goal: str) -> str:
    from hermes.context.goal_resolution import extract_location_path

    location = extract_location_path(user_goal) or resolve_known_folder(user_goal)
    return str(location or (Path.home() / "Desktop"))


def is_general_organize_goal(message: str) -> bool:
    text = (message or "").strip()
    if not _ORGANIZE.search(text):
        return False
    lower = text.casefold()
    return bool(
        re.search(r"masa[uü]st|desktop|indirilen|download", lower)
        or re.search(r"\b(?:dosya|file|pdf|txt)\b", lower)
    )


def is_pdf_inspect_goal(message: str) -> bool:
    return bool(_PDF_INSPECT.search(message or ""))


def is_disk_info_goal(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    lower = text.casefold()
    if _DISK_INFO.search(text):
        return True
    return bool(re.search(r"\bne\s+kadar\s+bos\b|\bne\s+kadar\s+boş\b", lower))


def is_browser_report_goal(message: str) -> bool:
    return bool(_BROWSER_REPORT.search(message or ""))


def is_browser_page_check_goal(message: str) -> bool:
    return bool(_BROWSER_PAGE_CHECK.search(message or ""))


def _observe_step(
    step_id: str,
    title: str,
    *,
    observe_type: str,
    path: str = "",
    depends_on: list[str] | None = None,
    snapshot_key: str = "",
) -> MissionStep:
    metadata: dict[str, Any] = {
        "logical_kind": "observe_state",
        "observe_type": observe_type,
        "path": path,
    }
    if snapshot_key:
        metadata["snapshot_key"] = snapshot_key
    return MissionStep(
        step_id=step_id,
        title=title,
        action=StepAction.LOGICAL,
        depends_on=list(depends_on or []),
        expected_result="Mevcut durum gozlemlendi",
        verification={"required": True, "method": "observe_state"},
        metadata=metadata,
    )


def build_general_organize_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    if not is_general_organize_goal(user_goal):
        return []
    if not all(name in registry for name in ("list_directory", "search_files", "create_folder")):
        return []

    source = _resolve_source_path(user_goal)

    return [
        MissionStep(
            step_id="observe_source",
            title="Mevcut dosyalari inceliyorum",
            action=StepAction.TOOL,
            tool_name="list_directory",
            tool_arguments={"path": source},
            depends_on=[],
            risk_level="read_only",
            expected_result="Kaynak konum tarandi",
            verification={"required": True, "method": "output_present"},
        ),
        _observe_step(
            "snapshot_before",
            "Duzenleme oncesi durumu kaydediyorum",
            observe_type="filesystem_snapshot",
            path=source,
            depends_on=["observe_source"],
            snapshot_key="before",
        ),
        MissionStep(
            step_id="search_files_to_organize",
            title="Duzenlenecek dosyalari buluyorum",
            action=StepAction.TOOL,
            tool_name="search_files",
            tool_arguments={"path": source, "pattern": "*"},
            depends_on=["snapshot_before"],
            risk_level="read_only",
            expected_result="Eslesen dosyalar bulundu",
            verification={"required": True, "method": "search_files_filesystem"},
        ),
        MissionStep(
            step_id="prepare_type_folders",
            title="Duzenleme klasorlerini hazirliyorum",
            action=StepAction.LOGICAL,
            depends_on=["search_files_to_organize"],
            expected_result="PDF ve Metin klasorleri hazir",
            verification={"required": True, "method": "organize_prepare_folders"},
            metadata={"logical_kind": "prepare_organize_folders", "source_root": source},
        ),
        MissionStep(
            step_id="organize_by_type",
            title="Dosyalari turlerine gore tasiyorum",
            action=StepAction.LOGICAL,
            depends_on=["prepare_type_folders", "search_files_to_organize"],
            expected_result="Dosyalar duzenlendi",
            verification={"required": True, "method": "organize_files_by_type_filesystem"},
            metadata={"logical_kind": "organize_files_by_type", "source_root": source},
        ),
        MissionStep(
            step_id="snapshot_after",
            title="Duzenleme sonrasi durumu kontrol ediyorum",
            action=StepAction.LOGICAL,
            depends_on=["organize_by_type"],
            expected_result="Son durum kaydedildi",
            verification={"required": True, "method": "filesystem_snapshot"},
            metadata={
                "logical_kind": "observe_state",
                "observe_type": "filesystem_snapshot",
                "path": source,
                "snapshot_key": "after",
            },
        ),
        MissionStep(
            step_id="verify_organize_goal",
            title="Duzenleme sonucunu dogruluyorum",
            action=StepAction.LOGICAL,
            depends_on=["snapshot_after"],
            expected_result="Duzenleme dogrulandi",
            verification={"required": True, "method": "goal_achievement"},
            metadata={"logical_kind": "verify_desktop_organize", "source_root": source},
        ),
    ]


def build_pdf_inspect_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    if not is_pdf_inspect_goal(user_goal):
        return []
    if not all(name in registry for name in ("search_files", "create_folder", "list_directory")):
        return []

    source = _resolve_source_path(user_goal)
    dest = str(Path(source) / "Onemli dosyalar")

    return [
        MissionStep(
            step_id="scan_pdfs",
            title="PDF dosyalarini arıyorum",
            action=StepAction.TOOL,
            tool_name="search_files",
            tool_arguments={"path": source, "pattern": "*.pdf"},
            depends_on=[],
            risk_level="read_only",
            expected_result="PDF dosyalari listelendi",
            verification={"required": True, "method": "search_files_filesystem"},
        ),
        MissionStep(
            step_id="classify_pdf_importance",
            title="PDF iceriklerini okuyup onem derecesini belirliyorum",
            action=StepAction.LOGICAL,
            depends_on=["scan_pdfs"],
            expected_result="Onemli PDF dosyalari belirlendi",
            verification={"required": True, "method": "pdf_content_classification"},
            metadata={"logical_kind": "classify_pdf_importance"},
        ),
        MissionStep(
            step_id="prepare_important_folder",
            title="Onemli dosyalar klasorunu hazirliyorum",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": dest},
            depends_on=["classify_pdf_importance"],
            risk_level="normal_modification",
            expected_result=f"Klasor hazir: {dest}",
            verification={"required": True, "method": "output_present"},
            metadata={"requires_important_pdfs": True},
        ),
        MissionStep(
            step_id="copy_important_pdfs",
            title="Yalnizca onemli PDF dosyalarini ayiriyorum",
            action=StepAction.LOGICAL,
            depends_on=["prepare_important_folder", "classify_pdf_importance"],
            expected_result="Onemli PDF dosyalari kopyalandi",
            verification={"required": True, "method": "copy_classified_pdfs_filesystem"},
            metadata={"logical_kind": "copy_classified_pdfs", "destination": dest},
        ),
        MissionStep(
            step_id="verify_pdf_goal",
            title="Ayirma islemini dogruluyorum",
            action=StepAction.LOGICAL,
            depends_on=["copy_important_pdfs"],
            expected_result="PDF ayirma dogrulandi",
            verification={"required": True, "method": "goal_achievement"},
            metadata={"logical_kind": "verify_pdf_separation", "destination": dest},
        ),
    ]


def build_browser_page_check_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    if not is_browser_page_check_goal(user_goal):
        return []
    if "read_screen_text" not in registry:
        return []
    return [
        MissionStep(
            step_id="read_page_content",
            title="Sayfadaki metni okuyorum",
            action=StepAction.TOOL,
            tool_name="read_screen_text",
            tool_arguments={"focus_browser": True, "app": "chrome"},
            depends_on=[],
            risk_level="read_only",
            expected_result="Sayfa metni alindi",
            verification={"required": True, "method": "screen_text_substantive"},
        ),
        MissionStep(
            step_id="verify_page_content",
            title="Okunan icerigi dogruluyorum",
            action=StepAction.LOGICAL,
            depends_on=["read_page_content"],
            expected_result="Sayfa icerigi dogrulandi",
            verification={"required": True, "method": "page_content_verified"},
            metadata={"logical_kind": "verify_page_content"},
        ),
        MissionStep(
            step_id="summarize_page",
            title="Gorduklerimi ozetliyorum",
            action=StepAction.LOGICAL,
            depends_on=["verify_page_content"],
            expected_result="Sayfa ozeti hazir",
            verification={"required": True, "method": "goal_achievement"},
            metadata={"logical_kind": "summarize_page_content"},
        ),
    ]


def build_disk_info_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    if not is_disk_info_goal(user_goal):
        return []
    if "get_disk_info" not in registry:
        return []

    return [
        MissionStep(
            step_id="collect_disk_info",
            title="Disk bilgisini topluyorum",
            action=StepAction.TOOL,
            tool_name="get_disk_info",
            tool_arguments={},
            depends_on=[],
            risk_level="read_only",
            expected_result="Disk bos alan bilgisi",
            verification={"required": True, "method": "output_present"},
        ),
        MissionStep(
            step_id="summarize_disk_info",
            title="Bos alan bilgisini ozetliyorum",
            action=StepAction.LOGICAL,
            depends_on=["collect_disk_info"],
            expected_result="Disk ozeti hazir",
            verification={"required": True, "method": "manual_review"},
            metadata={"logical_kind": "produce_information_summary", "summary_kind": "disk_info"},
        ),
    ]


def build_browser_report_plan(user_goal: str, registry: ToolRegistry) -> list[MissionStep]:
    if not is_browser_report_goal(user_goal):
        return []
    required = ("write_file",)
    if not all(name in registry for name in required):
        return []

    report_path = str(Path.home() / "Desktop" / "rapor.txt")
    match = re.search(r"([\w\d_-]+\.(?:txt|md))\b", user_goal, re.IGNORECASE)
    if match:
        report_path = str(Path.home() / "Desktop" / match.group(1))

    steps: list[MissionStep] = []
    content_depends: list[str] = []

    url = extract_url_hint(user_goal) or ""
    from hermes.agent.application_catalog import resolve_web_url

    if not url:
        url = resolve_web_url(user_goal) or ""

    if "read_screen_text" in registry:
        read_depends: list[str] = []
        if url and all(name in registry for name in ("open_app", "open_url")):
            steps.extend(
                [
                    MissionStep(
                        step_id="open_browser",
                        title="Chrome'u aciyorum",
                        action=StepAction.TOOL,
                        tool_name="open_app",
                        tool_arguments={"app": "chrome"},
                        depends_on=[],
                        risk_level="normal_modification",
                        expected_result="Chrome acildi",
                        verification={"required": True, "method": "output_present"},
                    ),
                    MissionStep(
                        step_id="navigate_target",
                        title="Hedef sayfaya gidiyorum",
                        action=StepAction.TOOL,
                        tool_name="open_url",
                        tool_arguments={"url": url},
                        depends_on=["open_browser"],
                        risk_level="normal_modification",
                        expected_result="Sayfa acildi",
                        verification={"required": True, "method": "output_present"},
                    ),
                ]
            )
            read_depends = ["navigate_target"]
        steps.append(
            MissionStep(
                step_id="read_page_content",
                title="Sayfadaki bilgiyi okuyorum",
                action=StepAction.TOOL,
                tool_name="read_screen_text",
                tool_arguments={"focus_browser": True, "app": "chrome"},
                depends_on=read_depends,
                risk_level="read_only",
                expected_result="Ekran metni alindi",
                verification={"required": True, "method": "screen_text_substantive"},
            )
        )
        content_depends = ["read_page_content"]
    elif all(name in registry for name in ("open_app", "open_url")):
        url = extract_url_hint(user_goal) or ""
        steps.extend(
            [
                MissionStep(
                    step_id="open_browser",
                    title="Chrome'u aciyorum",
                    action=StepAction.TOOL,
                    tool_name="open_app",
                    tool_arguments={"app": "chrome"},
                    depends_on=[],
                    risk_level="normal_modification",
                    expected_result="Chrome acildi",
                    verification={"required": True, "method": "output_present"},
                ),
                MissionStep(
                    step_id="navigate_target",
                    title="Hedef sayfaya gidiyorum",
                    action=StepAction.TOOL,
                    tool_name="open_url",
                    tool_arguments={"url": url or "https://example.com"},
                    depends_on=["open_browser"],
                    risk_level="normal_modification",
                    expected_result="Sayfa acildi",
                    verification={"required": True, "method": "output_present"},
                ),
            ]
        )
        content_depends = ["navigate_target"]

    steps.append(
        MissionStep(
            step_id="prepare_report_content",
            title="Rapor icerigini hazirliyorum",
            action=StepAction.LOGICAL,
            depends_on=content_depends,
            expected_result="Rapor icerigi hazir",
            verification={"required": True, "method": "prepared_content"},
            metadata={
                "logical_kind": "prepare_screen_report",
                "report_path": report_path,
                "require_substantive_text": True,
            },
        )
    )
    steps.append(
        MissionStep(
            step_id="write_report_file",
            title="Raporu masaustune kaydediyorum",
            action=StepAction.TOOL,
            tool_name="write_file",
            tool_arguments={"path": report_path, "content": ""},
            depends_on=["prepare_report_content"],
            risk_level="normal_modification",
            expected_result=f"Rapor yazildi: {report_path}",
            verification={"required": True, "method": "output_present"},
            metadata={"content_from_step": "prepare_report_content", "unique_if_exists": True},
            argument_bindings=[
                {
                    "argument": "content",
                    "source_step_id": "prepare_report_content",
                    "source_field": "prepared_content",
                }
            ],
        )
    )
    steps.append(
        MissionStep(
            step_id="verify_report_file",
            title="Rapor dosyasini dogruluyorum",
            action=StepAction.LOGICAL,
            depends_on=["write_report_file"],
            expected_result="Rapor dosyasi dogrulandi",
            verification={"required": True, "method": "report_file_verified"},
            metadata={
                "logical_kind": "verify_report_file",
                "report_path": report_path,
            },
        )
    )
    return steps


def build_general_task_plan(
    user_goal: str,
    registry: ToolRegistry,
    *,
    agent_context: dict[str, Any] | None = None,
) -> list[MissionStep]:
    """Phase 10 dispatcher — tries general planners before Phase 9 heuristics."""
    _ = agent_context
    for builder in (
        build_general_organize_plan,
        build_pdf_inspect_plan,
        build_browser_page_check_plan,
        build_disk_info_plan,
        build_browser_report_plan,
    ):
        plan = builder(user_goal, registry)
        if plan:
            return plan
    return []
