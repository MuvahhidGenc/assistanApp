from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any

import yaml

from hermes.config.paths import (
    bundled_config_path,
    ensure_user_dirs,
    portable_config_path,
    user_config_path,
)
from hermes.config.settings import normalize_model

DEFAULT_SERVER_URL = "http://50.6.226.228:8642"
DEFAULT_MODEL = "hermes-agent"


def default_server_url() -> str:
    return DEFAULT_SERVER_URL


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def load_client_yaml() -> dict[str, Any]:
    for candidate in (
        user_config_path(),
        portable_config_path(),
        bundled_config_path(),
        Path("config/default.yaml"),
    ):
        if candidate is None:
            continue
        data = _read_yaml(candidate)
        if data:
            return data
    return {}


def apply_server_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    server = payload.setdefault("server", {})
    if not str(server.get("url") or "").strip():
        server["url"] = DEFAULT_SERVER_URL
    if not str(server.get("model") or "").strip():
        server["model"] = normalize_model(DEFAULT_MODEL)
    server["url"] = str(server["url"]).strip()
    return payload


def _dump_yaml_bytes(payload: dict[str, Any]) -> bytes:
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).encode("utf-8")


def _normalize_line_endings(data: bytes) -> bytes:
    """Normalize Windows CRLF ↔ Unix LF so idempotency checks compare
    *semantic* equality rather than byte-for-byte line endings.

    On Windows users may open ``default.yaml`` in Notepad, which rewrites
    every newline as ``\\r\\n``.  ``yaml.safe_dump`` always emits ``\\n``.
    Without normalisation we would rewrite the file on every launch — the
    exact behaviour that causes the production ``PermissionError(13)``.
    """
    return data.replace(b"\r\n", b"\n")


def write_yaml(path: Path, payload: dict[str, Any]) -> Path:
    """Windows-safe, idempotent YAML writer.

    Root-cause fix for ``PermissionError: [Errno 13]``:
    1. If the target file already exists and its *normalised* byte content
       is identical (CRLF vs LF ignored), **do not write anything at all**
       — skip the unnecessary overwrite that triggers Defender / shared-
       handle / Explorer-preview lock contention.
    2. Otherwise write to a uniquely-named temp file in the same directory
       and atomically ``os.replace`` it over the target. On Windows this
       avoids the truncate-write window where a concurrent reader or AV
       scanner causes Errno 13.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new_bytes = _dump_yaml_bytes(payload)
    norm_new = _normalize_line_endings(new_bytes)

    # ── Idempotency shortcut ──────────────────────────────────────────
    if path.exists() and path.is_file():
        try:
            existing_bytes = path.read_bytes()
            if _normalize_line_endings(existing_bytes) == norm_new:
                return path
        except OSError:
            pass

    # ── Temp-file atomic write ────────────────────────────────────────
    suffix = secrets.token_hex(4)
    tmp = path.with_name(f"{path.name}.__{os.getpid()}.{suffix}.tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(new_bytes)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.replace(tmp, path)
        except OSError:
            shutil.move(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def ensure_client_config() -> Path:
    """Create OR ensure user config has valid defaults — write only when needed.

    Previously this function rewrote ``%LOCALAPPDATA%\\HermesClient\\config\\default.yaml``
    on every single application launch, even when the file already contained
    correct defaults. On Windows that triggers transient share locks from
    Explorer, antivirus scanners or lingering prior processes and causes
    the production tray EXE to abort with ``PermissionError: [Errno 13]``.

    The fix: read the existing file, apply defaults in memory, and delegate
    to ``write_yaml`` which performs byte-for-byte idempotency checking.
    If nothing changed on disk we perform zero filesystem writes.
    """
    ensure_user_dirs()
    path = user_config_path()
    payload = _read_yaml(path) if path.exists() else load_client_yaml()
    apply_server_defaults(payload)
    return write_yaml(path, payload)
