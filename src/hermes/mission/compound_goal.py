"""Compound desktop file mission detection, parsing, and deterministic planning."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hermes.context.folder_reference import extract_named_folder_name
from hermes.mission.models import MissionStep, StepAction

LOGICAL_KIND_READ_VERIFY_FILES = "read_verify_files"
LOGICAL_KIND_PRODUCE_FILE_SUMMARY = "produce_file_content_summary"

_TXT_FILE = re.compile(r"\b([\w\d_.\-şğüöçıİŞĞÜÖÇ]+\.txt)\b", re.IGNORECASE)
_ORDINAL_WRITES = re.compile(
    r"birincisine\s+(.+?)\s*,?\s*ikincisine\s+(.+?)\s+yaz",
    re.IGNORECASE | re.DOTALL,
)
_RENAME_SOURCE = re.compile(
    r"([\w\d_.\-şğüöçıİŞĞÜÖÇ]+\.(?:txt|md|docx))\s+dosyas(?:ının|inin|inin|yı|yi|yi)\s+ad(?:ını|ini|ini)",
    re.IGNORECASE,
)
_OPEN_FILE = re.compile(
    r"([\w\d_.\-şğüöçıİŞĞÜÖÇ]+\.(?:txt|md|docx))\s+dosyas(?:ını|ini|ini|yı|yi|yi)\s+(?:aç|ac|open)",
    re.IGNORECASE,
)


@dataclass
class CompoundFileSpec:
    name: str
    content: str | None = None
    path: str = ""

    def to_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "content": self.content, "path": self.path}


@dataclass
class ParsedCompoundAction:
    action_type: str
    target: str = ""
    source: str = ""
    destination: str = ""
    content: str = ""
    depends_on: list[str] = field(default_factory=list)
    expected_output: str = ""


@dataclass
class CompoundFileGoal:
    raw_message: str
    folder_name: str
    folder_path: str
    files: list[CompoundFileSpec] = field(default_factory=list)
    search_pattern: str = "*.txt"
    rename_source: str | None = None
    rename_dest: str | None = None
    open_target: str | None = None
    ordered_steps: list[ParsedCompoundAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_name": self.folder_name,
            "folder_path": self.folder_path,
            "files": [item.to_dict() for item in self.files],
            "search_pattern": self.search_pattern,
            "rename_source": self.rename_source,
            "rename_dest": self.rename_dest,
            "open_target": self.open_target,
            "ordered_steps": [
                {
                    "action_type": step.action_type,
                    "target": step.target,
                    "source": step.source,
                    "destination": step.destination,
                    "content": step.content,
                    "depends_on": step.depends_on,
                    "expected_output": step.expected_output,
                }
                for step in self.ordered_steps
            ],
        }


def is_compound_chained_file_mission(text: str) -> bool:
    """
    True when a single user message chains multiple dependent file operations
    (create folder, multiple files, write, search, read, rename, open).
    """
    lower = (text or "").casefold()
    if not lower.strip():
        return False

    from hermes.context.parent_directory import is_parent_directory_open_message

    if is_parent_directory_open_message(text):
        return False

    segment = text
    split_match = re.search(r"\b(ardından|ardindan|adını|adini)\b", lower)
    if split_match and split_match.start() > 0:
        segment = text[: split_match.start()]

    file_names = _TXT_FILE.findall(segment)
    unique_files = {name.casefold() for name in file_names}

    has_folder_create = bool(
        re.search(
            r"(?:klas(?:o|ö)r(?:u|ü)?\s+(?:olustur|oluştur|yarat|create)|"
            r"(?:olustur|oluştur|yarat|create)[\w\s,]{0,24}klas(?:o|ö)r)",
            lower,
        )
    )
    has_write = bool(re.search(r"\.txt|\btxt\b|\byaz\b", lower))
    has_rename = bool(
        re.search(
            r"([\w\d_.\-]+(?:\.txt|\.md|\.docx)\s+dosyas(?:ının|inin|inin|yı|yi|yi)\s+ad(?:ını|ini|ini)|"
            r"dosya(?:sının|sinin|sinin|sini|sini)\s+ad(?:ını|ini|ini)\s+(?:\S+\s+){0,2}(?:yap|koy|ver|olsun))",
            text,
            re.IGNORECASE,
        )
    )
    has_open = bool(
        re.search(r"\b(aç|ac|open)\b", lower)
        and (bool(unique_files) or re.search(r"dosya", lower))
    )
    has_search = bool(re.search(r"\b(bul|ara|search|find)\b", lower))
    has_read_check = bool(re.search(r"icerik|içerik|kontrol|hangi", lower))
    has_ordinal_writes = bool(re.search(r"birincisine|ikincisine", lower))
    has_continuation = bool(re.search(r"\b(sonra|ardından|ardindan)\b", lower))

    if has_folder_create and has_write and (
        len(unique_files) >= 2
        or has_rename
        or has_open
        or has_search
        or has_read_check
        or has_ordinal_writes
    ):
        return True
    if len(unique_files) >= 2 and has_write and (has_rename or has_search or has_read_check):
        return True
    if has_rename and has_open and (has_folder_create or has_write or has_continuation):
        return True
    if has_folder_create and has_continuation and has_write and (has_rename or has_search):
        return True
    return False


def _extract_rename_dest(text: str) -> str | None:
    patterns = (
        r"(?:adini|adını)\s+([\w\d_.\-şğüöçıİŞĞÜÖÇ]+?\.(?:txt|md|docx))\s+(?:yap|koy|ver|olsun)",
        r"(?:adini|adını)\s+([\w\d_.\-şğüöçıİŞĞÜÖÇ]+?)\s+(?:yap|koy|ver|olsun)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .'\"")
            if candidate and "." not in candidate:
                candidate = f"{candidate}.txt"
            return candidate
    return None


def parse_compound_file_goal(text: str) -> CompoundFileGoal | None:
    """Parse chained desktop file mission from natural language."""
    if not is_compound_chained_file_mission(text):
        return None

    message = (text or "").strip()
    lower = message.casefold()
    desktop = Path.home() / "Desktop"

    folder_name = extract_named_folder_name(message)
    if not folder_name:
        folder_match = re.search(
            r"(?:masa[uü]st(?:u|ü)(?:nde|de|ne)?\s+)?([\w\d_.-]+)\s+klas[öo]r",
            message,
            re.IGNORECASE,
        )
        folder_name = folder_match.group(1).strip() if folder_match else "HermesTest"
    folder_path = str(desktop / folder_name)

    segment = message
    split_match = re.search(r"\b(ardından|ardindan|adını|adini)\b", lower)
    if split_match and split_match.start() > 0:
        segment = message[: split_match.start()]

    file_names = _TXT_FILE.findall(segment)
    seen: set[str] = set()
    ordered_names: list[str] = []
    for name in file_names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            ordered_names.append(name)

    rename_dest = _extract_rename_dest(message)
    if rename_dest:
        dest_key = Path(rename_dest).name.casefold()
        ordered_names = [
            name for name in ordered_names if Path(name).name.casefold() != dest_key
        ]

    contents: list[str | None] = [None] * len(ordered_names)
    ordinal = _ORDINAL_WRITES.search(message)
    if ordinal and len(ordered_names) >= 2:
        contents[0] = ordinal.group(1).strip(" .;,")
        contents[1] = ordinal.group(2).strip(" .;,")

    files: list[CompoundFileSpec] = []
    for index, name in enumerate(ordered_names):
        path = str(Path(folder_path) / Path(name).name)
        files.append(CompoundFileSpec(name=Path(name).name, content=contents[index], path=path))

    rename_source: str | None = None
    rename_match = _RENAME_SOURCE.search(message)
    if rename_match:
        rename_source = Path(rename_match.group(1)).name
    elif ordered_names:
        for name in ordered_names:
            if "test1" in name.casefold():
                rename_source = Path(name).name
                break

    open_target: str | None = None
    open_match = _OPEN_FILE.search(message)
    if open_match:
        open_target = Path(open_match.group(1)).name
    elif rename_dest:
        open_target = Path(rename_dest).name

    search_pattern = "*.txt" if "txt" in lower else "*"

    goal = CompoundFileGoal(
        raw_message=message,
        folder_name=folder_name,
        folder_path=folder_path,
        files=files,
        search_pattern=search_pattern,
        rename_source=rename_source,
        rename_dest=rename_dest,
        open_target=open_target,
    )
    goal.ordered_steps = _build_ordered_actions(goal)
    return goal


def _build_ordered_actions(goal: CompoundFileGoal) -> list[ParsedCompoundAction]:
    steps: list[ParsedCompoundAction] = []
    prev = ""

    steps.append(
        ParsedCompoundAction(
            action_type="create_folder",
            target=goal.folder_path,
            expected_output=f"Klasor: {goal.folder_path}",
        )
    )
    prev = "create_folder"

    write_ids: list[str] = []
    for index, spec in enumerate(goal.files):
        step_id = f"write_{Path(spec.name).stem}"
        write_ids.append(step_id)
        steps.append(
            ParsedCompoundAction(
                action_type="write_file",
                target=spec.path,
                content=spec.content or "",
                depends_on=[prev] if index == 0 else [write_ids[index - 1]],
                expected_output=f"Dosya yazildi: {spec.name}",
            )
        )
        prev = step_id

    steps.append(
        ParsedCompoundAction(
            action_type="search_files",
            target=goal.folder_path,
            destination=goal.search_pattern,
            depends_on=[prev],
            expected_output="Txt dosyalari bulundu",
        )
    )
    prev = "search_files"

    for spec in goal.files:
        step_id = f"read_{Path(spec.name).stem}"
        steps.append(
            ParsedCompoundAction(
                action_type="read_file",
                target=spec.path,
                content=spec.content or "",
                depends_on=[prev],
                expected_output=f"Icerik dogrulandi: {spec.name}",
            )
        )
        prev = step_id

    steps.append(
        ParsedCompoundAction(
            action_type="produce_summary",
            depends_on=[prev],
            expected_output="Dosya icerik ozeti",
        )
    )
    prev = "produce_summary"

    if goal.rename_source and goal.rename_dest:
        source_path = str(Path(goal.folder_path) / goal.rename_source)
        steps.append(
            ParsedCompoundAction(
                action_type="rename_path",
                source=source_path,
                destination=goal.rename_dest,
                depends_on=[prev],
                expected_output=f"{goal.rename_source} -> {goal.rename_dest}",
            )
        )
        prev = "rename_file"

    if goal.open_target:
        open_path = str(Path(goal.folder_path) / goal.open_target)
        steps.append(
            ParsedCompoundAction(
                action_type="open_path",
                target=open_path,
                depends_on=[prev],
                expected_output=f"Dosya acildi: {goal.open_target}",
            )
        )

    return steps


def build_compound_desktop_file_plan(user_goal: str, registry: Any) -> list[MissionStep]:
    """Deterministic mission plan for compound chained desktop file workflows."""
    parsed = parse_compound_file_goal(user_goal)
    if parsed is None:
        return []

    required = ("create_folder", "write_file", "search_files", "rename_path", "open_path")
    if not all(name in registry for name in required):
        return []

    steps: list[MissionStep] = []
    prev_id: str | None = None

    folder_id = "create_target_folder"
    steps.append(
        MissionStep(
            step_id=folder_id,
            title=f"Klasor olustur: {parsed.folder_name}",
            action=StepAction.TOOL,
            tool_name="create_folder",
            tool_arguments={"path": parsed.folder_path},
            depends_on=[],
            risk_level="normal_modification",
            expected_result=f"Klasor hazir: {parsed.folder_path}",
            verification={"required": True, "method": "output_present"},
        )
    )
    prev_id = folder_id

    write_step_ids: list[str] = []
    for spec in parsed.files:
        step_id = f"write_{Path(spec.name).stem}"
        write_step_ids.append(step_id)
        steps.append(
            MissionStep(
                step_id=step_id,
                title=f"Dosya yaz: {spec.name}",
                action=StepAction.TOOL,
                tool_name="write_file",
                tool_arguments={"path": spec.path, "content": spec.content or ""},
                depends_on=[prev_id],
                risk_level="normal_modification",
                expected_result=f"Icerik yazildi: {spec.name}",
                verification={"required": True, "method": "output_present"},
                metadata={"compound_file": spec.name, "expected_content": spec.content or ""},
            )
        )
        prev_id = step_id

    search_id = "search_txt_files"
    steps.append(
        MissionStep(
            step_id=search_id,
            title="Txt dosyalarini bul",
            action=StepAction.TOOL,
            tool_name="search_files",
            tool_arguments={"path": parsed.folder_path, "pattern": parsed.search_pattern},
            depends_on=[prev_id],
            risk_level="read_only",
            expected_result="Txt dosyalari listelendi",
            verification={"required": True, "method": "search_files_filesystem"},
        )
    )
    prev_id = search_id

    read_ids: list[str] = []
    for spec in parsed.files:
        step_id = f"verify_read_{Path(spec.name).stem}"
        read_ids.append(step_id)
        steps.append(
            MissionStep(
                step_id=step_id,
                title=f"Icerik dogrula: {spec.name}",
                action=StepAction.LOGICAL,
                depends_on=[prev_id],
                expected_result=f"Icerik okundu: {spec.name}",
                verification={"required": True, "method": "compound_read_verify"},
                metadata={
                    "logical_kind": LOGICAL_KIND_READ_VERIFY_FILES,
                    "file_path": spec.path,
                    "expected_content": spec.content or "",
                    "file_name": spec.name,
                },
            )
        )
        prev_id = step_id

    summary_id = "produce_content_summary"
    steps.append(
        MissionStep(
            step_id=summary_id,
            title="Dosya iceriklerini ozetle",
            action=StepAction.LOGICAL,
            depends_on=[prev_id],
            expected_result="Icerik ozeti hazir",
            verification={"required": True, "method": "compound_file_summary"},
            metadata={
                "logical_kind": LOGICAL_KIND_PRODUCE_FILE_SUMMARY,
                "folder_path": parsed.folder_path,
                "read_step_ids": read_ids,
            },
        )
    )
    prev_id = summary_id

    rename_id = "rename_target_file"
    if parsed.rename_source and parsed.rename_dest:
        source_path = str(Path(parsed.folder_path) / parsed.rename_source)
        steps.append(
            MissionStep(
                step_id=rename_id,
                title=f"Dosya adini degistir: {parsed.rename_source} -> {parsed.rename_dest}",
                action=StepAction.TOOL,
                tool_name="rename_path",
                tool_arguments={"path": source_path, "new_name": parsed.rename_dest},
                depends_on=[prev_id],
                risk_level="normal_modification",
                expected_result=f"Yeni ad: {parsed.rename_dest}",
                verification={"required": True, "method": "output_present"},
                metadata={"rename_source": source_path, "rename_dest_name": parsed.rename_dest},
            )
        )
        prev_id = rename_id

    if parsed.open_target:
        open_path = str(Path(parsed.folder_path) / parsed.open_target)
        steps.append(
            MissionStep(
                step_id="open_result_file",
                title=f"Dosyayi ac: {parsed.open_target}",
                action=StepAction.TOOL,
                tool_name="open_path",
                tool_arguments={"path": open_path},
                depends_on=[prev_id],
                risk_level="low_risk",
                expected_result=f"Dosya acildi: {parsed.open_target}",
                verification={"required": True, "method": "output_present"},
                metadata={"open_target_name": parsed.open_target},
            )
        )

    return steps


def run_read_verify_files(file_path: str, expected_content: str) -> dict[str, Any]:
    """Read file from disk and verify expected content."""
    path = Path(file_path)
    if not path.is_file():
        return {"ok": False, "error": f"Dosya yok: {file_path}", "path": file_path}
    try:
        actual = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": file_path}
    expected = (expected_content or "").strip()
    actual_stripped = actual.strip()
    if expected and actual_stripped != expected:
        return {
            "ok": False,
            "error": "Icerik uyusmuyor",
            "path": str(path),
            "expected": expected,
            "actual": actual_stripped,
        }
    return {
        "ok": True,
        "path": str(path),
        "name": path.name,
        "content": actual_stripped,
        "verified": True,
    }


def build_file_content_summary(mission_context: dict[str, Any]) -> str:
    """Natural-language summary of verified file contents."""
    reads = mission_context.get("read_verifications") or []
    if not isinstance(reads, list) or not reads:
        return ""
    parts: list[str] = []
    for item in reads:
        if not isinstance(item, dict) or not item.get("ok"):
            continue
        name = str(item.get("name") or Path(str(item.get("path") or "")).name)
        content = str(item.get("content") or "").strip()
        if name and content:
            parts.append(f"{name} dosyasinda '{content}' yaziyor")
    return ". ".join(parts) + "." if parts else ""


def build_compound_mission_natural_summary(parsed: CompoundFileGoal, mission_context: dict[str, Any]) -> str:
    """User-facing mission completion summary without internal tool names."""
    folder_label = parsed.folder_name
    file_count = len(parsed.files)
    content_summary = build_file_content_summary(mission_context)

    parts = [f"{folder_label} klasorunu olusturdum"]
    if file_count:
        parts.append(f"{file_count} dosya hazirladim")
    if content_summary:
        parts.append(content_summary.rstrip("."))
    if parsed.rename_source and parsed.rename_dest:
        parts.append(
            f"{parsed.rename_source} dosyasini {parsed.rename_dest} olarak yeniden adlandirdim"
        )
    if parsed.open_target:
        parts.append(f"{parsed.open_target} dosyasini actim")
    return ", ".join(parts) + "."
