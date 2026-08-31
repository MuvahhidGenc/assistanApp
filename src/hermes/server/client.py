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

MESSAGE_DELTA = "message.delta"
ASSISTANT_DELTA = "assistant.delta"
TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"
APPROVAL_REQUIRED = "approval.request"
APPROVAL_RESPONDED = "approval.responded"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_CANCELLED = "run.cancelled"

_HERMES_EVENT_MAP = {
    MESSAGE_DELTA: RunEventType.MESSAGE_DELTA,
    ASSISTANT_DELTA: RunEventType.ASSISTANT_DELTA,
    TOOL_STARTED: RunEventType.TOOL_STARTED,
    TOOL_COMPLETED: RunEventType.TOOL_COMPLETED,
    APPROVAL_REQUIRED: RunEventType.APPROVAL_REQUIRED,
    APPROVAL_RESPONDED: RunEventType.APPROVAL_RESPONDED,
    RUN_COMPLETED: RunEventType.RUN_COMPLETED,
    RUN_FAILED: RunEventType.RUN_FAILED,
    RUN_CANCELLED: RunEventType.RUN_CANCELLED,
}


def normalize_server_urls(base_url: str) -> tuple[str, str]:
    """Return (server_root, v1_base) accepting either root or /v1 suffixed URL."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base[:-3], base
    return base, f"{base}/v1"


class HermesServerError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    def is_not_found(self) -> bool:
        text = str(self).lower()
        return self.status_code == 404 or "not_found" in text or "not found" in text

    def is_connection_error(self) -> bool:
        text = str(self).lower()
        return any(
            token in text
            for token in (
                "all connection attempts failed",
                "connecterror",
                "connect timeout",
                "connection refused",
                "10061",
                "reddetti",
                "network is unreachable",
                "timed out",
            )
        )

    def is_auth_error(self) -> bool:
        text = str(self).lower()
        return (
            self.status_code in (401, 403)
            or "invalid_api_key" in text
            or "invalid api key" in text
        )

    def is_session_key_rejected(self) -> bool:
        text = str(self).lower()
        return self.status_code == 403 and (
            "session-key" in text or "session key" in text or "x-hermes-session-key" in text
        )


def extract_session_id(data: Any) -> str:
    if isinstance(data, str) and data.strip():
        return data.strip()
    if not isinstance(data, dict):
        return ""
    for key in ("id", "session_id"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for nested_key in ("session", "data", "result"):
        found = extract_session_id(data.get(nested_key))
        if found:
            return found
    return ""


class HermesServerClient:
    """HTTP client for Hermes Agent API Server (OpenAI-compatible + native runs)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "hermes-agent",
        timeout: float = 120.0,
        verify_ssl: bool = True,
        session_key: str | None = None,
    ) -> None:
        self._server_root, self._v1_base = normalize_server_urls(base_url)
        self._api_key = api_key
        self._model = model
        # Do not send X-Hermes-Session-Key: Hermes 0.20.6 returns 403 when
        # API_SERVER_KEY is unset. Conversation continuity uses session_id +
        # X-Hermes-Session-Id. api.server_key is the Bearer token, not this header.
        del session_key
        self._session_key = None
        self._active_session_id: str | None = None
        self._closed = False
        self._timeout = httpx.Timeout(timeout, connect=5.0)
        self._verify_ssl = verify_ssl
        self._client = self._make_client()

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._server_root,
            timeout=self._timeout,
            verify=self._verify_ssl,
            headers=self._build_headers(),
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=1, local_address="0.0.0.0"),
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

    def set_active_session(self, session_id: str | None) -> None:
        self._active_session_id = (session_id or "").strip() or None
        if not self._closed:
            self._client.headers.update(self._build_headers())

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "hermes-windows-client/0.1.0",
        }
        if self._session_key:
            headers["X-Hermes-Session-Key"] = self._session_key
        if self._active_session_id:
            headers["X-Hermes-Session-Id"] = self._active_session_id
        return headers

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
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._client.request(method, path, json=json_body, params=params)
                if response.status_code >= 400:
                    detail = response.text
                    try:
                        body = response.json()
                        if isinstance(body, dict):
                            detail = body.get(
                                "detail", body.get("error", body.get("message", detail))
                            )
                    except Exception:
                        pass
                    error = HermesServerError(str(detail), response.status_code)
                    if error.is_session_key_rejected() and self._session_key:
                        logger.warning("session_key_header_disabled", error=str(error))
                        self._session_key = None
                        if not self._closed:
                            await self._client.aclose()
                            self._client = self._make_client()
                        continue
                    raise error
                if response.status_code == 204:
                    return {}
                try:
                    return response.json()
                except json.JSONDecodeError:
                    text = (response.text or "").strip()
                    if not text:
                        return {}
                    raise HermesServerError("invalid json response", response.status_code)
            except HermesServerError:
                raise
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TransportError) as exc:
                last_error = exc
                logger.warning(
                    "http_retry",
                    method=method,
                    path=path,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt == 0:
                    await asyncio.sleep(0.4)
                if self._closed:
                    break
        raise HermesServerError(str(last_error or "All connection attempts failed"))

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
        requested = extract_session_id(body)
        last_error: HermesServerError | None = None
        for path in ("/api/sessions", "/v1/sessions"):
            try:
                data = await self._request("POST", path, json_body=body)
                session_id = extract_session_id(data) or requested
                if not session_id:
                    last_error = HermesServerError("session not found", 404)
                    continue
                payload_dict = data if isinstance(data, dict) else {}
                return Session.model_validate({**payload_dict, "id": session_id})
            except HermesServerError as exc:
                last_error = exc
                if exc.status_code == 409 or "session_exists" in str(exc).lower():
                    return Session(id=requested)
                if exc.is_auth_error() or exc.is_connection_error():
                    raise
                if exc.status_code in (404, 405, 501) or exc.is_not_found():
                    continue
                continue
        if last_error and (last_error.is_connection_error() or last_error.is_auth_error()):
            raise last_error
        return Session(id=requested)

    async def get_session(self, session_id: str) -> Session:
        last_error: HermesServerError | None = None
        for path in (f"/api/sessions/{session_id}", f"/v1/sessions/{session_id}"):
            try:
                data = await self._request("GET", path)
                found = extract_session_id(data)
                if not found:
                    raise HermesServerError("session not found", 404)
                payload_dict = data if isinstance(data, dict) else {"id": found}
                payload_dict["id"] = found
                return Session.model_validate(payload_dict)
            except HermesServerError as exc:
                last_error = exc
                if exc.status_code in (404, 405, 501) or exc.is_not_found():
                    continue
                raise
        raise last_error or HermesServerError("session not found", 404)

    async def update_session(self, session_id: str, metadata: dict[str, Any]) -> Session | None:
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
        if isinstance(result, dict):
            return result
        return {"data": result}

    async def create_run(
        self,
        message: str,
        session_id: str | None = None,
        instructions: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> Run:
        session_id = (session_id or "").strip() or None
        if session_id:
            self.set_active_session(session_id)
        payload = RunCreate(
            input=message,
            session_id=session_id,
            model=self._model,
            instructions=instructions,
            conversation_history=conversation_history or None,
        )
        data = await self._request(
            "POST",
            "/v1/runs",
            json_body=payload.model_dump(exclude_none=True),
        )
        return Run.model_validate(data)

    async def get_run(self, run_id: str) -> Run:
        data = await self._request("GET", f"/v1/runs/{run_id}")
        return Run.model_validate(data)

    async def stop_run(self, run_id: str) -> None:
        try:
            await self._request("POST", f"/v1/runs/{run_id}/stop")
        except HermesServerError as exc:
            if exc.is_not_found():
                logger.debug("stop_run_not_found", run_id=run_id)
                return
            raise

    async def submit_approval(self, response: HermesApprovalSubmit) -> dict[str, Any]:
        result = await self._request(
            "POST",
            f"/v1/runs/{response.run_id}/approval",
            json_body={"choice": response.choice.value},
        )
        if isinstance(result, dict):
            return result
        return {"data": result}

    async def stream_events(self, run_id: str) -> AsyncIterator[RunEvent]:
        """Stream run SSE events. Fully consume or aclose before calling client.close()."""
        try:
            async with self._client.stream("GET", f"/v1/runs/{run_id}/events") as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise HermesServerError(body.decode(), response.status_code)
                event_name: str | None = None
                data_buffer: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        data_buffer.append(line[5:].strip())
                        continue
                    if line == "" and data_buffer:
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
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TransportError) as exc:
            raise HermesServerError(str(exc)) from exc

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
        json_body: dict[str, Any] | None = None,
    ) -> AsyncIterator[RunEvent]:
        try:
            async with self._client.stream(method, path, json=json_body) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise HermesServerError(body.decode(), response.status_code)
                event_name: str | None = None
                data_buffer: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if line.startswith("data:"):
                        data_buffer.append(line[5:].strip())
                        continue
                    if line == "" and data_buffer:
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
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TransportError) as exc:
            raise HermesServerError(str(exc)) from exc

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
    from hermes.config.paths import resolve_config_path
    from hermes.config.settings_store import bootstrap_user_config

    bootstrap_user_config()
    return str(resolve_config_path())
