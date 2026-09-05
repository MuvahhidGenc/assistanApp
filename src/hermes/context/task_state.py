"""Authoritative task parameters, separate from historical slots.

History (recent_files, last_created_*) may inform memory later. Only these
fields plus active_focus / container_focus may choose the current target.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


_FORMAT_ALIASES = {
    "pdf": "pdf",
    "word": "docx",
    "docx": "docx",
    "doc": "docx",
    "excel": "xlsx",
    "xlsx": "xlsx",
    "xls": "xlsx",
    "txt": "txt",
    "text": "txt",
    "md": "md",
}

_FORMAT_TOOLS = {
    "docx": "create_word_document",
    "pdf": "write_file",
    "xlsx": "write_file",
    "txt": "write_file",
    "md": "write_file",
}

_STATUS_VALUES = frozenset({"active", "waiting", "completed", "failed", "cancelled", "unverified"})


@dataclass
class TaskParameters:
    objective: str = ""
    action: str = ""
    target: str = ""
    container: str = ""
    format: str = ""
    content: str = ""
    constraints: str = ""
    status: str = ""
    topic: str = ""
    destination: str = ""
    filename: str = ""
    source_path: str = ""

    def to_dict(self) -> dict[str, str]:
        container = self.container or self.destination
        target = self.target or self.source_path
        return {
            key: value
            for key, value in {
                "objective": self.objective,
                "action": self.action,
                "target": target,
                "container": container,
                "format": self.format,
                "content": self.content,
                "constraints": self.constraints,
                "status": self.status,
                "topic": self.topic,
                "destination": container,
                "filename": self.filename,
                "source_path": self.source_path or target,
            }.items()
            if value
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> TaskParameters:
        raw = data if isinstance(data, dict) else {}
        container = str(raw.get("container") or raw.get("destination") or "")
        target = str(raw.get("target") or raw.get("source_path") or "")
        status = str(raw.get("status") or "").strip().casefold()
        if status not in _STATUS_VALUES:
            status = ""
        return cls(
            objective=str(raw.get("objective") or ""),
            action=str(raw.get("action") or ""),
            target=target,
            container=container,
            format=normalize_format(raw.get("format")),
            content=str(raw.get("content") or ""),
            constraints=str(raw.get("constraints") or ""),
            status=status,
            topic=str(raw.get("topic") or ""),
            destination=container,
            filename=str(raw.get("filename") or ""),
            source_path=str(raw.get("source_path") or target),
        )

    def merge(self, other: TaskParameters) -> TaskParameters:
        container = other.container or other.destination or self.container or self.destination
        target = other.target or other.source_path or self.target or self.source_path
        return TaskParameters(
            objective=other.objective or self.objective,
            action=other.action or self.action,
            target=target,
            container=container,
            format=other.format or self.format,
            content=other.content or self.content,
            constraints=other.constraints or self.constraints,
            status=other.status or self.status,
            topic=other.topic or self.topic,
            destination=container,
            filename=other.filename or self.filename,
            source_path=other.source_path or other.target or self.source_path or self.target,
        )


def normalize_format(value: str | None) -> str:
    token = str(value or "").strip().casefold().lstrip("*.")
    if token.startswith("."):
        token = token[1:]
    return _FORMAT_ALIASES.get(token, token if token in _FORMAT_TOOLS else "")


def tool_for_format(fmt: str) -> str:
    return _FORMAT_TOOLS.get(normalize_format(fmt) or "txt", "write_file")


def apply_format_to_path(path: str, fmt: str) -> str:
    suffix = normalize_format(fmt) or "txt"
    target = Path(path)
    if target.suffix:
        return str(target.with_suffix(f".{suffix}"))
    name = target.name or "belge"
    return str(target.with_name(f"{name}.{suffix}"))


def read_preserved_content(path: str | None) -> str:
    """Read prior artifact text without treating binary Office files as UTF-8."""
    if not path:
        return ""
    target = Path(path)
    if not target.is_file():
        return ""
    suffix = target.suffix.casefold()
    if suffix == ".docx":
        try:
            from hermes.mission.reality_verification import extract_docx_text

            return extract_docx_text(target)
        except Exception:
            try:
                from docx import Document

                return "\n\n".join(
                    para.text.strip()
                    for para in Document(str(target)).paragraphs
                    if para.text.strip()
                )
            except Exception:
                return ""
    if suffix == ".pdf":
        try:
            from hermes.mission.reality_verification import extract_pdf_text

            result = extract_pdf_text(target)
            return result.text if result.ok else ""
        except Exception:
            return ""
    if suffix in {".xlsx", ".xls"}:
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def preserved_task_content(
    params: TaskParameters,
    *,
    incoming_message: str = "",
) -> str:
    """Content of the current document — never the user's latest sentence."""
    incoming = (incoming_message or "").strip()
    stored = (params.content or "").strip()
    if stored and stored.casefold() != incoming.casefold():
        return stored
    from_file = read_preserved_content(params.source_path or params.target)
    if from_file and from_file.casefold() != incoming.casefold():
        return from_file
    topic = (params.topic or "").strip()
    objective = (params.objective or "").strip()
    # Topic that merely restates the goal is not document body.
    if (
        topic
        and topic.casefold() != incoming.casefold()
        and topic.casefold() != objective.casefold()
    ):
        return topic
    return ""


def infer_topic(objective: str) -> str:
    text = (objective or "").strip()
    return text[:120]


def parameters_from_path(
    path: str,
    *,
    action: str = "create_document",
    topic: str = "",
    content: str = "",
    objective: str = "",
) -> TaskParameters:
    resolved = Path(path)
    parent = str(resolved.parent)
    return TaskParameters(
        objective=objective or topic,
        action=action,
        target=str(resolved),
        container=parent,
        format=normalize_format(resolved.suffix),
        content=content,
        status="active",
        topic=topic,
        destination=parent,
        filename=resolved.name,
        source_path=str(resolved),
    )
