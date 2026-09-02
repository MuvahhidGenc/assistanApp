from __future__ import annotations

import re
from pathlib import Path

from hermes.context.goal_resolution import (
    CreateFolderGoal,
    OpenFolderGoal,
    extract_create_folder_target,
    extract_location_path,
    extract_named_folder_name,
    parse_create_folder_goal,
    parse_open_folder_goal,
    resolve_create_folder_path,
)

__all__ = [
    "CreateFolderGoal",
    "OpenFolderGoal",
    "extract_create_folder_target",
    "extract_location_path",
    "extract_named_folder_name",
    "parse_create_folder_goal",
    "parse_open_folder_goal",
    "resolve_create_folder_path",
    "resolve_desktop_folder_path",
    "resolve_folder_path_from_message",
    "message_uses_contextual_folder",
    "extract_new_filename",
    "extract_explicit_filename",
    "resolve_explicit_desktop_file",
    "is_file_create_message",
    "is_folder_create_message",
    "is_folder_open_only_message",
    "build_file_path_in_folder",
    "extract_desktop_relative_file_path",
]

_CONTEXTUAL_FOLDER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"son\s+oluşturduğun\s+klas(?:ö|o)r(?:u|ü|unun|ünün)?", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+klas(?:o|ö)r(?:u|u|unun|unun)?", re.IGNORECASE),
    re.compile(r"son\s+oluşturduğun\s+klas(?:ö|o)re", re.IGNORECASE),
    re.compile(r"son\s+olusturdugun\s+klas(?:o|ö)re", re.IGNORECASE),
    re.compile(r"aynı\s+klas(?:ö|o)re", re.IGNORECASE),
    re.compile(r"ayni\s+klas(?:o|ö)re", re.IGNORECASE),
    re.compile(r"\b(o|bu|şu|su)\s+klas(?:ö|o)r(?:u|ü|e|a|une|üne|re|ye)?\b", re.IGNORECASE),
    re.compile(r"\b(oraya|buraya)\b", re.IGNORECASE),
    re.compile(r"^(?:icine|içine)\b", re.IGNORECASE),
)

_CREATE_VERBS = re.compile(
    r"\b(olustur|oluştur|yarat|create|hazirla|hazırla)\b",
    re.IGNORECASE,
)
_OPEN_ONLY_VERBS = re.compile(
    r"\b(ac|aç|open|gir|geç|gec|baslat|başlat|calistir|çalıştır)\b",
    re.IGNORECASE,
)


def message_uses_contextual_folder(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _CONTEXTUAL_FOLDER_PATTERNS)


def resolve_desktop_folder_path(folder_name: str) -> Path:
    return (Path.home() / "Desktop" / folder_name.strip()).resolve()


def resolve_folder_path_from_message(
    text: str,
    *,
    active_folder: str | None = None,
    last_created_folder: str | None = None,
) -> str | None:
    """Resolve target folder path from named reference or conversational context."""
    named = extract_named_folder_name(text)
    if named:
        return str(resolve_desktop_folder_path(named))
    if message_uses_contextual_folder(text):
        return active_folder or last_created_folder
    return None


_EXPLICIT_FILENAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"['\"]?([\w\d_.\-]+(?:\s+\(\d+\))?\.(?:txt|md|docx|pdf))['\"]?",
        re.IGNORECASE,
    ),
)


def extract_explicit_filename(text: str) -> str | None:
    """Extract a literal filename from NL, including numbered variants like rapor (1).txt."""
    normalized = text or ""
    for pattern in _EXPLICIT_FILENAME_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        name = Path(match.group(1)).name.strip()
        if name:
            return name
    return None


def resolve_explicit_desktop_file(text: str) -> str | None:
    """Resolve an explicitly named desktop file if it exists."""
    name = extract_explicit_filename(text)
    if not name:
        return None
    candidate = (Path.home() / "Desktop" / name).resolve()
    return str(candidate) if candidate.is_file() else None


def extract_new_filename(text: str) -> str | None:
    match = re.search(r"\b([\w\d_.-]+\.(?:txt|md|docx))\b", text or "", re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(
        r"(?:olustur|oluştur|yarat|create)\s+(?:ve\s+)?(?:içine\s+)?([\w\d_.-]+)\s+(?:dosya|txt)",
        text or "",
        re.IGNORECASE,
    )
    if match:
        name = match.group(1).strip(" .")
        if name and not name.lower().endswith(".txt"):
            name = f"{name}.txt"
        return name
    match = re.search(
        r"(?:sadece\s+)?([\w\d_.-]+)\s+dosya(?:s[ıi])?\s+(?:olustur|oluştur|yarat|create)",
        text or "",
        re.IGNORECASE,
    )
    if match:
        name = match.group(1).strip(" .")
        if name and not name.lower().endswith(".txt"):
            name = f"{name}.txt"
        return name
    match = re.search(r"\b([\w\d_.-]+)\.txt\b", text or "", re.IGNORECASE)
    if match:
        return match.group(0)
    return None


def is_file_create_message(text: str) -> bool:
    """True when the user wants to create/write a file, not a standalone folder."""
    lower = (text or "").casefold()
    if re.search(r"\.txt|\.md|\.docx|\bdosya\b", lower) and _CREATE_VERBS.search(text or ""):
        return True
    if re.search(r"icine|içine", lower) and extract_new_filename(text):
        return True
    if extract_new_filename(text) and _CREATE_VERBS.search(text or ""):
        return True
    return False


def is_folder_create_message(text: str) -> bool:
    """True when user wants to create a folder (not merely open an existing one)."""
    if is_file_create_message(text):
        return False
    lower = (text or "").casefold()
    if not re.search(r"klasor|klasör|folder|dizin", lower):
        return False
    if _CREATE_VERBS.search(text or ""):
        return True
    if re.search(r"yeni\s+(?:bir\s+)?klas", lower):
        return not is_folder_open_only_message(text)
    return False


def is_folder_open_only_message(text: str) -> bool:
    """Distinguish 'klasörü aç' from 'klasör oluştur'."""
    lower = (text or "").casefold()
    if not re.search(r"klasor|klasör|folder|dizin", lower):
        return False
    if _CREATE_VERBS.search(text or ""):
        return False
    if re.search(r"yeni\s+(?:bir\s+)?klas", lower):
        return False
    return bool(_OPEN_ONLY_VERBS.search(text or ""))


def build_file_path_in_folder(folder_path: str, file_name: str) -> str:
    return str(Path(folder_path) / file_name)


def extract_desktop_relative_file_path(text: str) -> str | None:
    """
    Build Desktop-relative file path honoring explicit folder references.

    Invariant: when a target folder is named, the file path must live under it.
    """
    direct = re.search(
        r"([\w][\w\s.-]*[/\\][\w\s.-]+\.(?:docx|txt|md))",
        text or "",
        re.IGNORECASE,
    )
    if direct:
        return direct.group(1).replace("\\", "/").strip()

    folder_path = resolve_folder_path_from_message(text)
    folder_name = extract_named_folder_name(text)

    file_name = extract_new_filename(text)
    if file_name:
        if folder_path:
            return build_file_path_in_folder(folder_path, Path(file_name).name)
        if folder_name:
            return f"{folder_name}/{Path(file_name).name}"
        return Path(file_name).name

    lower = (text or "").casefold()
    stem_match = re.search(r"\b([\w.-]+)\s+word\b", lower)
    if stem_match:
        stem = stem_match.group(1)
        ext = f"{stem}.docx"
        if folder_name:
            return f"{folder_name}/{ext}"
        return ext
    return None
