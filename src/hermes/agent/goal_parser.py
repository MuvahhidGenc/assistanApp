"""General goal understanding — extract structured intent from natural language."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from hermes.context.conversational_context import ConversationalContext
from hermes.context.goal_resolution import extract_location_path, extract_named_folder_name
from hermes.context.system_paths import (
    detect_known_folder_alias,
    extract_file_type_pattern,
    resolve_known_folder,
)


class ActionKind(StrEnum):
    CREATE = "create"
    OPEN = "open"
    COPY = "copy"
    MOVE = "move"
    DELETE = "delete"
    LIST = "list"
    SEARCH = "search"
    WRITE = "write"
    RENAME = "rename"
    RUN = "run"
    INSTALL = "install"
    NAVIGATE = "navigate"
    READ = "read"
    SUMMARIZE = "summarize"
    UNKNOWN = "unknown"


@dataclass
class ParsedAction:
    action_type: str
    target: str = ""
    source: str = ""
    destination: str = ""
    content: str = ""
    depends_on: list[str] = field(default_factory=list)
    expected_output: str = ""


@dataclass
class ParsedGoal:
    raw_message: str
    goal: str = ""
    entities: dict[str, str] = field(default_factory=dict)
    targets: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    desired_result: str = ""
    references: dict[str, str] = field(default_factory=dict)
    required_actions: list[ActionKind] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    ambiguity: str = ""
    requires_ui: bool = False
    is_multi_step: bool = False
    confidence: float = 0.0
    source_location: str = ""
    file_type: str = ""
    file_pattern: str = ""
    operations: list[str] = field(default_factory=list)
    destination: str = ""
    output_filename: str = ""
    requires_read: bool = False
    requires_summary: bool = False
    requires_copy: bool = False
    requires_write: bool = False
    requires_open: bool = False
    actions: list[ParsedAction] = field(default_factory=list)
    ordered_steps: list[ParsedAction] = field(default_factory=list)
    continuation_requirements: list[str] = field(default_factory=list)
    requires_tool_output_dependency: bool = False
    outputs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "entities": self.entities,
            "targets": self.targets,
            "constraints": self.constraints,
            "desired_result": self.desired_result,
            "references": self.references,
            "required_actions": [item.value for item in self.required_actions],
            "dependencies": self.dependencies,
            "ambiguity": self.ambiguity,
            "requires_ui": self.requires_ui,
            "is_multi_step": self.is_multi_step,
            "confidence": self.confidence,
            "source_location": self.source_location,
            "file_type": self.file_type,
            "file_pattern": self.file_pattern,
            "operations": self.operations,
            "destination": self.destination,
            "output_filename": self.output_filename,
            "requires_read": self.requires_read,
            "requires_summary": self.requires_summary,
            "requires_copy": self.requires_copy,
            "requires_write": self.requires_write,
            "requires_open": self.requires_open,
            "actions": [
                {
                    "action_type": item.action_type,
                    "target": item.target,
                    "source": item.source,
                    "destination": item.destination,
                    "content": item.content,
                    "depends_on": item.depends_on,
                    "expected_output": item.expected_output,
                }
                for item in self.actions
            ],
            "ordered_steps": [
                {
                    "action_type": item.action_type,
                    "target": item.target,
                    "source": item.source,
                    "destination": item.destination,
                    "content": item.content,
                    "depends_on": item.depends_on,
                    "expected_output": item.expected_output,
                }
                for item in self.ordered_steps
            ],
            "continuation_requirements": self.continuation_requirements,
            "requires_tool_output_dependency": self.requires_tool_output_dependency,
            "outputs": self.outputs,
        }


_STEP_SPLIT = re.compile(
    r"\s+(?:ve|sonra|ardindan|ardından|then|and|,)\s+",
    re.IGNORECASE,
)
_OPEN_UI = re.compile(r"\b(ac|aç|open|goster|göster|baslat|başlat)\b", re.IGNORECASE)
_FILE_TYPE = re.compile(
    r"\b(pdf|txt|docx?|word|excel|xlsx?|png|jpg|jpeg|resim|gorsel|görsel)\b",
    re.IGNORECASE,
)
_WHEN_FILTER = re.compile(r"\b(dun|dün|bugun|bugün|son\s+\d+\s+gun|son\s+\d+\s+gün)\b", re.IGNORECASE)
_OUTPUT_FILE = re.compile(
    r"\b([A-Za-z0-9_\-\.]+\.(?:txt|pdf|docx?|md))\b",
    re.IGNORECASE,
)


def _resolve_destination_path(text: str, lower: str) -> str:
    location = extract_location_path(text)
    folder_name = extract_named_folder_name(text)
    if not folder_name:
        folder_match = re.search(
            r"(?:masa[uü]st(?:u|ü)(?:nde|de|ne)?\s+)?([\w\d_.-]+)\s+klas(?:o|ö)r",
            text,
            re.IGNORECASE,
        )
        if folder_match:
            folder_name = folder_match.group(1).strip()
    if folder_name:
        return str((location / folder_name).resolve())
    alias = detect_known_folder_alias(text)
    if alias:
        return str((Path.home() / alias).resolve())
    return ""


def _looks_like_media_open(text: str) -> bool:
    """True when the utterance is about opening media, not a system folder."""
    from hermes.screen.reference import looks_like_media_open

    return looks_like_media_open(text)


def parse_goal(
    message: str,
    ctx: ConversationalContext | None = None,
    *,
    resolved_references: dict[str, str] | None = None,
) -> ParsedGoal:
    text = (message or "").strip()
    parsed = ParsedGoal(raw_message=text)
    if not text:
        return parsed

    lower = text.casefold()
    refs = dict(resolved_references or {})
    if ctx is not None:
        for key, value in ctx.resolved_references(text).items():
            refs.setdefault(key, value)

    parts = [part.strip(" .;") for part in _STEP_SPLIT.split(text) if part.strip(" .;")]
    parsed.is_multi_step = len(parts) > 1
    parsed.goal = text
    parsed.references = refs
    parsed.requires_ui = bool(_OPEN_UI.search(lower))

    source_alias = detect_known_folder_alias(text)
    if source_alias and not _looks_like_media_open(text):
        source_path = str((Path.home() / source_alias).resolve())
        parsed.source_location = source_path
        parsed.entities["source_location"] = source_path

    for label in ("downloads", "desktop", "documents"):
        if detect_known_folder_alias(text) == label.title() or label in lower:
            known = resolve_known_folder(label)
            if known:
                parsed.entities[label] = str(known)
                if label == "downloads" and not parsed.source_location:
                    parsed.source_location = str(known)

    quoted = re.findall(r"['\"]([^'\"]{1,80})['\"]", text)
    if quoted:
        parsed.entities["quoted_name"] = quoted[0]

    folder_name = extract_named_folder_name(text)
    if not folder_name:
        folder_match = re.search(
            r"(?:masa[uü]st(?:u|ü)(?:nde|de|ne)?\s+)?([\w\d_.-]+)\s+klas(?:o|ö)r",
            text,
            re.IGNORECASE,
        )
        if folder_match:
            folder_name = folder_match.group(1).strip()
    if folder_name:
        parsed.entities["folder_name"] = folder_name

    file_match = _OUTPUT_FILE.search(text)
    if file_match:
        parsed.entities["file_name"] = file_match.group(1)
        parsed.output_filename = file_match.group(1)

    pattern = extract_file_type_pattern(text)
    if pattern:
        parsed.file_pattern = pattern
        parsed.constraints["file_pattern"] = pattern
        parsed.file_type = "pdf" if "pdf" in pattern else Path(pattern).suffix.lstrip(".") or pattern
        parsed.constraints["file_type"] = parsed.file_type

    type_match = _FILE_TYPE.search(lower)
    if type_match and not parsed.file_type:
        from hermes.context.task_state import normalize_format

        raw_type = type_match.group(1).casefold()
        parsed.file_type = normalize_format(raw_type) or raw_type
        parsed.constraints["file_type"] = parsed.file_type
        if parsed.file_type == "pdf" and not parsed.file_pattern:
            parsed.file_pattern = "*.pdf"
        elif parsed.file_type == "docx" and not parsed.file_pattern:
            parsed.file_pattern = "*.docx"
        elif parsed.file_type == "xlsx" and not parsed.file_pattern:
            parsed.file_pattern = "*.xlsx"

    when_match = _WHEN_FILTER.search(lower)
    if when_match:
        token = when_match.group(1).casefold()
        if "dün" in token or "dun" in token:
            parsed.constraints["modified_within_hours"] = 48
        elif "bugün" in token or "bugun" in token:
            parsed.constraints["modified_within_hours"] = 24

    actions: list[ActionKind] = []
    if re.search(r"\b(olustur|oluştur|yarat|create)\b", lower):
        actions.append(ActionKind.CREATE)
    if re.search(r"\b(kopyala|copy)\b", lower):
        actions.append(ActionKind.COPY)
    if re.search(r"\b(tasi|taşı|move|aktar)\b", lower):
        actions.append(ActionKind.MOVE)
    if re.search(r"\b(sil|delete|kaldir|kaldır)\b", lower):
        actions.append(ActionKind.DELETE)
    if re.search(r"\b(listele|goster|göster|list)\b", lower):
        actions.append(ActionKind.LIST)
    if re.search(r"\b(bul|ara|search|find)\b", lower):
        actions.append(ActionKind.SEARCH)
    if re.search(r"\b(yaz|write|icerik|içerik)\b", lower):
        actions.append(ActionKind.WRITE)
    if re.search(r"\b(adini|adını|rename|yeniden ad)\b", lower):
        actions.append(ActionKind.RENAME)
    if re.search(r"\b(oku\w*|read|extract)\b", lower):
        actions.append(ActionKind.READ)
    if re.search(r"\b(ozet\w*|özet\w*|summarize|summary|rapor\s+ozet)\b", lower):
        actions.append(ActionKind.SUMMARIZE)
    if _OPEN_UI.search(lower):
        actions.append(ActionKind.OPEN)
    if re.search(r"\b(kur|install|yukle|yükle)\b", lower):
        actions.append(ActionKind.INSTALL)
    if re.search(r"\b(git|navigate|site)\b", lower) and "chrome" in lower:
        actions.append(ActionKind.NAVIGATE)

    parsed.required_actions = actions or [ActionKind.UNKNOWN]
    parsed.requires_read = ActionKind.READ in actions or (
        "pdf" in lower and re.search(r"\b(oku\w*|read)\b", lower) is not None
    )
    parsed.requires_summary = ActionKind.SUMMARIZE in actions
    parsed.requires_copy = ActionKind.COPY in actions
    parsed.requires_write = ActionKind.WRITE in actions
    parsed.requires_open = ActionKind.OPEN in actions

    destination = _resolve_destination_path(text, lower)
    if destination:
        parsed.destination = destination
        parsed.entities["destination"] = destination

    operation_map = {
        ActionKind.SEARCH: "search",
        ActionKind.READ: "read",
        ActionKind.SUMMARIZE: "summarize",
        ActionKind.CREATE: "create_folder",
        ActionKind.WRITE: "write_file",
        ActionKind.COPY: "copy",
        ActionKind.OPEN: "open",
        ActionKind.LIST: "list",
    }
    parsed.operations = [
        operation_map[item]
        for item in parsed.required_actions
        if item in operation_map
    ]

    if parsed.is_multi_step:
        parsed.dependencies = [f"step_{index}" for index in range(1, len(parts))]

    if parsed.requires_read and parsed.requires_summary and parsed.requires_write:
        parsed.desired_result = "PDF iceriklerini okuyup ozet rapor dosyasina yaz"
        parsed.confidence = 0.86
    elif "pdf" in lower and ActionKind.SEARCH in actions and ActionKind.COPY in actions:
        parsed.desired_result = "Eslesen PDF dosyalarini hedef klasore kopyala"
        parsed.confidence = 0.82
    elif ActionKind.CREATE in actions and "klas" in lower:
        parsed.desired_result = "Hedef klasoru olustur"
        parsed.confidence = 0.85
    elif len(actions) == 1 and actions[0] is not ActionKind.UNKNOWN:
        parsed.desired_result = f"{actions[0].value} islemi"
        parsed.confidence = 0.78
    else:
        parsed.confidence = 0.55

    deictic_only = re.search(r"\b(onu|bunu|şunu|sunu|bu|o)\b", lower) and not refs
    explicit_target = bool(
        parsed.source_location
        or parsed.file_pattern
        or parsed.output_filename
        or folder_name
        or file_match
    )
    if deictic_only and not explicit_target:
        parsed.ambiguity = "referans_netlestirme"
        parsed.confidence = min(parsed.confidence, 0.45)

    from hermes.mission.compound_goal import is_compound_chained_file_mission, parse_compound_file_goal
    from hermes.mission.write_content import requires_tool_output_dependency as write_requires_tool_output

    parsed.requires_tool_output_dependency = write_requires_tool_output(text)
    if is_compound_chained_file_mission(text):
        compound = parse_compound_file_goal(text)
        if compound is not None:
            parsed.ordered_steps = [
                ParsedAction(
                    action_type=item.action_type,
                    target=item.target,
                    source=item.source,
                    destination=item.destination,
                    content=item.content,
                    depends_on=list(item.depends_on),
                    expected_output=item.expected_output,
                )
                for item in compound.ordered_steps
            ]
            parsed.actions = list(parsed.ordered_steps)
            parsed.entities["folder_name"] = compound.folder_name
            parsed.entities["folder_path"] = compound.folder_path
            parsed.is_multi_step = True
            parsed.confidence = max(parsed.confidence, 0.9)
            parsed.desired_result = "Bagimli dosya islemleri zinciri"
            parsed.continuation_requirements = ["verify_between_steps", "context_propagation"]
            if compound.open_target:
                parsed.outputs["last_opened_file"] = str(
                    Path(compound.folder_path) / compound.open_target
                )

    return parsed
