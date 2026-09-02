from __future__ import annotations

import re
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    ALREADY_EXISTS = "already_exists"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    AUTHENTICATION_REQUIRED = "authentication_required"
    DEPENDENCY_MISSING = "dependency_missing"
    NETWORK_ERROR = "network_error"
    TOOL_FAILURE = "tool_failure"
    VERIFICATION_FAILURE = "verification_failure"
    UNKNOWN = "unknown"


_TRANSIENT = re.compile(
    r"timeout|timed out|temporarily|busy|retry|connection reset|"
    r"gecici|zaman asim|yogun",
    re.IGNORECASE,
)
_ALREADY_EXISTS = re.compile(
    r"already installed|zaten kurulu|already exists|zaten var|"
    r"dolu|not empty|exists",
    re.IGNORECASE,
)
_PERMISSION = re.compile(
    r"permission|denied|access denied|yonetici|administrator|uac|"
    r"elevation|yetki",
    re.IGNORECASE,
)
_NOT_FOUND = re.compile(
    r"not found|bulunamadi|bulunamadı|does not exist|missing",
    re.IGNORECASE,
)
_AUTH = re.compile(
    r"authentication|auth required|private repo|credential|token|"
    r"401|403 forbidden|ssh key",
    re.IGNORECASE,
)
_NETWORK = re.compile(
    r"network|connect|unreachable|dns|host not found|"
    r"connection refused|all connection attempts failed",
    re.IGNORECASE,
)
_DEPENDENCY = re.compile(
    r"winget not|not recognized|dependency|missing tool|"
    r"git not|command not found",
    re.IGNORECASE,
)


def classify_error(
    *,
    error_text: str = "",
    failure_kind: str = "execution",
    verification_details: dict[str, Any] | None = None,
    tool_name: str = "",
) -> ErrorCategory:
    text = (error_text or "").strip()
    details = verification_details or {}
    reason = str(details.get("reason") or "")

    if failure_kind == "verification":
        if reason in ("git_metadata_missing", "dns_mismatch", "program_not_found", "folder_missing", "file_missing"):
            return ErrorCategory.VERIFICATION_FAILURE
        if reason == "already_installed" or "already" in reason:
            return ErrorCategory.ALREADY_EXISTS
        return ErrorCategory.VERIFICATION_FAILURE

    combined = f"{text} {reason}".strip()
    if not combined:
        return ErrorCategory.UNKNOWN

    if _AUTH.search(combined):
        return ErrorCategory.AUTHENTICATION_REQUIRED
    if _ALREADY_EXISTS.search(combined):
        return ErrorCategory.ALREADY_EXISTS
    if _PERMISSION.search(combined):
        return ErrorCategory.PERMISSION_DENIED
    if _NETWORK.search(combined):
        return ErrorCategory.NETWORK_ERROR
    if _NOT_FOUND.search(combined):
        return ErrorCategory.NOT_FOUND
    if _DEPENDENCY.search(combined):
        return ErrorCategory.DEPENDENCY_MISSING
    if _TRANSIENT.search(combined):
        return ErrorCategory.TRANSIENT
    if "invalid" in combined.casefold() or "gerekli" in combined.casefold():
        return ErrorCategory.INVALID_INPUT

    if tool_name:
        return ErrorCategory.TOOL_FAILURE
    return ErrorCategory.UNKNOWN
