from __future__ import annotations

import re


def looks_like_missing_tools(text: str) -> bool:
    norm = text.lower()
    patterns = (
        "local tool",
        "yerel arac",
        "don't have access to local",
        "no local tools",
        "cannot access local",
    )
    return any(p in norm for p in patterns)


def brief_spoken_reply(text: str) -> str:
    """Return a short phrase suitable for TTS."""
    stripped = text.strip()
    if not stripped:
        return ""

    lower = stripped.lower()
    if "dns" in lower and ("guncellendi" in lower or "8.8.8.8" in lower or "ayar" in lower):
        return "Tamam, DNS ayarland\u0131."

    if lower.startswith("ekranda gorunen yazi") or "ekranda gorunen" in lower:
        return "Ekrana baktım abi, detaylar sohbette."

    if stripped.startswith("Anladım.") or stripped.startswith("Anladim."):
        return "Anladım"

    if len(stripped) <= 80 and "\n" not in stripped:
        return stripped.rstrip(".")

    first_line = stripped.split("\n", 1)[0].strip()
    if len(first_line) <= 80:
        return first_line.rstrip(".")

    return first_line[:76].rstrip() + "..."


def spoken_quick_ack(user_text: str) -> str:
    norm = user_text.lower()
    if "libreoffice" in norm:
        return "LibreOffice kurulumu baslatiliyor abi."
    if "chrome" in norm and "kur" in norm:
        return "Chrome kurulumu baslatiliyor abi."
    if re.search(r"\bkur\b", norm):
        return "Kurulum baslatiliyor abi."
    return "Tamam abi, hallediyorum."
