from __future__ import annotations

import re
from pathlib import Path

_FOLDER_ALIASES: dict[str, str] = {
    "indirilenler": "Downloads",
    "indirilen": "Downloads",
    "downloads": "Downloads",
    "download": "Downloads",
    "belgeler": "Documents",
    "documents": "Documents",
    "document": "Documents",
    "masaustu": "Desktop",
    "masaüstü": "Desktop",
    "masaüstüne": "Desktop",
    "masaustune": "Desktop",
    "desktop": "Desktop",
    "resimler": "Pictures",
    "pictures": "Pictures",
    "videolar": "Videos",
    "videos": "Videos",
    "muzik": "Music",
    "müzik": "Music",
    "music": "Music",
}

# Turkish locative/ablative/plural suffixes attached to folder names.
_TURKISH_FOLDER_SUFFIX = (
    r"(?:"
    r"deki|daki|denki|dan(?:ki)?|"
    r"de|da|den|dan|"
    r"ye|ya|e|a|"
    r"nin|nın|nun|nün|"
    r"yi|yı|yu|yü|"
    r"ler|lar|"
    r"klas(?:o|ö)r(?:u|ü|une|üne|de|da|den|dan|deki|daki)?"
    r")?"
)

_FILE_TYPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bpd(?:f|'f|'ler|'leri|fler|fleri|flerden|flerde|fleri)\b", re.IGNORECASE), "*.pdf"),
    (re.compile(r"\bpdf(?:'|’)?(?:ler(?:i|den|de)?|leri|lerden|lerde)?\b", re.IGNORECASE), "*.pdf"),
    (re.compile(r"\bpdf\s+dosya(?:lar(?:ı|i|ını|ini|ından|inden|ında|inde))?\b", re.IGNORECASE), "*.pdf"),
)


def _alias_pattern(alias: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:^|[\W_]){re.escape(alias)}{_TURKISH_FOLDER_SUFFIX}(?:[\W_]|$)",
        re.IGNORECASE,
    )


def _normalize_folder_match_text(text: str) -> str:
    """Turkish-safe fold for folder alias matching.

    Python casefold maps dotted capital I (İ) to i + combining dot (U+0307), which
    no longer matches aliases like ``indirilenler``. Strip the combining mark.
    """
    folded = (text or "").casefold().replace("\u0307", "")
    return folded.replace("ı", "i")


def detect_known_folder_alias(message: str) -> str | None:
    """Return canonical folder name (Downloads, Desktop, …) if message mentions a system location."""
    raw = message or ""
    if not raw.strip():
        return None
    candidates: list[str] = []
    for candidate in (_normalize_folder_match_text(raw), raw.casefold(), raw):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        for alias, folder_name in _FOLDER_ALIASES.items():
            if _alias_pattern(alias).search(candidate):
                return folder_name
    return None


def resolve_known_folder(message: str) -> Path | None:
    """Resolve well-known Windows user folders from natural language."""
    folder_name = detect_known_folder_alias(message)
    if folder_name is None:
        return None
    return (Path.home() / folder_name).resolve()


def extract_file_type_pattern(message: str) -> str | None:
    """Map natural-language file type mentions to a glob pattern (e.g. *.pdf)."""
    text = message or ""
    for pattern, glob in _FILE_TYPE_PATTERNS:
        if pattern.search(text):
            return glob
    lower = text.casefold()
    if re.search(r"\bpd?f\b", lower):
        return "*.pdf"
    return None


def folder_display_name(path: Path) -> str:
    name = path.name.casefold()
    labels = {
        "downloads": "Indirilenler",
        "documents": "Belgeler",
        "desktop": "Masaustu",
        "pictures": "Resimler",
        "videos": "Videolar",
        "music": "Muzik",
    }
    return labels.get(name, path.name)
