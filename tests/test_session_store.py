from __future__ import annotations

from hermes.client.local_context import LocalClientContext
from hermes.client.session_store import get_client_id, load_session_id, save_session_id
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
    assert "VPS" in instructions or "bagli degil" in instructions.lower() or "bağlı değil" in instructions
