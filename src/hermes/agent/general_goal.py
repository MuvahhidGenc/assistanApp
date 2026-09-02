"""Phase 10 — generic goal model for general autonomous agent tasks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hermes.agent.plan_models import TaskCategory
from hermes.context.conversational_context import ConversationalContext


@dataclass
class GenericGoal:
    """Structured goal beyond keyword heuristics — feeds planning and verification."""

    objective: str = ""
    category: TaskCategory = TaskCategory.UNKNOWN
    entities: dict[str, str] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    desired_outcome: str = ""
    required_capabilities: list[str] = field(default_factory=list)
    verification_criteria: list[str] = field(default_factory=list)
    risk_level: str = "normal"
    confidence: float = 0.0
    is_compound: bool = False
    requires_observe: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "category": self.category.value,
            "entities": dict(self.entities),
            "constraints": dict(self.constraints),
            "desired_outcome": self.desired_outcome,
            "required_capabilities": list(self.required_capabilities),
            "verification_criteria": list(self.verification_criteria),
            "risk_level": self.risk_level,
            "confidence": self.confidence,
            "is_compound": self.is_compound,
            "requires_observe": self.requires_observe,
        }


_ORGANIZE = re.compile(
    r"\b(?:duzenle|düzenle|organize|ayir|ayır|sirala|sırala|grupla|topla|temizle)\b",
    re.IGNORECASE,
)
_PDF_INSPECT = re.compile(
    r"\b(?:pdf|pdfl)\w*\b.*\b(?:incele|bul|ayir|ayır|onemli|önemli|sec|seç)\b|"
    r"\b(?:incele|bul|ayir|ayır).*\bpdf\b",
    re.IGNORECASE,
)
_DISK_INFO = re.compile(
    r"\b(?:bos\s*alan|boş\s*alan|disk|depolama|storage|yer\s+kaldi|yer\s+kaldı)\b",
    re.IGNORECASE,
)
_BROWSER_REPORT = re.compile(
    r"\b(?:chrome|tarayici|tarayıcı|browser|web\s*sayfa|siteye\s*git)\b.*"
    r"\b(?:rapor|kaydet|yaz|masaust|masaüst|dosya)\b|"
    r"\b(?:sayfa(?:daki|daki)?|web).*\b(?:rapor|kaydet|masaust|masaüst)\b",
    re.IGNORECASE,
)
_CHROME_NAV = re.compile(
    r"\b(?:chrome|tarayici|tarayıcı)\b.*\b(?:ac|aç|git|gir|site)\b",
    re.IGNORECASE,
)
_SYSTEM_INFO = re.compile(
    r"\b(?:sistem|bilgisayar|pc)\b.*\b(?:kontrol|bilgi|durum|ne\s+kadar)\b|"
    r"\b(?:ne\s+kadar\s+bos|ne\s+kadar\s+boş)\b",
    re.IGNORECASE,
)


def classify_generic_goal(
    message: str,
    ctx: ConversationalContext | None = None,
    *,
    parsed_goal_dict: dict[str, Any] | None = None,
    agent_state: dict[str, Any] | None = None,
) -> GenericGoal:
    """Derive a general goal model from message + current state."""
    text = (message or "").strip()
    lower = text.casefold()
    goal = GenericGoal(objective=text)
    state = dict(agent_state or {})

    if _ORGANIZE.search(text) and re.search(r"masa[uü]st|desktop|indirilen|download", lower):
        goal.category = TaskCategory.ORGANIZATION
        goal.desired_outcome = "Dosyalar turlerine gore duzenli klasorlerde"
        goal.required_capabilities = ["list_directory", "search_files", "create_folder", "copy_file"]
        goal.verification_criteria = ["filesystem_layout_verified"]
        goal.requires_observe = True
        goal.is_compound = True
        goal.confidence = 0.88

    elif _PDF_INSPECT.search(text):
        goal.category = TaskCategory.FILESYSTEM
        goal.desired_outcome = "Onemli PDF dosyalari ayri klasore alinmis"
        goal.required_capabilities = ["search_files", "create_folder", "copy_file", "list_directory"]
        goal.verification_criteria = ["pdf_files_separated"]
        goal.requires_observe = True
        goal.is_compound = True
        goal.confidence = 0.9
        if "indirilen" in lower or "download" in lower:
            goal.entities["source"] = "downloads"

    elif _DISK_INFO.search(text) or (_SYSTEM_INFO.search(text) and "bos" in lower):
        goal.category = TaskCategory.INFORMATION
        goal.desired_outcome = "Disk bos alan bilgisi raporlandi"
        goal.required_capabilities = ["get_disk_info"]
        goal.verification_criteria = ["disk_info_present"]
        goal.confidence = 0.92

    elif _BROWSER_REPORT.search(text):
        goal.category = TaskCategory.MULTI_STEP
        goal.desired_outcome = "Web icerigi dosyaya kaydedilmis ve dogrulanmis"
        goal.required_capabilities = ["open_app", "open_url", "read_screen_text", "write_file"]
        goal.verification_criteria = ["report_file_exists"]
        goal.is_compound = True
        goal.requires_observe = True
        goal.confidence = 0.85

    elif _CHROME_NAV.search(text):
        goal.category = TaskCategory.APPLICATION
        goal.desired_outcome = "Tarayici acilmis ve hedef adrese gidilmis"
        goal.required_capabilities = ["open_app", "open_url"]
        goal.verification_criteria = ["browser_open"]
        goal.is_compound = "sonra" in lower or lower.count(" ve ") >= 1
        goal.confidence = 0.8

    if parsed_goal_dict:
        if parsed_goal_dict.get("is_multi_step"):
            goal.is_compound = True
        if parsed_goal_dict.get("desired_result"):
            goal.desired_outcome = str(parsed_goal_dict["desired_result"])
        goal.confidence = max(goal.confidence, float(parsed_goal_dict.get("confidence") or 0))
        for key in ("source_location", "destination", "file_type", "file_pattern"):
            value = parsed_goal_dict.get(key)
            if value:
                goal.entities[key] = str(value)

    if state.get("verified_folder"):
        goal.entities["verified_folder"] = str(state["verified_folder"])
    if state.get("verified_file"):
        goal.entities["verified_file"] = str(state["verified_file"])

    return goal


def is_general_mission_goal(message: str) -> bool:
    """True when Phase 10 general planner should handle the goal locally."""
    from hermes.agent.general_task_planner import (
        is_browser_report_goal,
        is_browser_page_check_goal,
        is_disk_info_goal,
        is_general_organize_goal,
        is_pdf_inspect_goal,
    )

    text = (message or "").strip()
    if not text:
        return False
    return any(
        checker(text)
        for checker in (
            is_general_organize_goal,
            is_pdf_inspect_goal,
            is_disk_info_goal,
            is_browser_report_goal,
            is_browser_page_check_goal,
        )
    )
