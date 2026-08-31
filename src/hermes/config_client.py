from __future__ import annotations

DEFAULT_SERVER_URL = "http://50.6.226.228:8642"
DEFAULT_MODEL = "hermes-agent"


def default_server_url() -> str:
    return DEFAULT_SERVER_URL


def apply_server_defaults(payload: dict | None = None) -> dict:
    """Merge default server URL and model into a config payload."""
    data = dict(payload or {})
    server = dict(data.get("server") or {})
    if not server.get("url"):
        server["url"] = DEFAULT_SERVER_URL
    if not server.get("model"):
        server["model"] = DEFAULT_MODEL
    data["server"] = server
    return data
