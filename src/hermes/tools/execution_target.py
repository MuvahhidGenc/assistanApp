from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes.tools.registry import ToolRegistry


class ExecutionTarget(StrEnum):
    CLIENT = "client"
    SERVER = "server"


# Tools that may run only on Hermes VPS/backend (none in default Windows client registry).
SERVER_ONLY_TOOLS: frozenset[str] = frozenset()

_SERVER_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bvps\b", re.IGNORECASE),
    re.compile(r"\bvps['']?te\b", re.IGNORECASE),
    re.compile(r"\bsunucuda\b", re.IGNORECASE),
    re.compile(r"\bsunucu(?:da|da)\b", re.IGNORECASE),
    re.compile(r"\bserverda\b", re.IGNORECASE),
    re.compile(r"\bserver['']?da\b", re.IGNORECASE),
    re.compile(r"\bserver(?:\s+)?(?:side|taraf(?:ı|i)?)\b", re.IGNORECASE),
)

_CLIENT_SCOPE_SIGNALS: tuple[str, ...] = (
    "masaust",
    "masaüst",
    "masa ust",
    "desktop",
    "bilgisayar",
    "pc'm",
    "pcm",
    "pcde",
    "pc'de",
    "windows'ta",
    "windows ta",
    "yerel",
    "local",
)


def user_requested_server_execution(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _SERVER_SCOPE_PATTERNS)


def user_context_implies_client(text: str) -> bool:
    lower = (text or "").casefold()
    return any(signal in lower for signal in _CLIENT_SCOPE_SIGNALS)


def canonical_execution_target(tool_name: str, registry: ToolRegistry) -> ExecutionTarget:
    if tool_name in SERVER_ONLY_TOOLS:
        return ExecutionTarget.SERVER
    if registry.get(tool_name) is not None:
        definition = registry.get(tool_name).get_definition()
        return definition.execution_target
    return ExecutionTarget.CLIENT


def infer_message_execution_target(text: str) -> ExecutionTarget:
    """Default client; explicit VPS/server wording selects server scope."""
    if user_requested_server_execution(text):
        return ExecutionTarget.SERVER
    return ExecutionTarget.CLIENT


def resolve_requested_execution_target(
    tool_name: str,
    registry: ToolRegistry,
    *,
    user_message: str = "",
    envelope_target: str | None = None,
) -> ExecutionTarget:
    """
    Resolve where a tool must run. Registry canonical target wins over AI envelope.
    User message can force client scope for local PC intents (e.g. Masaüstünde).
    """
    canonical = canonical_execution_target(tool_name, registry)
    if user_context_implies_client(user_message):
        return ExecutionTarget.CLIENT
    if envelope_target:
        try:
            requested = ExecutionTarget(str(envelope_target).strip().lower())
        except ValueError:
            requested = ExecutionTarget.CLIENT
        if requested == ExecutionTarget.SERVER and canonical == ExecutionTarget.CLIENT:
            return ExecutionTarget.CLIENT
        if requested == ExecutionTarget.CLIENT and canonical == ExecutionTarget.SERVER:
            return ExecutionTarget.SERVER
    if user_requested_server_execution(user_message) and canonical == ExecutionTarget.SERVER:
        return ExecutionTarget.SERVER
    return canonical


def validate_runtime_execution(
    tool_name: str,
    registry: ToolRegistry,
    *,
    runtime: ExecutionTarget,
    user_message: str = "",
    envelope_target: str | None = None,
) -> tuple[bool, str]:
    """Return (allowed, reason). Enforced before PolicyEngine."""
    required = resolve_requested_execution_target(
        tool_name,
        registry,
        user_message=user_message,
        envelope_target=envelope_target,
    )
    if runtime == ExecutionTarget.CLIENT and required == ExecutionTarget.SERVER:
        return False, f"{tool_name} is a server-only tool and cannot run on the Windows client."
    if runtime == ExecutionTarget.SERVER and required == ExecutionTarget.CLIENT:
        return False, f"{tool_name} is a client-only tool and cannot run on the server/VPS."
    if runtime != required:
        return False, (
            f"{tool_name} execution_target mismatch: runtime={runtime.value}, "
            f"required={required.value}"
        )
    return True, ""
