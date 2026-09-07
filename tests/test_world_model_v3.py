"""V3 World Model tests."""

from __future__ import annotations

import pytest

from hermes.world_model import (
    EnvironmentState,
    EvidenceRecord,
    EvidenceSource,
    ReferenceBinding,
    ReferenceKind,
    ReferenceResolver,
    TaskRequirement,
    TaskState,
    WorldModel,
)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_world_model_starts_empty():
    wm = WorldModel()
    assert wm.environment.active_window is None
    assert wm.task.objective == ""
    assert wm.task.requirements == ()
    assert wm.evidence == ()
    assert wm.references == {}


def test_world_model_is_thread_safe():
    """Smoke test for the lock: concurrent updates don't corrupt state."""
    import threading

    wm = WorldModel()
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(100):
                wm.update_environment(active_window="x")
                wm.update_environment(browser_url="https://example.com")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


# ---------------------------------------------------------------------------
# Environment state
# ---------------------------------------------------------------------------


def test_update_environment_records_known_fields():
    wm = WorldModel()
    wm.update_environment(active_window="notepad.exe", browser_url="https://example.com")
    env = wm.environment
    assert env.active_window == "notepad.exe"
    assert env.browser_url == "https://example.com"
    assert env.as_of  # timestamp present


def test_update_environment_stores_unknown_fields_in_extra():
    wm = WorldModel()
    wm.update_environment(some_new_field="value", other=42)
    assert wm.environment.extra == {"some_new_field": "value", "other": 42}


def test_environment_state_to_dict_is_serialisable():
    env = EnvironmentState(
        active_window="chrome.exe",
        open_applications=("chrome.exe", "code.exe"),
        browser_url="https://example.com",
    )
    data = env.to_dict()
    assert data["active_window"] == "chrome.exe"
    assert data["open_applications"] == ["chrome.exe", "code.exe"]


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


def test_set_objective_replaces_task_state():
    wm = WorldModel()
    reqs = (TaskRequirement(capability="filesystem.write", description="x"),)
    wm.set_objective("write a file", requirements=reqs)
    assert wm.task.objective == "write a file"
    assert len(wm.task.requirements) == 1


def test_mark_requirement_satisfied_appends_evidence():
    wm = WorldModel()
    wm.set_objective(
        "write a file",
        requirements=(TaskRequirement(capability="filesystem.write", description="x"),),
    )
    wm.mark_requirement_satisfied("filesystem.write", "ev_abc")
    assert wm.task.unsatisfied == ()
    assert wm.task.all_satisfied
    assert "ev_abc" in wm.task.requirements[0].evidence


def test_unsatisfied_and_all_satisfied_reflect_state():
    reqs = (
        TaskRequirement(capability="a"),
        TaskRequirement(capability="b"),
        TaskRequirement(capability="c"),
    )
    state = TaskState(objective="x", requirements=reqs)
    assert len(state.unsatisfied) == 3
    assert not state.all_satisfied

    state.requirements = (TaskRequirement(capability="a", satisfied=True),)
    assert state.all_satisfied


def test_set_uncertainty_replaces_only_uncertainty():
    wm = WorldModel()
    wm.set_objective("write", requirements=(TaskRequirement(capability="fs.write"),))
    wm.set_uncertainty(("is the parent writable?",))
    assert wm.task.uncertainty == ("is the parent writable?",)
    assert len(wm.task.requirements) == 1


def test_set_status_preserves_state():
    wm = WorldModel()
    wm.set_objective("x", requirements=(TaskRequirement(capability="fs.read"),))
    wm.set_status("verifying")
    assert wm.task.status == "verifying"
    assert len(wm.task.requirements) == 1


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_record_make_assigns_id_and_timestamp():
    record = EvidenceRecord.make(
        source=EvidenceSource.OBSERVATION,
        capability="filesystem.read",
        claim="file exists",
    )
    assert record.evidence_id.startswith("ev_")
    assert record.recorded_at
    assert record.confidence == 1.0


def test_record_evidence_returns_id():
    wm = WorldModel()
    record = EvidenceRecord.make(
        source=EvidenceSource.VERIFIER,
        capability="filesystem.write",
        claim="verified",
    )
    evidence_id = wm.record_evidence(record)
    assert evidence_id == record.evidence_id
    assert wm.evidence[-1].evidence_id == evidence_id


def test_evidence_for_filters_by_capability():
    wm = WorldModel()
    wm.record_evidence(
        EvidenceRecord.make(
            source=EvidenceSource.OBSERVATION, capability="filesystem.read", claim="x"
        )
    )
    wm.record_evidence(
        EvidenceRecord.make(
            source=EvidenceSource.OBSERVATION, capability="filesystem.write", claim="y"
        )
    )
    only_read = wm.evidence_for("filesystem.read")
    assert len(only_read) == 1
    assert only_read[0].claim == "x"


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


def test_reference_binding_make_assigns_id_and_as_of():
    binding = ReferenceBinding.make(
        key="current_file",
        kind=ReferenceKind.FILE,
        value="/home/user/note.txt",
        provenance="write_file output",
    )
    assert binding.binding_id.startswith("ref_")
    assert binding.as_of


def test_resolver_returns_binding_for_existing_key():
    binding = ReferenceBinding.make(
        key="current_file",
        kind=ReferenceKind.FILE,
        value="/x",
    )
    resolver = ReferenceResolver({"current_file": binding})
    assert resolver.resolve("current_file") is binding


def test_resolver_returns_none_for_missing_key():
    resolver = ReferenceResolver({})
    assert resolver.resolve("missing") is None


def test_resolver_returns_none_for_expired_binding():
    binding = ReferenceBinding.make(
        key="ephemeral",
        kind=ReferenceKind.URL,
        value="https://x",
        expires_at="2000-01-01T00:00:00+00:00",
    )
    resolver = ReferenceResolver({"ephemeral": binding})
    assert resolver.resolve("ephemeral") is None


def test_resolver_all_keys():
    a = ReferenceBinding.make(key="a", kind=ReferenceKind.FILE, value="/a")
    b = ReferenceBinding.make(key="b", kind=ReferenceKind.URL, value="https://b")
    resolver = ReferenceResolver({"a": a, "b": b})
    assert set(resolver.all_keys()) == {"a", "b"}


def test_world_model_bind_and_lookup_reference():
    wm = WorldModel()
    binding = ReferenceBinding.make(
        key="active_url",
        kind=ReferenceKind.URL,
        value="https://example.com",
    )
    wm.bind_reference("active_url", binding)
    assert wm.lookup_reference("active_url") is binding
    assert wm.lookup_reference("missing") is None
    assert wm.drop_reference("active_url") is True
    assert wm.drop_reference("active_url") is False  # already gone


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_contains_all_four_sections():
    wm = WorldModel()
    wm.update_environment(active_window="chrome.exe")
    wm.set_objective("do x", requirements=(TaskRequirement(capability="fs.read"),))
    wm.record_evidence(
        EvidenceRecord.make(
            source=EvidenceSource.OBSERVATION, capability="fs.read", claim="x"
        )
    )
    wm.bind_reference(
        "k",
        ReferenceBinding.make(key="k", kind=ReferenceKind.FILE, value="/x"),
    )
    snap = wm.snapshot()
    assert "environment" in snap
    assert "task" in snap
    assert "evidence" in snap
    assert "references" in snap
    assert snap["environment"]["active_window"] == "chrome.exe"


# ---------------------------------------------------------------------------
# Anti-routing invariants
# ---------------------------------------------------------------------------


def test_world_model_has_no_routing_methods():
    """The World Model must not decide what to do."""
    wm = WorldModel()
    forbidden = [
        "route", "decide", "classify", "match", "dispatch", "plan",
        "interpret", "understand", "choose_intent", "score_message",
        "chat", "complete", "ask_llm", "run_recovery",
    ]
    for method in forbidden:
        assert not hasattr(wm, method)


def test_world_model_does_not_import_semantic_layers():
    import hermes.world_model as pkg

    modules = [pkg.state.__file__, pkg.evidence.__file__, pkg.reference.__file__]
    forbidden = ("intent.", "agent.orchestrator", "agent.goal_router", "agent.task_planner")
    for path in modules:
        text = open(path, encoding="utf-8").read()
        for token in forbidden:
            assert token not in text, f"{path} must not reference {token!r}"


def test_world_model_does_not_call_security_layer():
    """Defence-in-depth: state never touches policy/approval."""
    import hermes.world_model as pkg

    for path in (pkg.state.__file__, pkg.evidence.__file__):
        text = open(path, encoding="utf-8").read()
        for token in ("PolicyEngine", "ApprovalManager"):
            assert token not in text