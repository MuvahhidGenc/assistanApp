from __future__ import annotations

import asyncio
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Awaitable
from urllib.parse import unquote, urlparse

from hermes.server.models import ToolCallRequest, ToolResultPayload
from hermes.tools.executor import ToolExecutor
from hermes.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_RPC_HOST = "127.0.0.1"
DEFAULT_RPC_PORT = 8765
_TOOL_PATH = re.compile(r"^/v1/tool/(?P<name>[a-z0-9_]+)/?$", re.IGNORECASE)


ExecuteFn = Callable[[str, dict[str, Any]], Awaitable[ToolResultPayload]]


class LocalToolRpcServer:
    """Localhost HTTP RPC for Hermes VPS → Windows client tool execution."""

    def __init__(
        self,
        executor: ToolExecutor,
        loop: asyncio.AbstractEventLoop,
        *,
        host: str = DEFAULT_RPC_HOST,
        port: int = DEFAULT_RPC_PORT,
        secret: str = "",
        tool_timeout: float = 600.0,
    ) -> None:
        self._executor = executor
        self._loop = loop
        self._host = host
        self._port = port
        self._secret = secret
        self._tool_timeout = tool_timeout
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._httpd:
            host, port = self._httpd.server_address[:2]
            return str(host), int(port)
        return self._host, self._port

    def start(self) -> None:
        if self._httpd is not None:
            return
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                logger.debug("rpc_http", message=format % args)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON body: {exc}") from exc
                return parsed if isinstance(parsed, dict) else {}

            def _authorized(self) -> bool:
                if not server_ref._secret:
                    return True
                header = self.headers.get("X-Secret") or self.headers.get("x-secret") or ""
                auth = self.headers.get("Authorization") or ""
                if auth.lower().startswith("bearer "):
                    header = header or auth[7:].strip()
                return secrets.compare_digest(header.strip(), server_ref._secret)

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "Unauthorized"})
                    return
                path = urlparse(self.path).path
                if path in ("/v1/health", "/health"):
                    self._send(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "status": "ready",
                                "host": server_ref._host,
                                "port": server_ref._port,
                            },
                        },
                    )
                    return
                if path in ("/v1/tools", "/tools"):
                    from hermes.tools.registry import create_default_registry

                    registry = create_default_registry()
                    names = sorted(definition.name for definition in registry.list_tools())
                    self._send(200, {"ok": True, "result": {"tools": names, "count": len(names)}})
                    return
                self._send(404, {"ok": False, "error": "Not found"})

            def do_POST(self) -> None:
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "Unauthorized"})
                    return
                path = urlparse(self.path).path
                match = _TOOL_PATH.match(path)
                if not match:
                    self._send(404, {"ok": False, "error": f"Unknown endpoint: {path}"})
                    return
                tool_name = unquote(match.group("name"))
                try:
                    body = self._read_json()
                except ValueError as exc:
                    self._send(400, {"ok": False, "error": str(exc)})
                    return
                args = body.get("args") or body.get("arguments") or {}
                if not isinstance(args, dict):
                    self._send(400, {"ok": False, "error": "args must be an object"})
                    return
                try:
                    payload = server_ref._run_tool(tool_name, args)
                except Exception as exc:
                    logger.exception("rpc_tool_failed", tool=tool_name)
                    self._send(500, {"ok": False, "error": str(exc)})
                    return
                if payload.success:
                    self._send(
                        200,
                        {
                            "ok": True,
                            "result": {
                                "success": True,
                                "output": payload.output,
                                "tool": tool_name,
                            },
                        },
                    )
                else:
                    self._send(
                        200,
                        {
                            "ok": False,
                            "error": payload.error or "tool failed",
                            "result": {"success": False, "output": payload.output, "tool": tool_name},
                        },
                    )

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="hermes-rpc",
            daemon=True,
        )
        self._thread.start()
        host, port = self.address
        logger.info("rpc_server_started", host=host, port=port)

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None
        logger.info("rpc_server_stopped")

    def _run_tool(self, tool_name: str, arguments: dict[str, Any]) -> ToolResultPayload:
        call = ToolCallRequest(name=tool_name, arguments=arguments)
        future = asyncio.run_coroutine_threadsafe(
            self._executor.execute_tool_call(
                call,
                run_id=f"rpc-{tool_name}",
                skip_approval=True,
            ),
            self._loop,
        )
        return future.result(timeout=self._tool_timeout)
