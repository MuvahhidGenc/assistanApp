from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from hermes.context.folder_reference import (
    extract_named_folder_name,
    extract_new_filename,
    message_uses_contextual_folder,
    resolve_folder_path_from_message,
)

_CREATE_VERBS = re.compile(
    r"\b(olustur|oluştur|yarat|create|hazirla|hazırla)\b",
    re.IGNORECASE,
)
_WRITE_VERBS = re.compile(r"\b(yaz|write)\b", re.IGNORECASE)
_MODIFY_PATTERNS = (
    re.compile(r"içeriğini|icerigini", re.IGNORECASE),
    re.compile(r"icerigini\s+sadece", re.IGNORECASE),
    re.compile(r"içeriğini\s+sadece", re.IGNORECASE),
)


class FileIntentKind(StrEnum):
    CREATE_FILE = "create_file"
    WRITE_FILE = "write_file"
    MODIFY_CONTENT = "modify_content"


@dataclass(frozen=True)
class ResolvedFileTarget:
    path: str
    folder_path: str | None
    filename: str
    kind: FileIntentKind
    content: str | None = None


def is_content_modify_only_message(text: str) -> bool:
    lower = (text or "").casefold()
    if not any(pattern.search(text or "") for pattern in _MODIFY_PATTERNS):
        return False
    return not _CREATE_VERBS.search(text or "")


def is_empty_file_create_message(text: str) -> bool:
    """True for 'içine test.txt oluştur' without explicit write content."""
    normalized = (text or "").strip()
    if not normalized:
        return False
    if is_content_modify_only_message(normalized):
        return False
    if not _CREATE_VERBS.search(normalized):
        return False
    if not extract_new_filename(normalized):
        return False
    if has_explicit_write_content(normalized):
        return False
    return True


def has_explicit_write_content(text: str) -> bool:
    from hermes.context.content_extraction import extract_content_modification
    from hermes.mission.write_content import extract_literal_write_content

    if extract_content_modification(text) is not None:
        return True
    hint = extract_literal_write_content(text)
    return hint.literal_content is not None


_READ_CONTENT_PATTERNS = (
    re.compile(r"icinde\s+ne\s+yaz", re.IGNORECASE),
    re.compile(r"içinde\s+ne\s+yaz", re.IGNORECASE),
    re.compile(r"icerigini\s+oku", re.IGNORECASE),
    re.compile(r"içeriğini\s+oku", re.IGNORECASE),
    re.compile(r"icerigi\s+oku", re.IGNORECASE),
    re.compile(r"içeriği\s+oku", re.IGNORECASE),
    re.compile(r"ne\s+yaziyor", re.IGNORECASE),
    re.compile(r"ne\s+yazıyor", re.IGNORECASE),
    re.compile(r"ne\s+yaziyor", re.IGNORECASE),
)


def is_file_read_message(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    lower = normalized.casefold()
    if re.search(r"\b(olustur|oluştur|yarat|create)\b", lower):
        return False
    if any(pattern.search(normalized) for pattern in _READ_CONTENT_PATTERNS):
        return True
    if re.search(r"buraya\s+yaz", lower) and (
        re.search(r"ne\s+yaz", lower)
        or re.search(r"son\s+olustur", lower)
        or re.search(r"son\s+oluştur", lower)
        or re.search(r"\.txt\b", lower)
    ):
        return True
    if re.search(r"^buraya\s+yaz\.?$", lower):
        return True
    return False


def resolve_read_file_path(
    text: str,
    ctx: Any,
    *,
    resolved_references: dict[str, str] | None = None,
) -> str | None:
    """Resolve filesystem path for read_file intent."""
    from pathlib import Path as _Path

    refs = dict(resolved_references or {})
    lower = (text or "").casefold()

    def _existing(path: str | None) -> str | None:
        if not path:
            return None
        try:
            target = _Path(str(path)).expanduser()
            if not target.is_absolute():
                target = _Path.home() / "Desktop" / target
            target = target.resolve()
        except OSError:
            return None
        return str(target) if target.is_file() else None

    if re.search(r"son\s+olustur|son\s+oluştur", lower):
        created = _existing(refs.get("last_created_file") or getattr(ctx, "last_created_file", None))
        if created:
            return created
        if re.search(r"\brapor", lower):
            for path in list(getattr(ctx, "created_files", []) or []):
                if Path(str(path)).name.casefold().startswith("rapor"):
                    found = _existing(str(path))
                    if found:
                        return found
        explicit_match = re.search(r"([\w\d_.-]+\.(?:txt|md))\b", text, re.IGNORECASE)
        if explicit_match:
            hint_name = explicit_match.group(1).casefold()
            for path in list(getattr(ctx, "created_files", []) or []) + list(
                getattr(ctx, "recent_files", []) or []
            ):
                if Path(str(path)).name.casefold() == hint_name:
                    found = _existing(str(path))
                    if found:
                        return found

    for key in ("target_file", "last_created_file", "active_file", "last_opened_file"):
        found = _existing(refs.get(key) or getattr(ctx, key, None))
        if found:
            return found

    if not re.search(r"son\s+olustur|son\s+oluştur", lower):
        from hermes.context.folder_reference import extract_explicit_filename

        explicit_name = extract_explicit_filename(text)
        if explicit_name:
            matches: list[str] = []
            for path in list(getattr(ctx, "recent_files", []) or []):
                if Path(str(path)).name.casefold() == explicit_name.casefold():
                    found = _existing(str(path))
                    if found and found not in matches:
                        matches.append(found)
            if getattr(ctx, "active_file", None) and Path(ctx.active_file).name.casefold() == explicit_name.casefold():
                found = _existing(ctx.active_file)
                if found and found not in matches:
                    matches.append(found)
            desktop = _Path.home() / "Desktop" / explicit_name
            found = _existing(str(desktop))
            if found and found not in matches:
                matches.append(found)
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None

        from hermes.context.reference_resolver import match_named_file

        named = match_named_file(text, list(getattr(ctx, "recent_files", []) or []), allow_stem=True)
        explicit = named.resolved_references.get("target_file")
        if explicit:
            found = _existing(explicit)
            if found:
                return found

    from hermes.context.agent_context import resolve_deictic_file

    resolved = resolve_deictic_file(ctx, text, intent="open_file")
    if resolved:
        found = _existing(resolved.path)
        if found:
            return found
    return None


def classify_file_intent(text: str, *, content: str | None = None) -> FileIntentKind:
    if is_content_modify_only_message(text):
        return FileIntentKind.MODIFY_CONTENT
    if has_explicit_write_content(text) or (content is not None and content != ""):
        if _CREATE_VERBS.search(text or ""):
            return FileIntentKind.WRITE_FILE
        return FileIntentKind.MODIFY_CONTENT
    if is_empty_file_create_message(text) or (
        _CREATE_VERBS.search(text or "") and extract_new_filename(text)
    ):
        return FileIntentKind.CREATE_FILE
    return FileIntentKind.WRITE_FILE


def resolve_file_create_target(
    text: str,
    *,
    active_folder: str | None = None,
    last_created_folder: str | None = None,
) -> ResolvedFileTarget | None:
    """
    Resolve folder + filename for file creation from NL.

    Never falls back to Desktop when an active/target folder is available.
    """
    normalized = (text or "").strip()
    if not normalized:
        return None
    if not _CREATE_VERBS.search(normalized):
        return None

    file_name = extract_new_filename(normalized)
    if not file_name:
        return None

    folder_path = resolve_folder_path_from_message(
        normalized,
        active_folder=active_folder,
        last_created_folder=last_created_folder,
    )
    if not folder_path and extract_named_folder_name(normalized):
        folder_path = str(
            (Path.home() / "Desktop" / extract_named_folder_name(normalized)).resolve()
        )
    if not folder_path and (
        message_uses_contextual_folder(normalized)
        or re.search(r"^(?:icine|içine)\b", normalized, re.IGNORECASE)
    ):
        folder_path = active_folder or last_created_folder
    if not folder_path and extract_new_filename(normalized):
        folder_path = active_folder or last_created_folder

    if not folder_path:
        return None

    file_path = str(Path(folder_path) / Path(file_name).name)
    from hermes.context.content_extraction import extract_content_modification
    from hermes.mission.write_content import extract_literal_write_content

    content = extract_content_modification(normalized)
    if content is None:
        content = extract_literal_write_content(normalized).literal_content

    kind = classify_file_intent(normalized, content=content)
    if kind == FileIntentKind.MODIFY_CONTENT:
        return None

    return ResolvedFileTarget(
        path=file_path,
        folder_path=folder_path,
        filename=Path(file_name).name,
        kind=kind,
        content=content if kind == FileIntentKind.WRITE_FILE else "",
    )
