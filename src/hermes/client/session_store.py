from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import secrets

from hermes.config.paths import client_state_path, ensure_user_dirs


def get_client_id() -> str:
    ensure_user_dirs()
    path = client_state_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            client_id = str(data.get("client_id", "")).strip()
            if client_id:
                return client_id
        except (OSError, json.JSONDecodeError):
            pass
    client_id = str(uuid.uuid4())
    save_client_state({"client_id": client_id})
    return client_id


def load_session_id() -> str | None:
    path = client_state_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        session_id = str(data.get("session_id", "")).strip()
        return session_id or None
    except (OSError, json.JSONDecodeError):
        return None


def save_session_id(session_id: str) -> None:
    session_id = (session_id or "").strip()
    if not session_id:
        return
    state = load_client_state()
    state["client_id"] = str(state.get("client_id") or get_client_id()).strip() or get_client_id()
    state["session_id"] = session_id
    state["session_created_at"] = _utc_now()
    save_client_state(state)


def clear_session_id() -> None:
    """Drop the server session only. Machine client_id stays put."""
    state = load_client_state()
    state.pop("session_id", None)
    state.pop("session_created_at", None)
    state["client_id"] = str(state.get("client_id") or get_client_id()).strip() or get_client_id()
    save_client_state(state)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_session_id(ttl_seconds: int | None = None) -> str | None:
    """Return the stored conversation session id, or None."""
    del ttl_seconds
    return load_session_id()


def new_conversation_id() -> str:
    """Create and persist a new conversation session id. client_id is unchanged."""
    session_id = str(uuid.uuid4())
    save_session_id(session_id)
    return session_id


_MAX_HISTORY = 16


def load_conversation_history() -> list[dict[str, str]]:
    state = load_client_state()
    raw = state.get("conversation_history") or []
    history: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return history
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})
    return history[-_MAX_HISTORY:]


def append_conversation_turn(role: str, content: str) -> None:
    role = (role or "").strip()
    content = (content or "").strip()
    if role not in ("user", "assistant") or not content:
        return
    state = load_client_state()
    history = load_conversation_history()
    history.append({"role": role, "content": content[:4000]})
    state["conversation_history"] = history[-_MAX_HISTORY:]
    state["client_id"] = str(state.get("client_id") or get_client_id()).strip() or get_client_id()
    if state.get("session_id"):
        state["session_id"] = str(state["session_id"])
    save_client_state(state)


def load_client_state() -> dict[str, Any]:
    path = client_state_path()
    if not path.exists():
        return {"client_id": get_client_id()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"client_id": get_client_id()}


def save_client_state(state: dict[str, Any]) -> None:
    ensure_user_dirs()
    path = client_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def load_manifest_tool_count() -> int | None:
    value = load_client_state().get("registered_tool_count")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def save_manifest_tool_count(count: int) -> None:
    state = load_client_state()
    state["registered_tool_count"] = int(count)
    state["client_id"] = str(state.get("client_id") or get_client_id()).strip() or get_client_id()
    save_client_state(state)


def get_or_create_rpc_secret() -> str:
    state = load_client_state()
    secret = str(state.get("rpc_secret") or "").strip()
    if not secret:
        secret = secrets.token_urlsafe(32)
        state["rpc_secret"] = secret
        state["client_id"] = str(state.get("client_id") or get_client_id()).strip() or get_client_id()
        save_client_state(state)
    return secret


def get_rpc_bind() -> tuple[str, int]:
    state = load_client_state()
    host = str(state.get("rpc_host") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(state.get("rpc_port") or 8765)
    except (TypeError, ValueError):
        port = 8765
    return host, port
