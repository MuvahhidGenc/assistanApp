from __future__ import annotations

import asyncio
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from hermes.client.rpc_server import LocalToolRpcServer
from hermes.client.session_store import (
    get_or_create_rpc_secret,
    load_client_state,
    save_client_state,
)
from hermes.config.settings import AppSettings, RiskLevel
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def rpc_server():
    loop = asyncio.new_event_loop()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    loop_thread = threading.Thread(target=_run_loop, name="rpc-test-loop", daemon=True)
    loop_thread.start()

    registry = create_default_registry()
    settings = AppSettings()
    executor = ToolExecutor(
        registry,
        PolicyEngine(settings.security.require_approval_for),
        AuditLogger(settings.security.audit_log_path, settings.security.redact_patterns),
        ApprovalManager(),
    )
    port = _free_port()
    secret = "test-rpc-secret"
    server = LocalToolRpcServer(
        executor,
        loop,
        host="127.0.0.1",
        port=port,
        secret=secret,
    )
    server.start()
    yield server, port, secret, loop
    server.stop()
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=5)
    loop.close()


def _post(url: str, body: dict, secret: str) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "X-Secret": secret},
    )
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_rpc_rejects_bad_secret(rpc_server):
    _, port, _, _ = rpc_server
    with pytest.raises(HTTPError) as exc:
        _post(f"http://127.0.0.1:{port}/v1/tool/echo", {"args": {"message": "x"}}, "wrong")
    assert exc.value.code == 401


@pytest.mark.asyncio
async def test_rpc_list_directory(rpc_server, tmp_path, monkeypatch):
    server, port, secret, loop = rpc_server
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (desktop / "proof.txt").write_text("rpc", encoding="utf-8")
    monkeypatch.setattr("hermes.tools.windows.file_tools.Path.home", lambda: tmp_path)

    payload = await asyncio.to_thread(
        _post,
        f"http://127.0.0.1:{port}/v1/tool/list_directory",
        {"args": {"path": str(desktop)}},
        secret,
    )
    assert payload["ok"] is True
    assert payload["result"]["success"] is True
    names = [item["name"] for item in payload["result"]["output"]["entries"]]
    assert "proof.txt" in names


def test_rpc_secret_persisted_in_client_json(tmp_path, monkeypatch):
    state_file = tmp_path / "client.json"
    monkeypatch.setattr("hermes.client.session_store.client_state_path", lambda: state_file)
    monkeypatch.setattr("hermes.client.session_store.ensure_user_dirs", lambda: None)
    save_client_state({"client_id": "test-client"})
    secret = get_or_create_rpc_secret()
    assert secret
    stored = load_client_state()
    assert stored["rpc_secret"] == secret
