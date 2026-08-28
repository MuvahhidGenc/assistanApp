from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hermes.server.client import HermesServerClient, normalize_server_urls
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
