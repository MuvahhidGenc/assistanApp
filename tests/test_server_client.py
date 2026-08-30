from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hermes.server.client import (
    HermesServerClient,
    HermesServerError,
    extract_session_id,
    normalize_server_urls,
)
from hermes.server.models import HermesApprovalSubmit, HermesApprovalChoice


def test_normalize_server_urls_with_v1_suffix():
    root, v1 = normalize_server_urls("http://50.6.226.228:8642/v1")
    assert root == "http://50.6.226.228:8642"
    assert v1 == "http://50.6.226.228:8642/v1"


def test_normalize_server_urls_without_v1_suffix():
    root, v1 = normalize_server_urls("http://50.6.226.228:8642")
    assert root == "http://50.6.226.228:8642"
    assert v1 == "http://50.6.226.228:8642/v1"


@pytest.mark.asyncio
async def test_get_models():
    client = HermesServerClient("http://example.com/v1", "test-key")
    mock_response = {"object": "list", "data": [{"id": "hermes-agent", "object": "model"}]}

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=mock_response):
        result = await client.get_models()

    assert result.data[0].id == "hermes-agent"
    await client.close()


@pytest.mark.asyncio
async def test_create_run_uses_v1_runs():
    client = HermesServerClient("http://example.com/v1", "test-key", model="hermes-agent")
    mock_response = {"run_id": "run_abc", "status": "started"}

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=mock_response) as req:
        run = await client.create_run("hello", session_id="sess-1")

    req.assert_called_once()
    assert req.call_args.args[0] == "POST"
    assert req.call_args.args[1] == "/v1/runs"
    assert run.id == "run_abc"
    body = req.call_args.kwargs["json_body"]
    assert body["session_id"] == "sess-1"
    assert client._active_session_id == "sess-1"
    await client.close()


@pytest.mark.asyncio
async def test_create_run_omits_empty_session_id():
    client = HermesServerClient("http://example.com/v1", "test-key", model="hermes-agent")
    mock_response = {"run_id": "run_abc", "status": "started"}

    with patch.object(client, "_request", new_callable=AsyncMock, return_value=mock_response) as req:
        await client.create_run("hello", session_id="")

    body = req.call_args.kwargs["json_body"]
    assert "session_id" not in body
    await client.close()


def test_extract_session_id_from_nested_payloads():
    assert extract_session_id({"id": "sess-1"}) == "sess-1"
    assert extract_session_id({"session_id": "sess-2"}) == "sess-2"
    assert extract_session_id({"data": {"id": "sess-3"}}) == "sess-3"
    assert extract_session_id({"session": {"session_id": "sess-4"}}) == "sess-4"
    assert extract_session_id({}) == ""
    assert extract_session_id(None) == ""


@pytest.mark.asyncio
async def test_create_session_sends_client_id():
    from hermes.server.models import SessionCreate

    client = HermesServerClient("http://example.com/v1", "test-key")
    with patch.object(
        client,
        "_request",
        new_callable=AsyncMock,
        return_value={"id": "sess-mine"},
    ) as req:
        session = await client.create_session(
            SessionCreate(id="sess-mine", session_id="sess-mine")
        )

    assert session.id == "sess-mine"
    assert req.call_args.kwargs["json_body"]["id"] == "sess-mine"
    await client.close()


@pytest.mark.asyncio
async def test_create_session_treats_conflict_as_existing():
    from hermes.server.models import SessionCreate

    client = HermesServerClient("http://example.com/v1", "test-key")
    with patch.object(
        client,
        "_request",
        new_callable=AsyncMock,
        side_effect=HermesServerError("session_exists", status_code=409),
    ):
        session = await client.create_session(SessionCreate(id="sess-mine"))

    assert session.id == "sess-mine"
    await client.close()


@pytest.mark.asyncio
async def test_client_does_not_send_session_key_header():
    client = HermesServerClient(
        "http://example.com/v1", "test-key", session_key="hermes-pc-session"
    )
    headers = client._build_headers()
    assert "X-Hermes-Session-Key" not in headers
    assert headers["Authorization"] == "Bearer test-key"
    await client.close()


@pytest.mark.asyncio
async def test_create_session_falls_back_to_v1():
    client = HermesServerClient("http://example.com/v1", "test-key")
    with patch.object(
        client,
        "_request",
        new_callable=AsyncMock,
        side_effect=[
            HermesServerError("missing", status_code=404),
            {"data": {"id": "sess-v1"}},
        ],
    ) as req:
        session = await client.create_session()

    assert session.id == "sess-v1"
    assert req.call_args_list[0].args[1] == "/api/sessions"
    assert req.call_args_list[1].args[1] == "/v1/sessions"
    await client.close()


@pytest.mark.asyncio
async def test_get_session_empty_body_is_not_found():
    client = HermesServerClient("http://example.com/v1", "test-key")
    with patch.object(client, "_request", new_callable=AsyncMock, return_value={}):
        with pytest.raises(HermesServerError) as exc:
            await client.get_session("sess-missing")
    assert exc.value.is_not_found()
    await client.close()


@pytest.mark.asyncio
async def test_request_retries_connection_errors():
    import httpx

    client = HermesServerClient("http://example.com/v1", "test-key")
    ok = httpx.Response(200, json={"status": "ok"})
    mock_http = AsyncMock()
    mock_http.request = AsyncMock(side_effect=[httpx.ConnectError("offline"), ok])
    mock_http.aclose = AsyncMock()
    client._client = mock_http
    client._make_client = lambda: mock_http  # type: ignore[method-assign]
    with patch("hermes.server.client.asyncio.sleep", new_callable=AsyncMock):
        data = await client._request("GET", "/health")
    assert data["status"] == "ok"
    assert mock_http.request.await_count == 2
    await client.close()


@pytest.mark.asyncio
async def test_stop_run_ignores_not_found():
    client = HermesServerClient("http://example.com/v1", "test-key")
    with patch.object(
        client,
        "_request",
        new_callable=AsyncMock,
        side_effect=HermesServerError(
            "{'message': 'Run not found: run_abc', 'code': 'run_not_found'}",
            status_code=404,
        ),
    ):
        await client.stop_run("run_abc")
    await client.close()


@pytest.mark.asyncio
async def test_submit_approval_uses_run_path():
    client = HermesServerClient("http://example.com/v1", "test-key")
    submit = HermesApprovalSubmit(run_id="run_abc", choice=HermesApprovalChoice.ONCE)

    with patch.object(client, "_request", new_callable=AsyncMock, return_value={"ok": True}) as req:
        await client.submit_approval(submit)

    assert req.call_args.args[1] == "/v1/runs/run_abc/approval"
    assert req.call_args.kwargs["json_body"] == {"choice": "once"}
    await client.close()
