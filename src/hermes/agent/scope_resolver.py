from __future__ import annotations

import re
from enum import StrEnum


class ExecutionScope(StrEnum):
    CLIENT = "client"
    SERVER = "server"


_SERVER_SIGNALS = (
    "vps",
    "sunucu",
    "sunucuda",
    "server",
    "remote server",
    "uzak sunucu",
)

_CLIENT_SIGNALS = (
    "bilgisayarimda",
    "bilgisayarımda",
    "pc'de",
    "pc de",
    "masaustunde",
    "masaüstünde",
    "masaüstüne",
    "masaustune",
    "windows'ta",
    "windows ta",
    "yerel",
    "client",
)


def resolve_execution_scope(message: str) -> ExecutionScope:
    """Default CLIENT unless user explicitly asks for server/VPS work."""
    lower = (message or "").casefold()
    if any(signal in lower for signal in _SERVER_SIGNALS):
        if re.search(r"\b(vps|sunucu|server)\b", lower):
            return ExecutionScope.SERVER
    return ExecutionScope.CLIENT
