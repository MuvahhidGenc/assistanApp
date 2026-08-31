from __future__ import annotations

import re

from hermes.agent.local_intent import LocalIntent, match_local_intent
from hermes.tools.manifest import extract_url_hint

_STEP_SPLIT = re.compile(
    r"\s+(?:ve|sonra|ardindan|ardından|then|and)\s+",
    re.IGNORECASE,
)


def split_command_steps(message: str) -> list[str]:
    """Split a natural-language multi-step PC command into ordered parts."""
    text = (message or "").strip()
    if not text:
        return []
    parts = [part.strip(" .;") for part in _STEP_SPLIT.split(text) if part.strip(" .;")]
    return parts or [text]


def _intent_for_part(part: str) -> LocalIntent | None:
    from hermes.agent.local_intent import guess_local_action

    intent = match_local_intent(part)
    if intent:
        return intent
    return guess_local_action(part)


def plan_local_sequence(message: str) -> list[LocalIntent]:
    """Return ordered local intents for sequential execution."""
    parts = split_command_steps(message)
    if len(parts) <= 1:
        single = _intent_for_part(message)
        return [single] if single else []

    intents: list[LocalIntent] = []
    for part in parts:
        intent = _intent_for_part(part)
        if intent:
            intents.append(intent)
    return intents


def is_multi_step_message(message: str) -> bool:
    return len(split_command_steps(message)) > 1


def has_actionable_sequence(message: str) -> bool:
    """True when we can run at least two local steps without the server."""
    parts = split_command_steps(message)
    if len(parts) <= 1:
        return False
    matched = sum(1 for part in parts if _intent_for_part(part))
    return matched >= 2


def describe_sequence(intents: list[LocalIntent]) -> str:
    lines = [f"{index}. {intent.summary}" for index, intent in enumerate(intents, start=1)]
    return "Sirali plan:\n" + "\n".join(lines)


def extract_navigation_after_app(message: str, app_name: str) -> str | None:
    """If user asked to open app then navigate, return the URL/domain part."""
    lower = message.lower()
    if app_name not in lower:
        return None
    return extract_url_hint(message)
