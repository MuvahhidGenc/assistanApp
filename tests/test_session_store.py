from __future__ import annotations

from hermes.client.local_context import LocalClientContext

from hermes.client.session_store import (
    append_conversation_turn,
    clear_session_id,
    ensure_session_id,
    get_client_id,
    load_client_state,
    load_conversation_history,
    load_session_id,
    new_conversation_id,
    save_session_id,
)
from hermes.tools.registry import create_default_registry


def test_session_store_client_id_stable(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )

    first = get_client_id()
    second = get_client_id()
    assert first == second


def test_session_store_save_load_session_id(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )
    get_client_id()
    assert load_session_id() is None
    save_session_id("sess-abc")
    assert load_session_id() == "sess-abc"


def test_save_session_id_ignores_empty(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )
    get_client_id()
    save_session_id("")
    save_session_id("   ")
    assert load_session_id() is None


def test_ensure_session_id_does_not_invent_uuid(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )
    client_id = get_client_id()
    assert ensure_session_id() is None
    save_session_id("sess-abc")
    assert ensure_session_id() == "sess-abc"
    assert get_client_id() == client_id


def test_conversation_history_roundtrip(tmp_path, monkeypatch):
    append_conversation_turn("user", "Test")
    append_conversation_turn("assistant", "Pong")
    history = load_conversation_history()
    assert history == [
        {"role": "user", "content": "Test"},
        {"role": "assistant", "content": "Pong"},
    ]


def test_new_conversation_id_persists_without_rotating_client_id(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )
    client_id = get_client_id()
    first = new_conversation_id()
    second = new_conversation_id()
    assert first
    assert second
    assert first != second
    assert load_session_id() == second
    assert get_client_id() == client_id


def test_clear_session_id_keeps_client_id(tmp_path, monkeypatch):
    state_file = tmp_path / "state" / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )
    client_id = get_client_id()
    save_session_id("sess-old")
    clear_session_id()
    assert load_session_id() is None
    assert get_client_id() == client_id
    state = load_client_state()
    assert state["client_id"] == client_id
    assert "session_id" not in state


def test_local_context_metadata_flags_windows_client():
    ctx = LocalClientContext(create_default_registry())
    metadata = ctx.build_session_metadata()
    assert metadata["local_tools_connected"] is True
    assert metadata["platform"] == "windows"
    assert metadata["windows_client_id"]
    assert len(metadata["local_tools"]) >= 20


def test_local_context_instructions_mention_network_and_pc():
    ctx = LocalClientContext(create_default_registry())
    instructions = ctx.build_run_instructions()
    assert "get_network_config" in instructions
    assert "screenshot" in instructions
    assert "download_file" in instructions
    assert "git_clone" in instructions
    assert "LOCAL_TOOL" in instructions
    assert "open_url" in instructions
