from __future__ import annotations

import re
import unicodedata

DEFAULT_WAKE_WORDS = ("abi", "akhi", "dostum")

STOP_COMMANDS = frozenset({"dur", "iptal", "sus", "stop", "cancel", "kapat"})


def normalize_voice_text(text: str) -> str:
    """Lowercase and strip accents for robust Turkish matching."""
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def is_stop_command(text: str) -> bool:
    normalized = normalize_voice_text(text)
    first = normalized.split()[0] if normalized.split() else normalized
    return first in STOP_COMMANDS or normalized in STOP_COMMANDS


def detect_wake_word(
    text: str,
    wake_words: tuple[str, ...] | list[str] | None = None,
) -> tuple[str | None, str]:
    """
    Return (matched_wake_word, remainder_text).
    Supports leading wake word in the same utterance, e.g. 'abi sistem bilgimi goster'.
    """
    words = tuple(wake_words or DEFAULT_WAKE_WORDS)
    normalized = normalize_voice_text(text)
    if not normalized:
        return (None, "")

    for wake in sorted(words, key=len, reverse=True):
        wake_norm = normalize_voice_text(wake)
        pattern = rf"^(?:hey\s+|selam\s+)?{re.escape(wake_norm)}(?:[\s,.:;-]+|$)(.*)$"
        match = re.match(pattern, normalized)
        if not match:
            continue
        remainder = match.group(1).strip(" ,.;:-")
        return wake, remainder

    fuzzy = (
        ("abi", ("ahbi", "abbey", "api", "aby")),
        ("akhi", ("ahki", "aki")),
        ("dostum", ("dostım", "dost")),
    )
    allowed = {normalize_voice_text(w) for w in words}
    tokens = normalized.split()
    for i, token in enumerate(tokens):
        for canonical, aliases in fuzzy:
            if canonical not in allowed:
                continue
            if token == canonical or token in aliases:
                remainder = " ".join(tokens[i + 1 :]).strip()
                return canonical, remainder
    return None, text.strip()


def contains_wake_word(
    text: str,
    wake_words: tuple[str, ...] | list[str] | None = None,
) -> bool:
    wake, _ = detect_wake_word(text, wake_words)
    return wake is not None
