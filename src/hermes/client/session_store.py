from __future__ import annotations

import json
import secrets
import uuid
from pathlib import Path
from typing import Any

from hermes.config.paths import client_state_path, ensure_user_dirs

_MAX_HISTORY = 50


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


def load_session_id() -> str | None:
    session_id = str(load_client_state().get("session_id", "")).strip()
    return session_id or None


def save_session_id(session_id: str) -> None:
    if not session_id or not session_id.strip():
        return
    state = load_client_state()
    state["session_id"] = session_id.strip()
    save_client_state(state)


def ensure_session_id() -> str | None:
    return load_session_id()


def clear_session_id() -> None:
    state = load_client_state()
    state.pop("session_id", None)
    save_client_state(state)


def new_conversation_id() -> str:
    session_id = str(uuid.uuid4())
    save_session_id(session_id)
    return session_id


def _history_path() -> Path:
    return client_state_path().parent / "conversation.json"


def load_conversation_history() -> list[dict[str, str]]:
    path = _history_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_conversation_history(history: list[dict[str, str]]) -> None:
    ensure_user_dirs()
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(history[-_MAX_HISTORY:], handle, indent=2)


def append_conversation_turn(role: str, content: str) -> None:
    history = load_conversation_history()
    history.append({"role": role, "content": content})
    _save_conversation_history(history)


def get_or_create_rpc_secret() -> str:
    state = load_client_state()
    secret = str(state.get("rpc_secret", "")).strip()
    if not secret:
        secret = secrets.token_urlsafe(32)
        state["rpc_secret"] = secret
        save_client_state(state)
    return secret
