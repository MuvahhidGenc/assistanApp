"""Resolve 'open the folder containing this file' references."""
from __future__ import annotations

import re
from pathlib import Path

from hermes.context.conversational_context import ConversationalContext

# Never treat these as literal folder names (goal_resolution reserved set mirrors this).
PSEUDO_FOLDER_NAMES = frozenset(
    {
        "bulunduğu",
        "bulundugu",
        "bulundukları",
        "bulunduklari",
        "bulunduklarını",
        "bulunduklarini",
        "olduğu",
        "oldugu",
        "bulundu",
    }
)

_PARENT_DIRECTORY_INTENT = re.compile(
    r"(?:"
    r"bulundu(?:ğu|gu|klari|ları|kları|klarını|klarini)\s+klas(?:o|ö)r(?:u|ü|unu|ünü)?|"
    r"oldu(?:ğu|gu)\s+klas(?:o|ö)r(?:u|ü|unu|ünü)?|"
    r"dosya(?:nın|nin|sının|sinin)\s+klas(?:o|ö)r(?:u|ü|unu|ünü)?"
    r")",
    re.IGNORECASE,
)

_OPEN_VERBS = re.compile(r"\b(aç|ac|open|goster|göster|baslat|başlat)\b", re.IGNORECASE)

_EXPLICIT_FILE_PARENT = (
    re.compile(
        r"([\w\d_.\-şğüöçıİŞĞÜÖÇ]+(?:\.(?:txt|md|docx))?)"
        r"(?:'?(?:nin|nın|nin|nın|in|ın|un|ün))?s?\s+"
        r"(?:bulundu(?:ğu|gu|klari|ları|kları|klarını|klarini)|oldu(?:ğu|gu))\s+klas(?:o|ö)r",
        re.IGNORECASE,
    ),
    re.compile(
        r"([\w\d_.\-şğüöçıİŞĞÜÖÇ]+(?:\.(?:txt|md|docx))?)\s+dosya(?:sının|sinin|sini|sini)?\s+"
        r"(?:bulundu(?:ğu|gu|klari|ları|kları|klarını|klarini)|oldu(?:ğu|gu))\s+klas(?:o|ö)r",
        re.IGNORECASE,
    ),
)

_DEICTIC_FILE_PARENT = re.compile(
    r"\b(?:bu|o|şu|su|bunun|onun|şunun|sunun)\s+dosya(?:nın|nin|sının|sinin)?\s+"
    r"(?:bulundu(?:ğu|gu|klari|ları|kları|klarını|klarini)|oldu(?:ğu|gu))\s+klas(?:o|ö)r",
    re.IGNORECASE,
)

_SON_FILE_PARENT = re.compile(
    r"son\s+olusturdu(?:gumuz|gun|duğun|dugun)\s+dosya(?:nın|nin|sının|sinin)?\s+"
    r"(?:(?:bulundu(?:ğu|gu|klari|ları|kları|klarını|klarini)|oldu(?:ğu|gu))\s+klas|klas(?:o|ö)r)",
    re.IGNORECASE,
)

_DEICTIC_FOLDER_ONLY = re.compile(
    r"\b(?:bu|o|şu|su|bunun|onun|şunun|sunun)\s+dosya(?:nın|nin|sının|sinin)?\s+klas(?:o|ö)r",
    re.IGNORECASE,
)

_GENERIC_FILE_FOLDER = re.compile(
    r"\bdosya(?:nın|nin|sının|sinin)\s+klas(?:o|ö)r(?:u|ü|unu|ünü)?\s+(?:aç|ac|open|goster|göster)",
    re.IGNORECASE,
)

_BARE_PARENT = re.compile(
    r"(?:^|\b)(?:bulundu(?:ğu|gu|klari|ları|kları|klarını|klarini)|oldu(?:ğu|gu))\s+klas(?:o|ö)r",
    re.IGNORECASE,
)


def is_pseudo_folder_name(name: str) -> bool:
    return (name or "").strip().casefold() in PSEUDO_FOLDER_NAMES


def is_parent_directory_open_message(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    if not _OPEN_VERBS.search(normalized):
        return False
    if _PARENT_DIRECTORY_INTENT.search(normalized):
        return True
    if _DEICTIC_FOLDER_ONLY.search(normalized):
        return True
    if _GENERIC_FILE_FOLDER.search(normalized):
        return True
    return False


def _normalize_existing_file(path: str) -> Path | None:
    raw = (path or "").strip()
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.home() / "Desktop" / target
    try:
        target = target.resolve()
    except OSError:
        return None
    return target if target.is_file() else None


def _normalize_existing_folder(path: str) -> Path | None:
    raw = (path or "").strip()
    if not raw:
        return None
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = Path.home() / "Desktop" / target
    try:
        target = target.resolve()
    except OSError:
        return None
    return target if target.is_dir() else None


def _context_file_candidates(ctx: ConversationalContext) -> list[str]:
    from hermes.context.agent_context import _verified_file_candidates

    return [path for path, _source in _verified_file_candidates(ctx)]


def _matches_file_reference(name: str, path: str) -> bool:
    ref = (name or "").strip()
    if not ref:
        return False
    file_path = Path(path)
    ref_path = Path(ref)
    if ref_path.suffix:
        return file_path.name.casefold() == ref_path.name.casefold()
    return file_path.stem.casefold() == ref.casefold()


def _extract_explicit_file_token(text: str) -> str | None:
    for pattern in _EXPLICIT_FILE_PARENT:
        match = pattern.search(text)
        if match:
            return match.group(1).strip(" .'\"")
    return None


def resolve_file_reference_for_parent(text: str, ctx: ConversationalContext) -> str | None:
    """Resolve which file's parent folder the user means."""
    normalized = (text or "").strip()
    if not normalized:
        return None

    for pattern in _EXPLICIT_FILE_PARENT:
        match = pattern.search(normalized)
        if match:
            token = match.group(1).strip(" .'\"")
            for candidate in _context_file_candidates(ctx):
                if _matches_file_reference(token, candidate):
                    verified = _normalize_existing_file(candidate)
                    if verified is not None:
                        return str(verified)
            for candidate in _context_file_candidates(ctx):
                if _matches_file_reference(token, candidate):
                    return candidate

    if _DEICTIC_FILE_PARENT.search(normalized) or _DEICTIC_FOLDER_ONLY.search(normalized):
        from hermes.context.agent_context import resolve_deictic_file

        resolved = resolve_deictic_file(ctx, normalized, intent="open_file")
        if resolved is not None:
            return resolved.path

    if _GENERIC_FILE_FOLDER.search(normalized):
        from hermes.context.agent_context import resolve_deictic_file

        resolved = resolve_deictic_file(ctx, normalized, intent="open_file")
        if resolved is not None:
            return resolved.path

    if _SON_FILE_PARENT.search(normalized):
        from hermes.context.agent_context import resolve_deictic_file

        resolved = resolve_deictic_file(ctx, normalized, intent="open_file")
        if resolved is not None:
            verified = _normalize_existing_file(resolved.path)
            if verified is not None:
                return str(verified)

    if _BARE_PARENT.search(normalized):
        from hermes.context.agent_context import resolve_deictic_file

        resolved = resolve_deictic_file(ctx, normalized, intent="open_file")
        if resolved is not None:
            verified = _normalize_existing_file(resolved.path)
            if verified is not None:
                return str(verified)

    return None


def resolve_parent_directory_path(text: str, ctx: ConversationalContext) -> tuple[str | None, str]:
    """
    Return (folder_path, error_message).

    folder_path is absolute when the parent directory exists on disk.
    error_message is set when clarification is needed.
    """
    if not is_parent_directory_open_message(text):
        return None, ""

    file_path = resolve_file_reference_for_parent(text, ctx)
    explicit_token = _extract_explicit_file_token(text)
    if not file_path:
        if explicit_token:
            label = Path(explicit_token).name
            if not label.endswith(".txt") and "." not in label:
                label = f"{label}.txt"
            return None, f"{label} dosyasini bulamadim. Hangi dosyanin klasorunu acmami istiyorsun?"
        return None, "Hangi dosyanin bulundugu klasoru acmami istiyorsun?"

    verified_file = _normalize_existing_file(file_path)
    if verified_file is None:
        label = Path(file_path).name
        return None, f"{label} dosyasini bulamadim. Hangi dosyanin klasorunu acmami istiyorsun?"

    parent = verified_file.parent
    if not parent.is_dir():
        return None, f"{verified_file.name} dosyasinin klasoru bulunamadi."

    return str(parent.resolve()), ""
