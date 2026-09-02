"""Implicit file content generation when the user omits literal write content."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_COUNT_WORDS = {
    "bir": 1,
    "1": 1,
    "iki": 2,
    "2": 2,
    "uc": 3,
    "üç": 3,
    "3": 3,
    "dort": 4,
    "dört": 4,
    "4": 4,
}

_MULTI_TYPE = re.compile(
    r"\b(?:"
    r"(?:uc|üç|3|iki|2|bir|1|farkli|farklı|cesit|çeşit|tur|tür)\s+(?:farkli|farklı|cesit|çeşit|tur|tür)?\s*dosya|"
    r"farkli\s+tur(?:de|da)?\s+dosya|"
    r"farklı\s+tür(?:de|da)?\s+dosya|"
    r"birkac\s+dosya|birkaç\s+dosya|"
    r"gerekli.*dosya"
    r")\b",
    re.IGNORECASE,
)

_AUDIT_OR_SUMMARY = re.compile(
    r"\b(?:"
    r"kontrol|dogrula|doğrula|"
    r"ne\s+olusturd|ne\s+oluşturd|"
    r"ne\s+yapt|"
    r"bana\s+soyle|bana\s+söyle|"
    r"rapor(?:la|lama)?"
    r")\b",
    re.IGNORECASE,
)

_FILE_CREATE = re.compile(
    r"\b(?:dosya(?:y[ıi])?\s+(?:olustur|oluştur|yarat|koy)|"
    r"(?:olustur|oluştur|yarat|koy).*(?:dosya|txt|csv|md))\b",
    re.IGNORECASE,
)

_TYPE_SPECS: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (re.compile(r"\btxt\b|metin\s+dosya", re.IGNORECASE), ".txt", "ornek", "Ornek metin"),
    (re.compile(r"\bmd\b|markdown", re.IGNORECASE), ".md", "notlar", "# Notlar"),
    (re.compile(r"\bcsv\b", re.IGNORECASE), ".csv", "veri", "sutun1,sutun2\na,b"),
    (re.compile(r"\bjson\b", re.IGNORECASE), ".json", "data", '{"ornek": true}'),
)

_DEFAULT_TYPES: tuple[tuple[str, str, str], ...] = (
    (".txt", "ornek", "Ornek metin"),
    (".md", "notlar", "# Notlar"),
    (".csv", "veri", "sutun1,sutun2\na,b"),
)


@dataclass(frozen=True)
class ImplicitFileSpec:
    path: Path
    content: str
    extension: str


def requires_implicit_content_generation(text: str) -> bool:
    """
    True when file creation is part of the goal but the user did not specify literal content.

    Does not apply when explicit content was given (backticks, quotes, içine X yaz).
    """
    from hermes.mission.write_content import extract_literal_write_content, requires_tool_output_dependency

    normalized = (text or "").strip()
    if not normalized:
        return False
    if extract_literal_write_content(normalized).literal_content is not None:
        return False
    if requires_tool_output_dependency(normalized):
        return False
    if not _FILE_CREATE.search(normalized):
        return False
    if _MULTI_TYPE.search(normalized):
        return True
    if _AUDIT_OR_SUMMARY.search(normalized) and re.search(
        r"\bklas(?:o|ö)r|dosya", normalized, re.IGNORECASE
    ):
        return True
    return bool(
        re.search(r"\b(?:ve|sonra)\b", normalized, re.IGNORECASE)
        and _FILE_CREATE.search(normalized)
        and re.search(r"\bklas(?:o|ö)r", normalized, re.IGNORECASE)
    )


def infer_file_type_count(text: str) -> int:
    lower = (text or "").casefold()
    match = re.search(
        r"\b(bir|1|iki|2|uc|üç|3|dort|dört|4)\s+(?:farkli|farklı|cesit|çeşit|tur|tür)?\s*(?:dosya|tur|tür)",
        lower,
    )
    if match:
        return _COUNT_WORDS.get(match.group(1), 3)

    detected = _detect_requested_type_specs(text)
    if detected:
        return len(detected)

    if re.search(r"birkac|birkaç|gerekli", lower):
        return 3
    return 3


def _detect_requested_type_specs(text: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    seen_ext: set[str] = set()
    for pattern, ext, stem, content in _TYPE_SPECS:
        if pattern.search(text or "") and ext not in seen_ext:
            seen_ext.add(ext)
            found.append((ext, stem, content))
    return found


def generate_implicit_file_specs(user_goal: str, folder: Path) -> list[ImplicitFileSpec]:
    """Build file paths and minimal content for implicit multi-type file creation goals."""
    count = max(1, infer_file_type_count(user_goal))
    type_specs = _detect_requested_type_specs(user_goal)
    if not type_specs:
        type_specs = list(_DEFAULT_TYPES[:count])
    else:
        while len(type_specs) < count:
            for ext, stem, content in _DEFAULT_TYPES:
                if ext not in {item[0] for item in type_specs}:
                    type_specs.append((ext, stem, content))
                    break
            else:
                break
        type_specs = type_specs[:count]

    specs: list[ImplicitFileSpec] = []
    used_stems: set[str] = set()
    for index, (ext, stem, content) in enumerate(type_specs):
        name = stem
        if name in used_stems:
            name = f"{stem}{index + 1}"
        used_stems.add(name)
        path = folder / f"{name}{ext}"
        specs.append(ImplicitFileSpec(path=path, content=content, extension=ext))
    return specs
