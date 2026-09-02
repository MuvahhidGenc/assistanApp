from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hermes.context.system_paths import resolve_known_folder

# System / filler tokens — never treated as user folder names.
_RESERVED_NAMES = frozenset(
    {
        "masa",
        "masaüstü",
        "masaustu",
        "masaustune",
        "masaüstüne",
        "masaustunde",
        "masaüstünde",
        "desktop",
        "belgeler",
        "documents",
        "indirilenler",
        "downloads",
        "resimler",
        "pictures",
        "videolar",
        "videos",
        "muzik",
        "müzik",
        "music",
        "yeni",
        "bir",
        "klasor",
        "klasör",
        "folder",
        "klasoru",
        "klasörü",
        "klasorune",
        "klasörüne",
        "klasorun",
        "klasörün",
        "klasorunu",
        "klasörünü",
        "icine",
        "içine",
        "adli",
        "adlı",
        "adinda",
        "adında",
        "diye",
        "olsun",
        "koy",
        "yap",
        "hazirla",
        "hazırla",
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

_CREATE_VERBS = re.compile(
    r"\b(olustur|oluştur|yarat|create|hazirla|hazırla|yap)\b",
    re.IGNORECASE,
)
_FOLDER_WORD = re.compile(r"\bklas(?:o|ö)r(?:u|ü|une|üne|unu|ünü)?\b", re.IGNORECASE)
_OPEN_VERBS = re.compile(
    r"\b(ac|aç|open|gir|geç|gec|baslat|başlat|calistir|çalıştır)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CreateFolderGoal:
    name: str
    location: Path


@dataclass(frozen=True)
class OpenFolderGoal:
    path: Path
    display_name: str


def _clean_name(raw: str) -> str | None:
    name = (raw or "").strip(" .'\"")
    if not name:
        return None
    if name.casefold() in _RESERVED_NAMES:
        return None
    return name


def extract_location_path(text: str) -> Path:
    """Resolve explicit system location from message; default Desktop."""
    known = resolve_known_folder(text or "")
    if known is not None:
        return known
    lower = (text or "").casefold()
    if re.search(r"\bmasa[uü]st", lower) or "desktop" in lower:
        return Path.home() / "Desktop"
    return Path.home() / "Desktop"


def parse_create_folder_goal(text: str) -> CreateFolderGoal | None:
    """
    Parse create-folder NL into folder name + parent location.

    Location tokens (Desktop/Masaüstü/Belgeler/…) are never folder names.
    """
    normalized = (text or "").strip()
    if not normalized:
        return None
    if not _FOLDER_WORD.search(normalized):
        return None
    if not _CREATE_VERBS.search(normalized):
        return None

    location = extract_location_path(normalized)
    name: str | None = None

    name_patterns: tuple[re.Pattern[str], ...] = (
        re.compile(r"ad[ıi]na\s+([\w\d_.-]+)\s+koy", re.IGNORECASE),
        re.compile(r"ad[ıi]\s+([\w\d_.-]+)\s+olsun", re.IGNORECASE),
        re.compile(
            r"masa[uü]st(?:u|ü)(?:ne|de)?\s+([\w\d_.-]+)\s+adl[ıi]\s+(?:bir\s+)?klas",
            re.IGNORECASE,
        ),
        re.compile(
            r"([\w\d_.-]+)\s+adl[ıi]\s+(?:bir\s+)?klas",
            re.IGNORECASE,
        ),
        re.compile(
            r"([\w\d_.-]+)\s+ad[ıi]nda\s+(?:bir\s+)?klas",
            re.IGNORECASE,
        ),
        re.compile(
            r"([\w\d_.-]+)\s+(?:diye|adinda|adında)\s+(?:bir\s+)?klas",
            re.IGNORECASE,
        ),
        re.compile(
            r"masa[uü]st(?:u|ü)(?:nde|de|ne)?\s+([\w\d_.-]+)\s+(?:klas(?:o|ö)r(?:u|ü)?\s+)?"
            r"(?:olustur|oluştur|yarat|hazirla|hazırla|yap)",
            re.IGNORECASE,
        ),
        re.compile(
            r"([\w\d_.-]+)\s+klas(?:o|ö)r(?:u|ü)?\s+(?:olustur|oluştur|yarat|hazirla|hazırla|yap|ac|aç)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:olustur|oluştur|yarat|hazirla|hazırla|yap).*?ad[ıi]na\s+([\w\d_.-]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"masa[uü]st(?:u|ü)(?:ne|de)?\s+(?:yeni\s+)?(?:bir\s+)?klas(?:o|ö)r(?:u|ü)?\s+"
            r"(?:olustur|oluştur|yarat|hazirla|hazırla).*?([\w\d_.-]+)",
            re.IGNORECASE,
        ),
    )

    for pattern in name_patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        candidate = _clean_name(match.group(1))
        if candidate:
            name = candidate
            break

    if not name:
        loose = re.search(
            r"(?:masa[uü]st(?:u|ü)(?:nde|de)?\s+)?([\w\d_.-]+)\s+klas(?:o|ö)r(?:u|ü)?\s+"
            r"(?:olustur|oluştur|yarat|create|hazirla|hazırla|yap)",
            normalized,
            re.IGNORECASE,
        )
        if loose:
            name = _clean_name(loose.group(1))

    if not name:
        return None

    return CreateFolderGoal(name=name, location=location)


def parse_open_folder_goal(text: str) -> OpenFolderGoal | None:
    """Resolve explicit named folder open target from message (ignores context)."""
    normalized = (text or "").strip()
    if not normalized:
        return None
    if not _FOLDER_WORD.search(normalized):
        return None
    if not _OPEN_VERBS.search(normalized):
        return None
    if _CREATE_VERBS.search(normalized):
        return None

    location = extract_location_path(normalized)

    patterns: tuple[re.Pattern[str], ...] = (
        re.compile(
            r"(?:masa[uü]st(?:u|ü)(?:nde|de)?\s+)?([\w\d_.-]+)\s+klas(?:o|ö)r(?:u|ü|nu|nü|unu|ünü)?",
            re.IGNORECASE,
        ),
        re.compile(r"\b([\w\d_.-]+)\s+klas(?:o|ö)r\b", re.IGNORECASE),
    )

    for pattern in patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        name = _clean_name(match.group(1))
        if not name:
            continue
        path = (location / name).resolve()
        return OpenFolderGoal(path=path, display_name=name)

    known = resolve_known_folder(normalized)
    if known is not None:
        return OpenFolderGoal(path=known.resolve(), display_name=known.name)

    return None


def extract_create_folder_target(text: str) -> str | None:
    goal = parse_create_folder_goal(text)
    return goal.name if goal else None


def resolve_create_folder_path(text: str) -> str | None:
    goal = parse_create_folder_goal(text)
    if not goal:
        return None
    return str((goal.location / goal.name).resolve())


def extract_named_folder_name(text: str) -> str | None:
    """Extract explicit folder name from reference phrases like 'Hermes klasörünün içine'."""
    normalized = (text or "").strip()
    if not normalized:
        return None

    patterns: tuple[re.Pattern[str], ...] = (
        re.compile(
            r"([\w\d_.-]+)\s+klas(?:ö|o)r(?:u|ü)?n(?:un|ün|de|da|e|a|u|ü)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"([\w\d_.-]+)\s+klas(?:ö|o)r(?:u|ü|unun|ünün|unde|ünde|une|üne)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:masa[uü]st(?:u|ü)(?:nde|de)?\s+)?([\w\d_.-]+)\s+klas(?:ö|o)r\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"([\w\d_.-]+)\s+klas(?:ö|o)r(?:u|ü)?\b",
            re.IGNORECASE,
        ),
    )

    for pattern in patterns:
        match = pattern.search(normalized)
        if not match:
            continue
        name = _clean_name(match.group(1))
        if name:
            return name
    return None
