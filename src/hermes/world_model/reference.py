"""Reference state — the binding between names and entities.

"Bu dosya", "şu pencere", "az önce açtığımız site" — these are references.
The user expects the runtime to keep them stable across turns even as
the underlying entities change. The Reference Resolver turns a reference
key into the entity the runtime should act on, given the current World
Model state.

The resolver is **not** a parser. It does not read user language. It
looks up a name that the reasoning layer has already identified as a
reference key, and returns the entity bound to it. If the binding is
stale, the resolver returns `None` and the reasoning layer must ask the
user or re-observe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_reference_id() -> str:
    import uuid as _uuid

    return f"ref_{_uuid.uuid4().hex[:12]}"


class ReferenceKind(StrEnum):
    FILE = "file"
    FOLDER = "folder"
    URL = "url"
    WINDOW = "window"
    APPLICATION = "application"
    ENTITY = "entity"            # screen entity, OCR token, etc.


@dataclass(frozen=True)
class ReferenceBinding:
    """One name in user space bound to one entity in world state."""

    binding_id: str
    key: str
    kind: ReferenceKind
    value: str
    provenance: str = ""            # how the binding was set (tool, observation, user)
    as_of: str = field(default_factory=_utc_now_iso)
    expires_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def make(
        *,
        key: str,
        kind: ReferenceKind,
        value: str,
        provenance: str = "",
        expires_at: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> "ReferenceBinding":
        return ReferenceBinding(
            binding_id=_new_reference_id(),
            key=key,
            kind=kind,
            value=value,
            provenance=provenance,
            expires_at=expires_at,
            extra=dict(extra or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "key": self.key,
            "kind": self.kind.value,
            "value": self.value,
            "provenance": self.provenance,
            "as_of": self.as_of,
            "expires_at": self.expires_at,
            "extra": dict(self.extra),
        }


class ReferenceResolver:
    """Look up reference bindings by key, with expiry enforcement."""

    def __init__(self, references: dict[str, ReferenceBinding]) -> None:
        self._references = references

    def resolve(self, key: str) -> ReferenceBinding | None:
        """Return the binding for `key`, or None if missing or expired."""
        binding = self._references.get(key)
        if binding is None:
            return None
        if binding.expires_at and binding.expires_at <= _utc_now_iso():
            return None
        return binding

    def all_keys(self) -> tuple[str, ...]:
        return tuple(self._references)