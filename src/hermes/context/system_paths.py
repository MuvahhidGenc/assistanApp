from __future__ import annotations

import re
import sys
import uuid
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

_WINDOWS_KNOWN_FOLDER_IDS: dict[str, str] = {
    "Desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "Documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "Downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "Pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "Music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "Videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
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


def current_user_known_folders() -> dict[str, str]:
    """Observe canonical known-folder paths for the current OS user."""
    if sys.platform != "win32":
        return {
            name.casefold(): str((Path.home() / name).resolve())
            for name in _WINDOWS_KNOWN_FOLDER_IDS
        }

    observed: dict[str, str] = {}
    for name, folder_id in _WINDOWS_KNOWN_FOLDER_IDS.items():
        path = _windows_known_folder_path(folder_id)
        if path is not None:
            observed[name.casefold()] = str(path)
    return observed


def current_user_desktop_path() -> Path | None:
    """Return the OS-reported Desktop for the current user, if observable."""
    value = current_user_known_folders().get("desktop")
    return Path(value) if value else None


def _windows_known_folder_path(folder_id: str) -> Path | None:
    import ctypes
    from ctypes import wintypes

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    value = uuid.UUID(folder_id)
    fields = value.fields
    data4 = (ctypes.c_ubyte * 8)(
        fields[3],
        fields[4],
        *fields[5].to_bytes(6, byteorder="big"),
    )
    guid = _Guid(fields[0], fields[1], fields[2], data4)
    output = ctypes.c_wchar_p()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    result = shell32.SHGetKnownFolderPath(
        ctypes.byref(guid),
        0,
        None,
        ctypes.byref(output),
    )
    if result != 0 or not output.value:
        return None
    try:
        return Path(output.value).resolve()
    finally:
        ole32.CoTaskMemFree(ctypes.cast(output, ctypes.c_void_p))


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
