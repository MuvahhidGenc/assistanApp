"""V3 Execution Log tests.

The tests verify the *new* Execution Log layer. V2 execution/verifier
behaviour is exercised by the V2 test suite (`tests/test_execution_*`,
`tests/test_verifiers.py`, etc.); these tests treat the Execution Log as
a pure data structure and exercise the boundary with V2 in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.execution_log import (
    EventEnvelope,
    EventKind,
    ExecutionLogStore,
)
from hermes.execution_log.audit import ExecutionLogAuditor
from hermes.execution_log.events import (
    ActionFinished,
    ActionStarted,
    ObservationRecorded,
    RecoveryFinished,
    RecoveryStarted,
    VerificationRecorded,
    action_finished_payload,
    action_started_payload,
    observation_recorded_payload,
    verification_recorded_payload,
    recovery_finished_payload,
    recovery_started_payload,
)
from hermes.execution_log.recovery import RecoveryEvidenceRecorder
from hermes.execution_log.verifier import extract_observation_data, record_verification
from hermes.tools.verifiers.base import (
    Observation,
    VerificationResult,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_log(tmp_path: Path) -> ExecutionLogStore:
    return ExecutionLogStore(path=tmp_path / "log.jsonl")


# ---------------------------------------------------------------------------
# Store: persistence + append-only
# ---------------------------------------------------------------------------


def test_store_is_durable_across_instances(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    store_a = ExecutionLogStore(path=path)
    store_a.append(action_started_payload("corr-1", None, "fs.write", "write_file", {"path": "/a"}, "client"))

    store_b = ExecutionLogStore(path=path)
    events = store_b.all()
    assert len(events) == 1
    assert events[0].kind == EventKind.ACTION_STARTED
    assert isinstance(events[0].payload, ActionStarted)


def test_store_appends_in_order(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {"p": "x"}, "client"))
    tmp_log.append(action_finished_payload("c1", "a1", "read_file", True, "content", None, "t0", "t1", 100))
    tmp_log.append(verification_recorded_payload("c1", "a1", "ReadFileVerifier", "filesystem.readable", "verified"))
    tmp_log.append(observation_recorded_payload("c1", "a1", "filesystem.list", "structured", {"entries": 3}))

    kinds = [e.kind for e in tmp_log.all()]
    assert kinds == [
        EventKind.ACTION_STARTED,
        EventKind.ACTION_FINISHED,
        EventKind.VERIFICATION_RECORDED,
        EventKind.OBSERVATION_RECORDED,
    ]


def test_store_file_is_real_jsonl(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {}, "client"))
    raw = tmp_log.path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1
    record = json.loads(raw[0])
    assert record["kind"] == "action_started"
    assert record["action_id"] == "a1"


def test_store_returns_frozen_tuples(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {}, "client"))
    events = tmp_log.all()
    assert isinstance(events, tuple)
    with pytest.raises(Exception):
        events[0] = "nope"  # type: ignore[index]


def test_store_rejects_unknown_event_kind_in_read(tmp_log: ExecutionLogStore):
    tmp_log.path.parent.mkdir(parents=True, exist_ok=True)
    with tmp_log.path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "made_up_kind", "payload": {}, "event_id": "x", "recorded_at": "t", "correlation_id": "c", "action_id": "a"}) + "\n")
    with pytest.raises(RuntimeError):
        tmp_log.all()


def test_store_rejects_malformed_json(tmp_log: ExecutionLogStore):
    tmp_log.path.parent.mkdir(parents=True, exist_ok=True)
    tmp_log.path.write_text("{this is not json\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        tmp_log.all()


def test_store_is_append_only(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {}, "client"))
    first_count = len(tmp_log.all())
    # Append more; the existing record must remain unchanged on disk.
    tmp_log.append(action_started_payload("c1", "a2", "fs.write", "write_file", {}, "client"))
    raw = tmp_log.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(raw[0])
    assert record["action_id"] == "a1"  # untouched
    assert len(tmp_log.all()) == first_count + 1


def test_store_no_event_is_silently_swallowed(tmp_log: ExecutionLogStore):
    """An OS error during write must surface, not be swallowed."""
    # Force a write to a non-writable parent.
    bad_store = ExecutionLogStore(path=Path("/proc/no-such-dir/log.jsonl"))
    with pytest.raises(OSError):
        bad_store.append(action_started_payload("c", "a", "x", "y", {}, "client"))


# ---------------------------------------------------------------------------
# Query surface
# ---------------------------------------------------------------------------


def test_events_for_filters_by_correlation(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {}, "client"))
    tmp_log.append(action_started_payload("c2", "a2", "fs.write", "write_file", {}, "client"))
    tmp_log.append(action_finished_payload("c1", "a1", "read_file", True, "ok", None, "t0", "t1", 50))

    only_c1 = tmp_log.events_for("c1")
    assert len(only_c1) == 2
    assert {e.correlation_id for e in only_c1} == {"c1"}


def test_events_for_unknown_correlation_is_empty(tmp_log: ExecutionLogStore):
    assert tmp_log.events_for("does-not-exist") == ()


def test_history_for_action_preserves_order(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {}, "client"))
    tmp_log.append(action_finished_payload("c1", "a1", "read_file", False, None, "boom", "t0", "t1", 5))
    tmp_log.append(verification_recorded_payload("c1", "a1", "ReadFileVerifier", "filesystem.readable", "failed"))
    tmp_log.append(recovery_started_payload("c1", "a1", "retry", "io_error"))
    tmp_log.append(recovery_finished_payload("c1", "a1", "retry", "recovered", "ok", 1, 4))

    history = tmp_log.history_for_action("a1")
    kinds = [e.kind for e in history]
    assert kinds == [
        EventKind.ACTION_STARTED,
        EventKind.ACTION_FINISHED,
        EventKind.VERIFICATION_RECORDED,
        EventKind.RECOVERY_STARTED,
        EventKind.RECOVERY_FINISHED,
    ]


def test_actions_lists_distinct_action_ids_in_first_seen_order(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {}, "client"))
    tmp_log.append(action_started_payload("c1", "a1", "fs.write", "write_file", {}, "client"))  # same id
    tmp_log.append(action_started_payload("c1", "a2", "fs.list", "list_directory", {}, "client"))
    tmp_log.append(action_started_payload("c2", "a3", "fs.copy", "copy_file", {}, "client"))

    assert tmp_log.actions() == ("a1", "a2", "a3")


def test_count_by_kind_aggregates(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.read", "read_file", {}, "client"))
    tmp_log.append(action_started_payload("c1", "a2", "fs.write", "write_file", {}, "client"))
    tmp_log.append(action_finished_payload("c1", "a1", "read_file", True, "ok", None, "t0", "t1", 50))
    counts = tmp_log.count_by_kind()
    assert counts == {
        "action_started": 2,
        "action_finished": 1,
    }


# ---------------------------------------------------------------------------
# Action success != observation success != verification success != goal success
# ---------------------------------------------------------------------------


def test_action_success_is_recorded_independently_from_verification(tmp_log: ExecutionLogStore):
    """Action reports success, but verifier reports failed. Both are kept."""
    tmp_log.append(action_started_payload("c1", "a1", "fs.write", "write_file", {"path": "/x"}, "client"))
    tmp_log.append(action_finished_payload("c1", "a1", "write_file", True, "wrote", None, "t0", "t1", 10))
    tmp_log.append(verification_recorded_payload("c1", "a1", "WriteFileVerifier", "filesystem.exists", "failed", {"reason": "file_not_found"}))

    events = tmp_log.all()
    action = next(e for e in events if e.kind is EventKind.ACTION_FINISHED)
    verification = next(e for e in events if e.kind is EventKind.VERIFICATION_RECORDED)
    assert action.payload.success is True
    assert verification.payload.status == "failed"
    # The log does not collapse them; the runner must combine.


def test_observation_is_kept_separate_from_action(tmp_log: ExecutionLogStore):
    tmp_log.append(action_started_payload("c1", "a1", "fs.write", "write_file", {"path": "/x"}, "client"))
    tmp_log.append(action_finished_payload("c1", "a1", "write_file", True, "wrote", None, "t0", "t1", 10))
    tmp_log.append(observation_recorded_payload("c1", "a1", "filesystem.list", "structured", {"entries": []}))

    action = tmp_log.all()[1]
    observation = tmp_log.all()[2]
    assert action.kind is EventKind.ACTION_FINISHED
    assert observation.kind is EventKind.OBSERVATION_RECORDED
    assert observation.payload.data == {"entries": []}
    # No event in the log asserts "goal achieved" — that is the runner's job.


def test_log_has_no_goal_completion_event():
    """Closed taxonomy: there is no event kind that says 'goal achieved'."""
    kinds = {member.value for member in EventKind}
    assert "goal_achieved" not in kinds
    assert "goal_completed" not in kinds
    assert "mission_completed" not in kinds


# ---------------------------------------------------------------------------
# Verifier boundary
# ---------------------------------------------------------------------------


def test_record_verification_translates_v2_result(tmp_log: ExecutionLogStore):
    result = VerificationResult(
        status=VerificationStatus.VERIFIED,
        method="filesystem.exists",
        details={"size": 1024},
        observation=Observation(source="filesystem.stat", data={"size": 1024}, observed_at="2026-09-07T00:00:00+00:00"),
    )
    envelope = record_verification(
        tmp_log,
        correlation_id="c1",
        action_id="a1",
        verifier_name="WriteFileVerifier",
        result=result,
    )
    assert envelope.kind is EventKind.VERIFICATION_RECORDED
    payload = envelope.payload
    assert isinstance(payload, VerificationRecorded)
    assert payload.verifier == "WriteFileVerifier"
    assert payload.status == "verified"
    assert payload.details == {"size": 1024}


def test_record_verification_rejects_unknown_status(tmp_log: ExecutionLogStore):
    # Bypass the dataclass to manufacture an invalid status string.
    with pytest.raises(ValueError):
        record_verification(
            tmp_log,
            correlation_id="c1",
            action_id="a1",
            verifier_name="X",
            result=VerificationResult(
                status="not_a_real_status",  # type: ignore[arg-type]
                method="x",
            ),
        )


def test_extract_observation_data_handles_none():
    result = VerificationResult(status=VerificationStatus.NOT_REQUIRED, method="x")
    assert extract_observation_data(result) == {}


# ---------------------------------------------------------------------------
# Recovery evidence
# ---------------------------------------------------------------------------


def test_recovery_recorder_emits_pair_and_tracks_budget(tmp_log: ExecutionLogStore):
    recorder = RecoveryEvidenceRecorder(
        store=tmp_log, correlation_id="c1", action_id="a1", budget_total=3
    )
    recorder.started(strategy_id="retry", reason="io_error", idempotency_key="idem-1")
    recorder.finished(strategy_id="retry", result="recovered", user_message="ok")
    recorder.started(strategy_id="alt", reason="still_failing")
    recorder.finished(strategy_id="alt", result="failed", user_message="nope")

    events = tmp_log.all()
    kinds = [e.kind for e in events]
    assert kinds == [
        EventKind.RECOVERY_STARTED,
        EventKind.RECOVERY_FINISHED,
        EventKind.RECOVERY_STARTED,
        EventKind.RECOVERY_FINISHED,
    ]
    last_finished = events[-1].payload
    assert isinstance(last_finished, RecoveryFinished)
    assert last_finished.attempts_used == 2
    assert last_finished.budget_remaining == 1


def test_recovery_recorder_rejects_unknown_result(tmp_log: ExecutionLogStore):
    recorder = RecoveryEvidenceRecorder(
        store=tmp_log, correlation_id="c1", action_id="a1", budget_total=3
    )
    with pytest.raises(ValueError):
        recorder.finished(strategy_id="retry", result="succeeded", user_message="ok")


def test_recovery_recorder_does_not_double_count_on_aborted_start(tmp_log: ExecutionLogStore):
    """`started` does not increment attempts — only `finished` does."""
    recorder = RecoveryEvidenceRecorder(
        store=tmp_log, correlation_id="c1", action_id="a1", budget_total=3
    )
    recorder.started(strategy_id="retry", reason="x")
    assert recorder.budget_remaining == 3
    recorder.started(strategy_id="retry", reason="x")
    assert recorder.budget_remaining == 3


# ---------------------------------------------------------------------------
# Auditor convenience wrapper
# ---------------------------------------------------------------------------


def test_auditor_writes_one_event_per_call(tmp_log: ExecutionLogStore):
    auditor = ExecutionLogAuditor(tmp_log)
    auditor.action_started(
        correlation_id="c1",
        capability="fs.write",
        tool="write_file",
        arguments={"path": "/a"},
        execution_target="client",
        action_id="a1",
        risk_level="normal_modification",
        security_decision="allow",
        approval_outcome=None,
    )
    auditor.action_finished(
        correlation_id="c1",
        action_id="a1",
        tool="write_file",
        success=True,
        output={"path": "/a"},
        error=None,
        started_at="t0",
        finished_at="t1",
        duration_ms=42,
    )
    auditor.observation_recorded(
        correlation_id="c1",
        action_id="a1",
        source="filesystem.list",
        observation_type="structured",
        data={"entries": 1},
    )

    kinds = [e.kind for e in tmp_log.all()]
    assert kinds == [
        EventKind.ACTION_STARTED,
        EventKind.ACTION_FINISHED,
        EventKind.OBSERVATION_RECORDED,
    ]
    started = tmp_log.all()[0].payload
    assert started.security_decision == "allow"
    assert started.risk_level == "normal_modification"


# ---------------------------------------------------------------------------
# No-routing invariants — registry/log is not a router
# ---------------------------------------------------------------------------


def test_execution_log_has_no_routing_or_llm_methods(tmp_log: ExecutionLogStore):
    """Critical: the Execution Log must not decide or call LLMs."""
    forbidden_on_store = [
        "route", "decide", "classify", "match", "dispatch", "plan",
        "interpret", "understand", "choose_intent", "score_message",
        "chat", "complete", "ask_llm",
    ]
    for method in forbidden_on_store:
        assert not hasattr(tmp_log, method), (
            f"ExecutionLogStore must not expose '{method}'."
        )


def test_execution_log_modules_do_not_import_intent_or_orchestrator():
    """Defensive: Execution Log must not depend on semantic layers."""
    import hermes.execution_log as pkg

    modules = [
        pkg.events.__file__,
        pkg.store.__file__,
        pkg.verifier.__file__,
        pkg.recovery.__file__,
        pkg.audit.__file__,
    ]
    forbidden_substrings = (
        "intent.",  # hermes.intent
        "agent.orchestrator",
        "agent.conversation_flow",
        "agent.goal_parser",
        "agent.goal_router",
        "agent.task_planner",
        "agent.local_intent",
    )
    for path in modules:
        text = open(path, encoding="utf-8").read()
        for forbidden in forbidden_substrings:
            assert forbidden not in text, (
                f"{path} must not reference '{forbidden}'."
            )


def test_execution_log_does_not_call_policy_or_approval():
    """Security: Execution Log must not decide access — it only records facts."""
    import hermes.execution_log.audit as audit_module
    import hermes.execution_log.recovery as recovery_module

    for path in (audit_module.__file__, recovery_module.__file__):
        text = open(path, encoding="utf-8").read()
        for forbidden in ("PolicyEngine", "ApprovalManager", "evaluate_policy"):
            assert forbidden not in text, (
                f"{path} must not reference '{forbidden}'."
            )


# ---------------------------------------------------------------------------
# Determinism + serialization
# ---------------------------------------------------------------------------


def test_serialization_is_deterministic_across_runs(tmp_path: Path):
    """Two stores writing the same envelopes produce identical files."""
    path = tmp_path / "shared.jsonl"

    def write_once(target: Path):
        store = ExecutionLogStore(path=target)
        store.append(action_started_payload("c1", "a1", "fs.read", "read_file", {"path": "/x"}, "client"))
        store.append(action_finished_payload("c1", "a1", "read_file", True, "ok", None, "t0", "t1", 1))
        return target.read_text(encoding="utf-8")

    # Two separate stores writing to two separate files with the same shape
    # must produce identical JSONL (timestamps aside, both ISO-8601 same).
    text_a = write_once(tmp_path / "a.jsonl")
    text_b = write_once(tmp_path / "b.jsonl")

    def normalize(text: str) -> str:
        # Drop recorded_at and event_id (both differ between runs).
        out = []
        for line in text.strip().splitlines():
            record = json.loads(line)
            record.pop("event_id", None)
            record.pop("recorded_at", None)
            out.append(json.dumps(record, sort_keys=True))
        return "\n".join(out)

    assert normalize(text_a) == normalize(text_b)


def test_append_many_writes_in_order(tmp_log: ExecutionLogStore):
    envelopes = [
        action_started_payload("c", "a1", "fs.read", "read_file", {}, "client"),
        action_finished_payload("c", "a1", "read_file", True, "ok", None, "t0", "t1", 5),
        verification_recorded_payload("c", "a1", "V", "filesystem.readable", "verified"),
    ]
    tmp_log.append_many(envelopes)
    assert [e.kind for e in tmp_log.all()] == [
        EventKind.ACTION_STARTED,
        EventKind.ACTION_FINISHED,
        EventKind.VERIFICATION_RECORDED,
    ]


# ---------------------------------------------------------------------------
# Persistence is real, not in-memory masquerade
# ---------------------------------------------------------------------------


def test_path_property_reflects_disk_location(tmp_path: Path):
    p = tmp_path / "x.jsonl"
    store = ExecutionLogStore(path=p)
    assert store.path == p
    assert not store.path.exists()  # not pre-created

    store.append(action_started_payload("c", "a", "x", "y", {}, "client"))
    assert store.path.exists()
    assert store.path.stat().st_size > 0


def test_lifecycle_helpers(tmp_path: Path):
    path = tmp_path / "log.jsonl"
    store = ExecutionLogStore(path=path)
    store.append(action_started_payload("c", "a", "x", "y", {}, "client"))
    backup = store.rotate()
    assert backup.exists()
    assert not store.path.exists()

    # After rotate, writing again starts a fresh log.
    store.append(action_started_payload("c", "a", "x", "y", {}, "client"))
    assert store.path.exists()
    assert len(store.all()) == 1

    store.reset()
    assert not store.path.exists()
    assert store.all() == ()