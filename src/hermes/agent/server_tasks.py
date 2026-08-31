from __future__ import annotations

import re
import unicodedata


def _normalize(text: str) -> str:
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def should_defer_to_server(text: str) -> bool:
    """Return True when the request should go to Hermes server instead of local fast path."""
    norm = _normalize(text)

    defer_patterns = (
        r"\bindir\b.*\bkur\b",
        r"\bkur\b.*\bindir\b",
        r"\bdownload\b.*\binstall\b",
        r"\bprogram\s+indir\b",
        r"\bdosya\s+indir\b",
    )
    for pattern in defer_patterns:
        if re.search(pattern, norm):
            return True

    local_patterns = (
        r"\byoutube\b",
        r"\bchrome\b.*\b(ac|git)\b",
        r"\bdns\b",
        r"\bekran\b",
        r"\bscreenshot\b",
        r"\bklasor\b",
        r"\bvideo\b.*\b(ac|izle|ara)\b",
    )
    for pattern in local_patterns:
        if re.search(pattern, norm):
            return False

    return False
