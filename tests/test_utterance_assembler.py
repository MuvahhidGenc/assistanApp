"""Utterance assembly — STT finals must not become separate agent turns."""
from __future__ import annotations

from hermes.voice.utterance import UtteranceAssembler, looks_incomplete_utterance


def test_incomplete_short_fragments() -> None:
    assert looks_incomplete_utterance("YouTube'u")
    assert looks_incomplete_utterance("not defter")
    assert looks_incomplete_utterance("sayfayı")


def test_complete_local_commands() -> None:
    assert not looks_incomplete_utterance("Not defterini aç")
    assert not looks_incomplete_utterance("YouTube'u aç")
    assert not looks_incomplete_utterance("Chrome'u aç")


def test_assembler_merges_then_dispatches() -> None:
    buf = UtteranceAssembler(hold_seconds=2.0)
    assert buf.push("YouTube'u") is None
    assert buf.pending == "YouTube'u"
    final = buf.push("aç")
    assert final == "YouTube'u aç"


def test_assembler_drops_incomplete_on_flush() -> None:
    buf = UtteranceAssembler()
    buf.push("not defter")
    assert buf.flush() is None


def test_three_token_wait_is_complete_enough() -> None:
    # Must not treat normal short phrases as cut-offs.
    assert not looks_incomplete_utterance("Bir dakika bekle")
