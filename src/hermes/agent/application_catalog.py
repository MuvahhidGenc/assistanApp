from __future__ import annotations

import re
import unicodedata

_OPEN_VERBS = re.compile(
    r"\b(ac|aç|open|baslat|başlat|calistir|çalıştır|run|start|git|geç|gec)\b",
    re.IGNORECASE,
)

# Web shortcuts: "Google'ı aç" → open_url, not file context
WEB_SHORTCUTS: dict[str, str] = {
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "facebook": "https://www.facebook.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "instagram": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "github": "https://github.com",
    "bing": "https://www.bing.com",
}

# Canonical app id -> natural language aliases (Turkish + English)
APPLICATION_ALIASES: dict[str, tuple[str, ...]] = {
    "chrome": (
        "chrome",
        "google chrome",
        "google chrome'u",
        "chrome'u",
        "chrome u",
        "krom",
    ),
    "edge": ("edge", "microsoft edge", "edge'i"),
    "firefox": ("firefox", "firefox'u", "mozilla firefox"),
    "notepad": ("notepad", "not defteri", "notepad'i", "not defterini"),
    "explorer": ("explorer", "gezgin", "dosya gezgini", "file explorer"),
    "calc": ("calc", "hesap makinesi", "hesap makinesini", "calculator"),
    "paint": ("paint", "mspaint", "boya", "paint'i"),
    "spotify": ("spotify", "spotify'i"),
    "discord": ("discord", "discord'u"),
    "cmd": ("cmd", "komut istemi", "command prompt"),
    "powershell": ("powershell", "power shell"),
}


def normalize_user_text(text: str) -> str:
    raw = unicodedata.normalize("NFKC", text or "")
    return (
        raw.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("'", "'")
        .replace("'", "'")
    )


def resolve_web_url(text: str) -> str | None:
    """Map 'Google'ı aç' style phrases to URLs."""
    normalized = normalize_user_text(text)
    lower = normalized.casefold()
    if not _OPEN_VERBS.search(lower):
        return None
    if re.search(r"https?://|www\.|\.com\b|\.net\b|\.org\b", lower):
        return None
    for aliases in APPLICATION_ALIASES.values():
        for alias in aliases:
            alias_lower = alias.casefold()
            if len(alias_lower) >= 5 and alias_lower in lower:
                return None
    best_name: str | None = None
    best_len = 0
    for name in WEB_SHORTCUTS:
        pattern = rf"\b{re.escape(name)}(?:['']?(?:y[ıi]|u|ü|yu|yü|na|ne|ya|ye|a|ı|i))?\b"
        if re.search(pattern, lower) and len(name) > best_len:
            best_name = name
            best_len = len(name)
    if best_name:
        return WEB_SHORTCUTS[best_name]
    return None


def resolve_application(text: str) -> str | None:
    """Map natural language to canonical open_app id."""
    normalized = normalize_user_text(text)
    lower = normalized.casefold()
    if not _OPEN_VERBS.search(lower):
        return None

    if resolve_web_url(text):
        return None

    best_id: str | None = None
    best_len = 0
    for app_id, aliases in APPLICATION_ALIASES.items():
        for alias in aliases:
            alias_lower = alias.casefold()
            if alias_lower in lower and len(alias_lower) > best_len:
                best_id = app_id
                best_len = len(alias_lower)
    if best_id:
        return best_id

    quoted = re.search(r"['\"]([^'\"]{2,40})['\"]", normalized)
    if quoted:
        candidate = quoted.group(1).strip().casefold()
        if candidate in WEB_SHORTCUTS:
            return None
        return candidate
    return None


def is_open_application_message(text: str) -> bool:
    return resolve_application(text) is not None


def is_web_or_app_open_message(text: str) -> bool:
    """True when message is an explicit app or web shortcut open — not a file open."""
    return resolve_web_url(text) is not None or resolve_application(text) is not None


def has_explicit_filename(text: str) -> bool:
    return bool(re.search(r"\b[\w\d_.-]+\.(?:txt|md|docx|pdf|xlsx|csv)\b", text, re.IGNORECASE))
