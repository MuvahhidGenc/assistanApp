import pytest

from hermes.utils.logging import setup_cli_logging


@pytest.fixture(autouse=True)
def _reset_logging():
    setup_cli_logging(debug=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_client_state(tmp_path, monkeypatch):
    state_file = tmp_path / "hermes-state" / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr(
        "hermes.client.session_store.ensure_user_dirs",
        lambda: state_file.parent.mkdir(parents=True, exist_ok=True),
    )
