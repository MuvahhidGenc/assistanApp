from __future__ import annotations

import re
import unicodedata

from hermes.agent.local_intent import LocalIntent, match_local_intent
from hermes.tools.manifest import LocalToolRequest, extract_url_hint


def _normalize(text: str) -> str:
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def split_command_steps(text: str) -> list[str]:
    """Split multi-step Turkish commands on 've' conjunction."""
    norm = _normalize(text)
    parts = re.split(r"\s+ve\s+", norm)
    return [part.strip() for part in parts if part.strip()]


def _match_step(step: str, full_text: str) -> LocalIntent | None:
    intent = match_local_intent(step)
    if intent:
        return intent

    norm = _normalize(step)
    if re.search(r"\bchrome\b", norm) and re.search(r"\b(ac|baslat)\b", norm):
        return LocalIntent(LocalToolRequest(name="open_app", arguments={"app": "chrome"}))

    url_hint = extract_url_hint(full_text) or extract_url_hint(step)
    if url_hint and re.search(r"\b(git|ac|yukle)\b", norm):
        return LocalIntent(LocalToolRequest(name="open_url", arguments={"url": url_hint}))

    if re.search(r"\b(klasor|folder)\b", norm) and re.search(r"\b(olustur|yarat)\b", norm):
        return match_local_intent(step + " klasor olustur")

    return None


def plan_local_sequence(text: str) -> list[LocalIntent]:
    steps = split_command_steps(text)
    if len(steps) <= 1:
        single = match_local_intent(text)
        return [single] if single else []

    intents: list[LocalIntent] = []
    for step in steps:
        intent = _match_step(step, text)
        if intent:
            intents.append(intent)
    return intents


def has_actionable_sequence(text: str) -> bool:
    steps = split_command_steps(text)
    if len(steps) < 2:
        return False
    matched = sum(1 for step in steps if _match_step(step, text) is not None)
    return matched >= 2
