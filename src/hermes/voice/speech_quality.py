"""Lightweight speech-quality gate before STT text reaches the agent.

Does not rewrite user language — only rejects empty / noise-like transcripts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechQualityDecision:
    accept: bool
    reason: str = "ok"


_NOISE_ONLY = re.compile(
    r"^(?:[.\-…,_!?\s]|um+|uh+|ı+|ee+|aa+|hmm+|hm+|şş+|tss+)+$",
    re.IGNORECASE,
)
_REPEATED_CHAR = re.compile(r"(.)\1{5,}")


def assess_speech_quality(text: str) -> SpeechQualityDecision:
    raw = (text or "").strip()
    if not raw:
        return SpeechQualityDecision(False, "empty")
    letters = sum(1 for ch in raw if ch.isalpha())
    if letters < 2:
        return SpeechQualityDecision(False, "too_short")
    if len(raw) < 3 and letters < 3:
        return SpeechQualityDecision(False, "too_short")
    if _NOISE_ONLY.match(raw):
        return SpeechQualityDecision(False, "noise_only")
    if _REPEATED_CHAR.search(raw) and letters < 6:
        return SpeechQualityDecision(False, "degenerate")
    # Extremely low letter ratio → likely keyboard/TV artifact transcript.
    alnum = sum(1 for ch in raw if ch.isalnum())
    if alnum and letters / max(alnum, 1) < 0.35 and letters < 8:
        return SpeechQualityDecision(False, "low_letter_ratio")
    return SpeechQualityDecision(True, "ok")
