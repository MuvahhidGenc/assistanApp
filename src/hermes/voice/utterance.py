"""Assemble STT finals into one user utterance before the agent runs.

speech_recognition / Google often end mid-phrase on Turkish (e.g. "YouTube'u",
"not defter"). Those fragments must not become separate agent turns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class UtteranceAssembler:
    """Hold incomplete STT finals and merge consecutive chunks."""

    hold_seconds: float = 2.0
    max_parts: int = 6
    _parts: list[str] = field(default_factory=list)
    _last_push_at: float = 0.0

    def clear(self) -> None:
        self._parts.clear()
        self._last_push_at = 0.0

    @property
    def pending(self) -> str:
        return " ".join(self._parts).strip()

    def push(self, text: str) -> str | None:
        """Add a STT final. Return a dispatchable utterance, or None while holding."""
        chunk = " ".join(str(text or "").split()).strip()
        if not chunk:
            return None
        now = time.monotonic()
        if self._parts and (now - self._last_push_at) > max(self.hold_seconds * 2.5, 5.0):
            # Stale buffer — drop rather than glue unrelated turns.
            self.clear()
        self._parts.append(chunk)
        self._last_push_at = now
        if len(self._parts) > self.max_parts:
            return self.flush(force=True)
        combined = self.pending
        if looks_incomplete_utterance(combined):
            return None
        self.clear()
        return combined

    def flush(self, *, force: bool = False) -> str | None:
        """Release buffered text after silence. Incomplete fragments are dropped unless force."""
        combined = self.pending
        self.clear()
        if not combined:
            return None
        if force:
            return combined
        if looks_incomplete_utterance(combined):
            return None
        return combined

    def should_keep_listening(self) -> bool:
        return bool(self._parts)


def looks_incomplete_utterance(text: str) -> bool:
    """Structural incompleteness — not a command keyword table.

    Complete when an existing local matcher already binds a tool. Otherwise short
    or conjunction-tailed fragments stay buffered.
    """
    raw = " ".join((text or "").split()).strip()
    if not raw:
        return True
    # Already a bound local command → final.
    try:
        from hermes.agent.local_intent import guess_local_action, match_local_intent

        if match_local_intent(raw) is not None:
            return False
        if guess_local_action(raw) is not None:
            return False
    except Exception:
        pass

    tokens = raw.split()
    last = tokens[-1].casefold().strip(".,;:!?…\"'")
    # Discourse / conjunction tails expect more speech.
    if last in {
        "ve",
        "ile",
        "sonra",
        "simdi",
        "şimdi",
        "bir",
        "o",
        "şu",
        "bu",
        "ama",
        "yani",
    }:
        return True
    # 1–2 token fragments without a bound tool are almost always cut-offs
    # ("YouTube'u", "not defter", "sayfayı").
    if len(tokens) <= 2:
        return True
    return False
