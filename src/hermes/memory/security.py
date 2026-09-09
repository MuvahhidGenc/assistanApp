"""Security and durability boundary shared by persistent memory stores."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


class MemorySecurityError(ValueError):
    """Memory content could not be made safe for persistence."""


class MemoryPersistenceError(OSError):
    """A memory file could not be durably read or written."""


_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "password",
    "passphrase",
    "api_key",
    "apikey",
    "token",
    "secret",
    "private_key",
    "credential",
    "credentials",
    "ssh_key",
}
_ASSIGNMENT = re.compile(
    r"(?P<key>password|passphrase|api[_-]?key|token|secret|credential(?:s)?|"
    r"private[_ -]?key|ssh[_-]?key)\s*[:=]\s*(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_NATURAL_ASSIGNMENT = re.compile(
    r"(?P<key>password|passphrase|api[_ -]?key|token|secret|credential(?:s)?|"
    r"private[_ -]?key|ssh[_ -]?key)\s+(?:is|is:|şudur|budur)\s+"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_TOKEN_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bxox[abp]-[A-Za-z0-9-]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*\b", re.IGNORECASE),
)


def is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
    return any(marker in normalized for marker in _SENSITIVE_KEYS)


def scrub_text(value: str) -> str:
    """Redact credential-shaped material and verify the result is safe."""
    text = str(value)
    text = _ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}={_REDACTED}",
        text,
    )
    text = _NATURAL_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}={_REDACTED}",
        text,
    )
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    if contains_sensitive_data(text):
        raise MemorySecurityError("Sensitive value remained after memory scrubbing")
    return text


def scrub_data(value: Any) -> Any:
    """Recursively scrub data at the final persistence boundary."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            safe[key_text] = _REDACTED if is_sensitive_key(key_text) else scrub_data(item)
        return safe
    if isinstance(value, list):
        return [scrub_data(item) for item in value]
    if isinstance(value, tuple):
        return [scrub_data(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_text(str(value))


def contains_sensitive_data(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (is_sensitive_key(str(key)) and item != _REDACTED)
            or contains_sensitive_data(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_data(item) for item in value)
    if not isinstance(value, str):
        return False
    for match in _ASSIGNMENT.finditer(value):
        if match.group("value") != _REDACTED:
            return True
    for match in _NATURAL_ASSIGNMENT.finditer(value):
        if match.group("value") != _REDACTED:
            return True
    return any(pattern.search(value) is not None for pattern in _TOKEN_PATTERNS)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Scrub, validate, fsync and atomically replace one memory file."""
    safe_payload = scrub_data(payload)
    if contains_sensitive_data(safe_payload):
        raise MemorySecurityError("Refusing to persist sensitive memory data")
    encoded = json.dumps(safe_payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise MemoryPersistenceError(f"Could not persist memory file {path}") from exc


def read_json_file(path: Path) -> dict[str, Any]:
    """Read one memory file or fail closed on corruption/unsafe content."""
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryPersistenceError(f"Could not read memory file {path}") from exc
    if not isinstance(payload, dict):
        raise MemoryPersistenceError(f"Memory file {path} must contain an object")
    if contains_sensitive_data(payload):
        raise MemorySecurityError(f"Memory file {path} contains sensitive data")
    return payload


__all__ = [
    "MemoryPersistenceError",
    "MemorySecurityError",
    "atomic_write_json",
    "contains_sensitive_data",
    "is_sensitive_key",
    "read_json_file",
    "scrub_data",
    "scrub_text",
]
