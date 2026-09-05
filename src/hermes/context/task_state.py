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


@dataclass
class TaskParameters:
    action: str = ""
    format: str = ""
    topic: str = ""
    destination: str = ""
    filename: str = ""
    content: str = ""
    source_path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "action": self.action,
                "format": self.format,
                "topic": self.topic,
                "destination": self.destination,
                "filename": self.filename,
                "content": self.content,
                "source_path": self.source_path,
            }.items()
            if value
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> TaskParameters:
        raw = data if isinstance(data, dict) else {}
        return cls(
            action=str(raw.get("action") or ""),
            format=normalize_format(raw.get("format")),
            topic=str(raw.get("topic") or ""),
            destination=str(raw.get("destination") or ""),
            filename=str(raw.get("filename") or ""),
            content=str(raw.get("content") or ""),
            source_path=str(raw.get("source_path") or ""),
        )

    def merge(self, other: TaskParameters) -> TaskParameters:
        return TaskParameters(
            action=other.action or self.action,
            format=other.format or self.format,
            topic=other.topic or self.topic,
            destination=other.destination or self.destination,
            filename=other.filename or self.filename,
            content=other.content or self.content,
            source_path=other.source_path or self.source_path,
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
            from docx import Document

            return "\n\n".join(
                para.text.strip()
                for para in Document(str(target)).paragraphs
                if para.text.strip()
            )
        except Exception:
            return ""
    if suffix in {".pdf", ".xlsx", ".xls", ".docx"}:
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
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
) -> TaskParameters:
    resolved = Path(path)
    parent = str(resolved.parent)
    return TaskParameters(
        action=action,
        format=normalize_format(resolved.suffix),
        topic=topic,
        destination=parent,
        filename=resolved.name,
        content=content,
        source_path=str(resolved),
    )
