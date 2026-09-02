from __future__ import annotations

import re

from hermes.mission.write_content import extract_literal_write_content


def extract_content_modification(text: str) -> str | None:
    normalized = (text or "").strip()
    trailing = re.search(
        r"ve\s+(?:icine|içine)\s+(.+?)\s+yaz\s*\.?\s*$",
        normalized,
        re.IGNORECASE,
    )
    if trailing:
        content = trailing.group(1).strip(" .")
        if content and not _looks_like_path(content):
            return content

    tail_write = re.search(
        r"(?:icine|içine)\s+(.+?)\s+yaz\s*\.?\s*$",
        normalized,
        re.IGNORECASE,
    )
    if tail_write:
        content = tail_write.group(1).strip(" .")
        if content and not _looks_like_path(content):
            return content

    split_parts = re.split(r"\s+ve\s+(?:icine|içine)\s+", normalized, flags=re.IGNORECASE)
    if len(split_parts) >= 2:
        maybe = re.search(r"^(.+?)\s+yaz\s*\.?\s*$", split_parts[-1], re.IGNORECASE)
        if maybe:
            content = maybe.group(1).strip(" .")
            if content and not _looks_like_path(content):
                return content

    inline_matches = list(
        re.finditer(r"(?:icine|içine)\s+(.+?)\s+yaz", normalized, re.IGNORECASE)
    )
    for match in reversed(inline_matches):
        content = match.group(1).strip(" .")
        if content and not _looks_like_path(content):
            return content

    patterns = (
        r"icerigini\s+sadece\s+(.+?)\s+yap",
        r"içeriğini\s+sadece\s+(.+?)\s+yap",
        r"icerigini\s+(.+?)\s+olarak\s+(?:değiştir|degistir|yap)",
        r"içeriğini\s+(.+?)\s+olarak\s+(?:değiştir|degistir|yap)",
        r"icerigini\s+(.+?)\s+yap",
        r"içeriğini\s+(.+?)\s+yap",
        r"degistir\s+ve\s+sadece\s+(.+?)\s+yaz",
        r"değiştir\s+ve\s+sadece\s+(.+?)\s+yaz",
        r"dosyasini\s+degistir\s+ve\s+sadece\s+(.+?)\s+yaz",
        r"\"([^\"]+)\"\s+yaz",
        r"\"([^\"]+)\"\s+Yaz",
        r"'([^']+)'\s+yaz",
        r"sadece\s+(.+?)\s+yaz",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        content = match.group(1).strip(" .")
        if content and not _looks_like_path(content):
            return content

    hint = extract_literal_write_content(text)
    if hint.literal_content is not None and hint.literal_source in {"backticks", "quotes"}:
        return hint.literal_content
    return None


def _looks_like_path(text: str) -> bool:
    return bool(re.search(r"[\\/]|\.txt\b|\.docx\b", text, re.IGNORECASE))
