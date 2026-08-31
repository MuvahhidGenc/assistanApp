from __future__ import annotations

import asyncio
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from hermes.server.models import ToolCallRequest
from hermes.tools.executor import ToolExecutor
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class LocalToolRpcServer:
    """Minimal HTTP RPC server exposing local tools to Hermes Server."""

    def __init__(
        self,
        executor: ToolExecutor,
        loop: asyncio.AbstractEventLoop,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        secret: str = "",
    ) -> None:
        self._executor = executor
        self._loop = loop
        self._host = host
        self._port = port
        self._secret = secret
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        if self._httpd:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                logger.debug("rpc_http", message=format % args)

            def do_POST(self) -> None:  # noqa: N802
                secret = self.headers.get("X-Secret", "")
                if secret != server._secret:
                    self.send_response(401)
                    self.end_headers()
                    return

                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    body = {}

                path = urlparse(self.path).path
                if not path.startswith("/v1/tool/"):
                    self.send_response(404)
                    self.end_headers()
                    return

                tool_name = path.split("/v1/tool/", 1)[1].strip("/")
                args = body.get("args") or body.get("arguments") or {}

                future = asyncio.run_coroutine_threadsafe(
                    server._execute(tool_name, args),
                    server._loop,
                )
                try:
                    payload = future.result(timeout=120)
                except Exception as exc:
                    payload = {"ok": False, "error": str(exc)}

                data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        logger.info("rpc_server_started", host=self._host, port=self._port)

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    async def _execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        call = ToolCallRequest(name=tool_name, arguments=arguments)
        result = await self._executor.execute_tool_call(call, skip_approval=True)
        return {
            "ok": True,
            "result": {
                "success": result.success,
                "output": result.output,
                "error": result.error,
            },
        }
