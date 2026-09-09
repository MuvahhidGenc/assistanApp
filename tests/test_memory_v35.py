"""V3.5 production memory acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hermes.execution_log import ExecutionLogStore
from hermes.memory import (
    MemorySecurityError,
    RuntimeMemory,
    SessionMemory,
)
from hermes.runtime.bootstrap import build_v3_application
from hermes.runtime.session import SessionStore, snapshot_from_world_model
from hermes.world_model import EvidenceRecord, EvidenceSource, WorldModel
from tests._approval_providers import approving_provider


class _ReplyServer:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.requests: list[Any] = []

    async def chat(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        reply = self.replies.pop(0)
        return {"choices": [{"message": {"content": json.dumps(reply)}}]}


@pytest.mark.asyncio
async def test_production_session_persists_and_restores_context(
    tmp_path: Path,
):
    state_dir = tmp_path / "state"
    folder = tmp_path / "verified-folder"
    first_server = _ReplyServer(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(folder)},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "folder ready",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
        ]
    )
    first_app = build_v3_application(
        server=first_server,
        log_dir=state_dir,
        approval_provider=approving_provider(),
    )

    first_reply = await first_app.process_message("Klasörü oluştur.")

    session_id = first_app.orchestrator.state.current_session_id
    assert first_reply == "folder ready"
    assert folder.is_dir()
    assert (state_dir / "active_session.json").is_file()
    assert (state_dir / "sessions" / f"{session_id}.json").is_file()
    assert (state_dir / "memory" / "sessions" / f"{session_id}.json").is_file()

    second_server = _ReplyServer(
            [
                {
                    "kind": "complete",
                    "summary": "context restored",
                    "evidence_ids": [],
                    "required_capabilities": [],
                }
            ]
    )
    second_app = build_v3_application(
        server=second_server,
        log_dir=state_dir,
        approval_provider=approving_provider(),
    )
    second_reply = await second_app.process_message("Yeni turn.")

    assert second_reply == "context restored"
    payload = json.loads(second_server.requests[0].message)
    assert payload["verified_references"]["last_folder"]["value"] == str(folder)
    assert payload["turn"]["objective"] == "Yeni turn."
    assert payload["turn"]["current_requirements"] == []
    assert payload["turn"]["client_session_id"] == session_id
    assert payload["current_evidence"] == []
    assert payload["completion_eligible_evidence_ids"] == []
    assert "session_turns" not in payload["historical_context"]
    assert payload["historical_context"]["previous_successful_tasks"][-1][
        "summary"
    ] == "folder ready"


@pytest.mark.asyncio
async def test_sessions_are_isolated_in_production_runtime(tmp_path: Path):
    folder = tmp_path / "session-a-folder"
    server = _ReplyServer(
        [
            {
                "kind": "action",
                "capability": "filesystem.write",
                "arguments": {"path": str(folder)},
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "A complete",
                "evidence_ids": [],
                "required_capabilities": ["filesystem.write"],
            },
            {
                "kind": "complete",
                "summary": "B complete",
                "evidence_ids": [],
                "required_capabilities": [],
            },
        ]
    )
    app = build_v3_application(
        server=server,
        log_dir=tmp_path / "state",
        approval_provider=approving_provider(),
    )
    await app.process_message("A görevi.", session_id="session_A")
    await app.process_message("B görevi.", session_id="session_B")

    b_payload = json.loads(server.requests[2].message)
    assert b_payload["verified_references"] == {}
    assert b_payload["current_evidence"] == []
    assert b_payload["turn"]["client_session_id"] == "session_B"
    assert "session_turns" not in b_payload["historical_context"]
    assert app.memory.active_session_id == "session_B"


def test_episodic_and_long_term_memory_really_persist(tmp_path: Path):
    directory = tmp_path / "memory"
    memory = RuntimeMemory(directory)
    memory.record_episode(
        summary="Created a verified report",
        capabilities=("filesystem.write",),
    )
    memory.long_term.remember(
        "language",
        "tr-TR",
        provenance="explicit_user_statement",
    )

    assert (directory / "episodic.json").is_file()
    assert (directory / "long_term.json").is_file()
    reloaded = RuntimeMemory(directory)
    assert reloaded.episodic.records[0].capabilities == ("filesystem.write",)
    assert reloaded.long_term.recall("language").value == "tr-TR"


@pytest.mark.asyncio
async def test_explicit_long_term_fact_from_reasoning_persists(tmp_path: Path):
    state_dir = tmp_path / "state"
    server = _ReplyServer(
        [
            {
                "kind": "complete",
                "summary": "Tercihini hatırlayacağım.",
                "evidence_ids": [],
                "memory_facts": [{"key": "language", "value": "tr-TR"}],
                "required_capabilities": [],
            },
            {
                "kind": "complete",
                "summary": "Tercih bağlamda.",
                "evidence_ids": [],
                "required_capabilities": [],
            },
        ]
    )
    app = build_v3_application(
        server=server,
        log_dir=state_dir,
        approval_provider=approving_provider(),
    )

    await app.process_message(
        "Dil tercihimi Türkçe olarak hatırla.",
        session_id="sess_preference",
    )
    await app.process_message(
        "Tercihim neydi?",
        session_id="sess_preference",
    )

    reloaded = RuntimeMemory(state_dir / "memory")
    fact = reloaded.long_term.recall("language")
    assert fact is not None
    assert fact.value == "tr-TR"
    assert fact.provenance == "explicit_user:sess_preference"
    second_payload = json.loads(server.requests[1].message)
    assert second_payload["historical_context"]["relevant_historical_facts"] == [
        {"key": "language", "value": "tr-TR"}
    ]


@pytest.mark.asyncio
async def test_sensitive_reasoning_memory_fact_is_rejected_fail_closed(
    tmp_path: Path,
):
    state_dir = tmp_path / "state"
    server = _ReplyServer(
        [
            {
                "kind": "complete",
                "summary": "Kaydettim.",
                "evidence_ids": [],
                "memory_facts": [{"key": "password", "value": "hunter2"}],
                "required_capabilities": [],
            },
            {
                "kind": "user_question",
                "question": "Kimlik bilgilerini kalıcı hafızaya kaydedemem.",
                "required_capabilities": [],
            },
        ]
    )
    app = build_v3_application(
        server=server,
        log_dir=state_dir,
        approval_provider=approving_provider(),
    )

    outcome = await app.orchestrator.process_message(
        "Şifremi hatırla: password=hunter2",
        session_id="sess_reject_secret",
    )

    assert "kaydedemem" in outcome
    assert app.memory.long_term.all_facts() == ()
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in state_dir.rglob("*.json")
    )
    assert "hunter2" not in persisted


def test_all_memory_layers_scrub_sensitive_values_on_disk(tmp_path: Path):
    memory = RuntimeMemory(tmp_path / "memory")
    memory.activate_session("sess_secure")
    memory.record_exchange(
        "password=hunter2 api_key=abcd1234",
        "token=ghp_1234567890abcdef",
    )
    memory.record_episode(
        summary="credential=supersecret",
        capabilities=("system.inspect",),
    )
    memory.long_term.remember(
        "note",
        "secret=hidden Bearer abcdefghijklmnop",
    )

    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "memory").rglob("*.json")
    )
    for secret in (
        "hunter2",
        "abcd1234",
        "ghp_1234567890abcdef",
        "supersecret",
        "hidden",
        "abcdefghijklmnop",
    ):
        assert secret not in persisted
    assert "[REDACTED]" in persisted


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "api_key",
        "token",
        "secret",
        "private key",
        "credential",
    ],
)
def test_long_term_memory_rejects_sensitive_keys(tmp_path: Path, key: str):
    memory = RuntimeMemory(tmp_path / "memory")

    with pytest.raises(ValueError):
        memory.long_term.remember(key, "must-not-persist")

    if (tmp_path / "memory" / "long_term.json").exists():
        assert "must-not-persist" not in (
            tmp_path / "memory" / "long_term.json"
        ).read_text(encoding="utf-8")


def test_persistence_scrubber_failure_rolls_back_and_keeps_disk_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "session.json"
    memory = SessionMemory(path=path)
    memory.record("user", "safe")
    before_disk = path.read_bytes()
    before_turns = memory.turns

    def _fail_closed(value: Any) -> Any:
        raise MemorySecurityError("forced scrubber failure")

    monkeypatch.setattr("hermes.memory.security.scrub_data", _fail_closed)
    with pytest.raises(MemorySecurityError):
        memory.record("user", "password=must-not-leak")

    assert path.read_bytes() == before_disk
    assert memory.turns == before_turns
    assert b"must-not-leak" not in path.read_bytes()


def test_runtime_session_snapshot_scrubs_world_model_evidence(tmp_path: Path):
    world = WorldModel()
    world.set_objective("token=abcdefghijklmnop")
    world.record_evidence(
        EvidenceRecord.make(
            source=EvidenceSource.TOOL_REPORT,
            capability="system.inspect",
            claim="credential=world-secret",
            data={"api_key": "key-secret"},
        )
    )
    store = SessionStore(directory=tmp_path / "sessions")
    state = snapshot_from_world_model(
        world,
        session_id="sess_world",
        objective=world.task.objective,
    )

    path = store.save(state)

    persisted = path.read_text(encoding="utf-8")
    assert "abcdefghijklmnop" not in persisted
    assert "world-secret" not in persisted
    assert "key-secret" not in persisted
    assert persisted.count("[REDACTED]") >= 3


def test_memory_is_not_world_model_or_execution_log(tmp_path: Path):
    memory = RuntimeMemory(tmp_path / "memory")
    world = WorldModel()
    log = ExecutionLogStore(path=tmp_path / "execution.jsonl")
    world.set_objective("current reality")
    memory.record_episode(
        summary="past task",
        capabilities=("filesystem.write",),
    )

    episodic_payload = (tmp_path / "memory" / "episodic.json").read_text(
        encoding="utf-8"
    )
    assert "current reality" not in episodic_payload
    assert "action_started" not in episodic_payload
    assert log.all() == ()
    assert world.evidence == ()


def test_production_runtime_uses_real_memory_not_noop(tmp_path: Path):
    server = _ReplyServer(
        [
            {
                "kind": "complete",
                "summary": "ok",
                "evidence_ids": [],
                "required_capabilities": [],
            }
        ]
    )
    app = build_v3_application(
        server=server,
        log_dir=tmp_path / "state",
        approval_provider=approving_provider(),
    )

    assert isinstance(app.memory, RuntimeMemory)
    source = Path(__import__("hermes.runtime.bootstrap", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    assert "_NoOpMemory" not in source
    assert "no-op memory" not in source.casefold()


def test_corrupt_persistent_memory_fails_closed(tmp_path: Path):
    path = tmp_path / "session.json"
    path.write_text('{"schema_version": 1, "turns": [', encoding="utf-8")

    with pytest.raises(OSError):
        SessionMemory(path=path)
