from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from hermes.server.models import (
    Capabilities,
    ChatRequest,
    HealthResponse,
    HermesApprovalSubmit,
    ModelInfo,
    ModelsResponse,
    Run,
    RunCreate,
    RunEvent,
    RunEventType,
    Session,
    SessionCreate,
)
from hermes.utils.logging import get_logger

logger = get_logger(__name__)

_HERMES_EVENT_MAP = {
    "message.delta": RunEventType.MESSAGE_DELTA,
    "assistant.delta": RunEventType.ASSISTANT_DELTA,
    "tool.started": RunEventType.TOOL_STARTED,
    "tool.completed": RunEventType.TOOL_COMPLETED,
    "approval.request": RunEventType.APPROVAL_REQUIRED,
    "approval.responded": RunEventType.APPROVAL_RESPONDED,
    "run.completed": RunEventType.RUN_COMPLETED,
    "run.failed": RunEventType.RUN_FAILED,
    "run.cancelled": RunEventType.RUN_CANCELLED,
}

_CONNECTION_RETRIES = 3
_CONNECTION_RETRY_DELAY = 0.5


def normalize_server_urls(base_url: str) -> tuple[str, str]:
    """Return (server_root, v1_base) accepting either root or /v1 suffixed URL."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3], base
    return base, f"{base}/v1"


def extract_session_id(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    if payload.get("id"):
        return str(payload["id"])
    if payload.get("session_id"):
        return str(payload["session_id"])
    data = payload.get("data")
    if isinstance(data, dict):
        if data.get("id"):
            return str(data["id"])
        if data.get("session_id"):
            return str(data["session_id"])
    session = payload.get("session")
    if isinstance(session, dict):
        if session.get("session_id"):
            return str(session["session_id"])
        if session.get("id"):
            return str(session["id"])
    return ""


class HermesServerError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    def is_not_found(self) -> bool:
        return self.status_code == 404


class HermesServerClient:
    """HTTP client for Hermes Agent API Server (OpenAI-compatible + native runs)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        model: str = "hermes-agent",
        timeout: float = 120.0,
        verify_ssl: bool = True,
        session_key: str | None = None,
    ) -> None:
        self._server_root, self._v1_base = normalize_server_urls(base_url)
        self._api_key = api_key
        self._model = model
        self._session_key = session_key
        self._closed = False
        self._active_session_id: str | None = None
        self._client = httpx.AsyncClient(
            base_url=self._server_root,
            timeout=timeout,
            verify=verify_ssl,
            headers=self._build_headers(),
        )

    @property
    def model(self) -> str:
        return self._model

    @property
    def server_root(self) -> str:
        return self._server_root

    @property
    def v1_base(self) -> str:
        return self._v1_base

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-windows-client/0.1.0",
        }

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._server_root,
            timeout=self._client.timeout,
            verify=self._client._verify,  # noqa: SLF001
            headers=self._build_headers(),
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def __aenter__(self) -> HermesServerClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        last_exc: Exception | None = None
        for attempt in range(_CONNECTION_RETRIES):
            try:
                response = await self._client.request(method, path, json=json_body, params=params)
                if response.status_code >= 400:
                    detail = response.text
                    try:
                        body = response.json()
                        if isinstance(body, dict):
                            detail = body.get("detail", body.get("error", body.get("message", detail)))
                    except Exception:
                        pass
                    raise HermesServerError(str(detail), response.status_code)
                if response.status_code == 204:
                    return {}
                return response.json()
            except httpx.ConnectError as exc:
                last_exc = exc
                if attempt + 1 >= _CONNECTION_RETRIES:
                    raise HermesServerError("All connection attempts failed") from exc
                await asyncio.sleep(_CONNECTION_RETRY_DELAY * (attempt + 1))
            except HermesServerError:
                raise
        if last_exc:
            raise HermesServerError("All connection attempts failed") from last_exc
        return {}

    async def health(self) -> HealthResponse:
        for path in ("/health", "/v1/health"):
            try:
                data = await self._request("GET", path)
                if isinstance(data, dict):
                    return HealthResponse(status=data.get("status", "ok"), raw=data)
                return HealthResponse(status="ok")
            except HermesServerError as exc:
                if exc.status_code == 404:
                    continue
                raise
        return HealthResponse(status="unknown")

    async def get_models(self) -> ModelsResponse:
        data = await self._request("GET", "/v1/models")
        if isinstance(data, list):
            return ModelsResponse(data=[ModelInfo.model_validate(m) for m in data])
        return ModelsResponse.model_validate(data)

    async def get_capabilities(self) -> Capabilities:
        data = await self._request("GET", "/v1/capabilities")
        caps = Capabilities.model_validate(data)
        if caps.model and caps.model not in caps.models:
            caps.models = [caps.model, *caps.models]
        return caps

    async def create_session(self, payload: SessionCreate | None = None) -> Session:
        body = (payload or SessionCreate()).model_dump(exclude_none=True)
        requested_id = body.get("id") or body.get("session_id") or ""
        try:
            data = await self._request("POST", "/api/sessions", json_body=body)
        except HermesServerError as exc:
            if exc.status_code == 404:
                data = await self._request("POST", "/v1/sessions", json_body=body)
            elif exc.status_code == 409 and requested_id:
                return Session(id=requested_id, session_id=requested_id)
            else:
                raise
        session_id = extract_session_id(data if isinstance(data, dict) else {})
        if not session_id and requested_id:
            session_id = requested_id
        return Session(id=session_id, session_id=session_id)

    async def get_session(self, session_id: str) -> Session:
        data = await self._request("GET", f"/api/sessions/{session_id}")
        if not data:
            raise HermesServerError("Session not found", 404)
        sid = extract_session_id(data if isinstance(data, dict) else {})
        if not sid:
            raise HermesServerError("Session not found", 404)
        return Session.model_validate(data if isinstance(data, dict) else {"id": sid})

    async def update_session(self, session_id: str, *, metadata: dict[str, Any]) -> Session | None:
        """Patch session metadata with local tools manifest (best-effort)."""
        try:
            data = await self._request(
                "PATCH",
                f"/api/sessions/{session_id}",
                json_body={"metadata": metadata},
            )
            return Session.model_validate(data)
        except HermesServerError as exc:
            if exc.status_code in (404, 405, 501):
                logger.debug("session_patch_unsupported", status=exc.status_code)
                return None
            raise

    async def chat(self, request: ChatRequest) -> dict[str, Any]:
        body = request.to_openai_body(self._model)
        result = await self._request("POST", "/v1/chat/completions", json_body=body)
        return result if isinstance(result, dict) else {"data": result}

    async def create_run(
        self,
        message: str,
        *,
        session_id: str | None = None,
        instructions: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> Run:
        payload = RunCreate(
            input=message,
            session_id=session_id or None,
            model=self._model,
            instructions=instructions,
        )
        body = payload.model_dump(exclude_none=True)
        if not session_id or not str(session_id).strip():
            body.pop("session_id", None)
        else:
            self._active_session_id = session_id
        if conversation_history:
            body["conversation_history"] = conversation_history
        data = await self._request("POST", "/v1/runs", json_body=body)
        return Run.model_validate(data)

    async def get_run(self, run_id: str) -> Run:
        data = await self._request("GET", f"/v1/runs/{run_id}")
        return Run.model_validate(data)

    async def stop_run(self, run_id: str) -> None:
        try:
            await self._request("POST", f"/v1/runs/{run_id}/stop")
        except HermesServerError as exc:
            if exc.status_code == 404:
                return
            raise

    async def submit_approval(self, response: HermesApprovalSubmit) -> dict[str, Any]:
        result = await self._request(
            "POST",
            f"/v1/runs/{response.run_id}/approval",
            json_body={"choice": response.choice.value},
        )
        return result if isinstance(result, dict) else {"data": result}

    async def stream_events(self, run_id: str) -> AsyncIterator[RunEvent]:
        """Stream run SSE events. Fully consume or aclose before calling client.close()."""
        async with self._client.stream("GET", f"/v1/runs/{run_id}/events") as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise HermesServerError(body.decode(), response.status_code)

            event_name: str | None = None
            data_buffer: list[str] = []

            try:
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_buffer.append(line[5:].strip())
                    elif line == "" and data_buffer:
                        raw = "\n".join(data_buffer)
                        data_buffer.clear()
                        for event in self._parse_sse_payload(raw, event_name):
                            event.run_id = run_id
                            yield event
                        event_name = None

                if data_buffer:
                    raw = "\n".join(data_buffer)
                    for event in self._parse_sse_payload(raw, event_name):
                        event.run_id = run_id
                        yield event
            except GeneratorExit:
                raise

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[RunEvent]:
        body = request.to_openai_body(self._model)
        body["stream"] = True
        async for event in self._stream_sse("POST", "/v1/chat/completions", json_body=body):
            yield event

    async def session_chat_stream(self, session_id: str, message: str) -> AsyncIterator[RunEvent]:
        async for event in self._stream_sse(
            "POST",
            f"/api/sessions/{session_id}/chat/stream",
            json_body={"input": message},
        ):
            yield event

    async def _stream_sse(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> AsyncIterator[RunEvent]:
        async with self._client.stream(method, path, json=json_body) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise HermesServerError(body.decode(), response.status_code)

            event_name: str | None = None
            data_buffer: list[str] = []

            try:
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_buffer.append(line[5:].strip())
                    elif line == "" and data_buffer:
                        raw = "\n".join(data_buffer)
                        data_buffer.clear()
                        for event in self._parse_sse_payload(raw, event_name):
                            yield event
                        event_name = None

                if data_buffer:
                    raw = "\n".join(data_buffer)
                    for event in self._parse_sse_payload(raw, event_name):
                        yield event
            except GeneratorExit:
                raise

    def _parse_sse_payload(self, raw: str, event_name: str | None) -> list[RunEvent]:
        if raw.strip() == "[DONE]":
            return [RunEvent(type=RunEventType.DONE, data={})]

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [RunEvent(type=RunEventType.MESSAGE, data={"content": raw})]

        if not isinstance(parsed, dict):
            return [RunEvent(type=RunEventType.STATUS, data={"value": parsed})]

        if "choices" in parsed:
            delta = parsed["choices"][0].get("delta", {})
            content = delta.get("content", "")
            if content:
                return [RunEvent(type=RunEventType.MESSAGE, data={"content": content})]
            return []

        event_type = event_name or parsed.get("event") or parsed.get("type") or "status"
        mapped = _HERMES_EVENT_MAP.get(str(event_type))
        if mapped:
            return [RunEvent(type=mapped, data=parsed)]

        try:
            return [RunEvent(type=RunEventType(event_type), data=parsed)]
        except ValueError:
            return [RunEvent(type=RunEventType.STATUS, data={"event": event_type, **parsed})]


def ensure_client_config() -> str:
    """Ensure user default.yaml exists; return resolved config path as string."""
    from pathlib import Path

    from hermes.config.paths import resolve_config_path
    from hermes.config.settings_store import bootstrap_user_config

    bootstrap_user_config()
    return str(resolve_config_path())
