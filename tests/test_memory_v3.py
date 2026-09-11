"""V3 Memory tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes.memory import (
    EpisodicMemory,
    LongTermMemory,
    SessionMemory,
)


# ---------------------------------------------------------------------------
# Session memory
# ---------------------------------------------------------------------------


def test_session_memory_records_turns_in_order():
    memory = SessionMemory()
    memory.record("user", "hi")
    memory.record("assistant", "hello")
    turns = memory.turns
    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert [turn.content for turn in turns] == ["hi", "hello"]


def test_session_memory_persists_to_disk_and_reloads(tmp_path: Path):
    path = tmp_path / "session.json"
    memory = SessionMemory(path=path)
    memory.record("user", "hi")
    memory.record("assistant", "hello")

    reloaded = SessionMemory(path=path)
    assert [turn.content for turn in reloaded.turns] == ["hi", "hello"]


def test_session_memory_last_returns_recent_turns():
    memory = SessionMemory()
    for role, content in [("user", "a"), ("assistant", "b"), ("user", "c")]:
        memory.record(role, content)
    assert [turn.content for turn in memory.last(2)] == ["b", "c"]


def test_session_memory_caps_length():
    memory = SessionMemory(max_turns=3)
    for index in range(5):
        memory.record("user", f"msg {index}")
    assert [turn.content for turn in memory.turns] == ["msg 2", "msg 3", "msg 4"]


def test_session_memory_clear_empties_store():
    memory = SessionMemory()
    memory.record("user", "hi")
    memory.clear()
    assert memory.turns == ()


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------


def test_episodic_memory_records_past_tasks():
    memory = EpisodicMemory()
    record = memory.record(
        summary="installed python 3.11",
        outcome="completed",
        capabilities=("application.install",),
        tags=("setup",),
    )
    assert record.outcome == "completed"
    assert memory.records[0].summary == "installed python 3.11"


def test_episodic_memory_filters_by_outcome_and_capability():
    memory = EpisodicMemory()
    memory.record(summary="ok", outcome="completed", capabilities=("filesystem.write",))
    memory.record(summary="nope", outcome="failed", capabilities=("filesystem.write",))
    completed = memory.query(outcome="completed")
    assert len(completed) == 1
    writes = memory.query(capability="filesystem.write")
    assert len(writes) == 2


def test_episodic_memory_scrubs_secrets_in_summaries():
    memory = EpisodicMemory()
    memory.record(
        summary="API_KEY=abcd1234 was leaked",
        outcome="failed",
    )
    assert "[REDACTED]" in memory.records[0].summary


def test_episodic_memory_persists_to_disk(tmp_path: Path):
    path = tmp_path / "episodic.json"
    memory = EpisodicMemory(path=path)
    memory.record(summary="x", outcome="completed")
    reloaded = EpisodicMemory(path=path)
    assert len(reloaded.records) == 1


# ---------------------------------------------------------------------------
# Long-term memory
# ---------------------------------------------------------------------------


def test_long_term_memory_remembers_and_recalls():
    memory = LongTermMemory()
    memory.remember("language", "tr-TR", provenance="user")
    fact = memory.recall("language")
    assert fact is not None
    assert fact.value == "tr-TR"


def test_long_term_memory_refuses_secret_keys():
    memory = LongTermMemory()
    with pytest.raises(ValueError):
        memory.remember("password", "hunter2")
    with pytest.raises(ValueError):
        memory.remember("API_KEY", "sk-1234")


def test_long_term_memory_scrubs_secret_like_values():
    memory = LongTermMemory()
    memory.remember("note", "the token is sk-abcdefghijklmnop1234")
    fact = memory.recall("note")
    assert fact is not None
    assert "[REDACTED]" in fact.value


def test_long_term_memory_expires_facts():
    memory = LongTermMemory()
    memory.remember("temp", "value", expires_at="2000-01-01T00:00:00+00:00")
    assert memory.recall("temp") is None


def test_long_term_memory_forget_returns_whether_it_existed():
    memory = LongTermMemory()
    memory.remember("k", "v")
    assert memory.forget("k") is True
    assert memory.forget("k") is False


def test_long_term_memory_persists_to_disk(tmp_path: Path):
    path = tmp_path / "long_term.json"
    memory = LongTermMemory(path=path)
    memory.remember("user_name", "Ömer")
    reloaded = LongTermMemory(path=path)
    fact = reloaded.recall("user_name")
    assert fact is not None
    assert fact.value == "Ömer"


# ---------------------------------------------------------------------------
# Memory layers never decide anything
# ---------------------------------------------------------------------------


def test_memory_layers_have_no_routing_methods():
    for instance in (
        SessionMemory(),
        EpisodicMemory(),
        LongTermMemory(),
    ):
        forbidden = [
            "route", "decide", "classify", "match", "dispatch", "plan",
            "interpret", "understand", "choose_intent", "score_message",
        ]
        for method in forbidden:
            assert not hasattr(instance, method)


def test_memory_does_not_import_security_or_orchestrator():
    import hermes.memory as pkg

    modules = [pkg.session.__file__, pkg.episodic.__file__, pkg.long_term.__file__]
    forbidden = ("PolicyEngine", "ApprovalManager", "agent.orchestrator", "intent.")
    for path in modules:
        text = open(path, encoding="utf-8").read()
        for token in forbidden:
            assert token not in text

def test_session_memory_scrubs_secrets_at_record_time(tmp_path: Path):
    """Pasting a password/api-key into the session must not persist it
    in plain text on disk. The runtime scrubs the value before the
    SessionTurn is appended.
    """
    from hermes.memory import SessionMemory, _scrub_session_text

    # Direct scrub
    assert "hunter2" not in _scrub_session_text("password=hunter2")
    assert "ghp_..." not in _scrub_session_text("ghp_1234567890abcdef1234")
    # Through SessionMemory
    sm = SessionMemory(path=tmp_path / "session.jsonl", max_turns=10)
    sm.record("user", "my token is ghp_1234567890abcdef1234")
    on_disk = (tmp_path / "session.jsonl").read_text(encoding="utf-8")
    assert "ghp_1234567890abcdef1234" not in on_disk
    assert "[REDACTED]" in on_disk
