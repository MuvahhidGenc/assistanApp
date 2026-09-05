"""Capability-level plan for screen perception tasks.

Produces an AgentIntent the existing IntentRouter can bind. No tool names.
"""
from __future__ import annotations

from typing import Any

from hermes.agent.application_catalog import WEB_SHORTCUTS, normalize_user_text, resolve_web_url
from hermes.intent.models import AgentIntent, IntentStep
from hermes.screen.reference import extract_reference_features, is_screen_perception_task
from hermes.screen.scroll import parse_scroll_action


def infer_navigate_url(message: str) -> str | None:
    url = resolve_web_url(message)
    if url:
        return url
    features = extract_reference_features(message)
    if not features.wants_navigate:
        lower = normalize_user_text(message or "").casefold()
        if "gir" not in lower and "git" not in lower:
            return None
    lower = normalize_user_text(message or "").casefold()
    best = ""
    href = None
    for name, target in WEB_SHORTCUTS.items():
        if name in lower and len(name) > len(best):
            best = name
            href = target
    return href


def build_screen_perception_intent(message: str, context: Any = None) -> AgentIntent | None:
    text = (message or "").strip()
    if not text or not is_screen_perception_task(text):
        return None

    features = extract_reference_features(text)
    steps: list[IntentStep] = []
    url = infer_navigate_url(text)
    if url:
        steps.append(IntentStep(capability="browser.navigate", inputs={"url": url}))

    steps.append(IntentStep(capability="screen.observe", inputs={}))
    steps.append(IntentStep(capability="screen.resolve", inputs={"reference": text}))
    steps.append(IntentStep(capability="screen.click", inputs={}))
    steps.append(IntentStep(capability="screen.observe", inputs={}))
    if features.wants_report:
        steps.append(IntentStep(capability="screen.read", inputs={}))

    return AgentIntent(
        goal=text,
        plan=tuple(steps),
        required_capabilities=tuple(step.capability for step in steps),
        references={"reference": text},
        expected_outcome="Ekrandaki hedefe tiklandi",
        reported_confidence=0.86,
        mode="task",
    )


def build_scroll_intent(message: str, context: Any = None) -> AgentIntent | None:
    """Deterministic scroll plan with direction/amount from the utterance."""
    action = parse_scroll_action(message, context)
    if action is None:
        return None
    args = action.to_tool_arguments()
    return AgentIntent(
        goal=(message or "").strip(),
        plan=(
            IntentStep(capability="screen.scroll", inputs=args),
            IntentStep(capability="screen.observe", inputs={}),
        ),
        required_capabilities=("screen.scroll", "screen.observe"),
        references=args,
        expected_outcome="Sayfa kaydirildi",
        reported_confidence=0.9,
        mode="task",
    )


def build_catalog_search_intent(message: str, context: Any = None) -> AgentIntent | None:
    """Catalog + topic + find is a search navigation, not homepage OCR picking."""
    del context
    text = (message or "").strip()
    if not text or is_screen_perception_task(text):
        return None
    from hermes.agent.local_intent import match_local_intent
    from hermes.screen.reference import looks_like_screen_reference

    if looks_like_screen_reference(text):
        return None
    local = match_local_intent(text)
    if local is None or local.request.name != "open_url":
        return None
    url = str(local.request.arguments.get("url") or "").strip()
    if not url:
        return None
    return AgentIntent(
        goal=text,
        plan=(IntentStep(capability="browser.navigate", inputs={"url": url}),),
        required_capabilities=("browser.navigate",),
        references={"url": url},
        expected_outcome="Arama sayfasi acildi",
        reported_confidence=0.84,
        mode="task",
    )
