from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.utils.logging import get_logger

logger = get_logger(__name__)


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyResult:
    __slots__ = ("decision", "reason", "risk_level")

    def __init__(
        self,
        decision: PolicyDecision,
        reason: str = "",
        risk_level: RiskLevel = RiskLevel.READ_ONLY,
    ) -> None:
        self.decision = decision
        self.reason = reason
        self.risk_level = risk_level

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyDecision.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.decision == PolicyDecision.REQUIRE_APPROVAL


DENIED_TOOL_PATTERNS = [
    r"^registry_delete",
    r"^format_disk",
    r"^delete_user",
    r"^disable_firewall",
    r"^shutdown_system",
    r"^wipe_",
]

READ_ONLY_TOOLS = {
    "get_system_info",
    "get_disk_info",
    "list_installed_programs",
    "get_network_config",
    "ping_host",
    "dns_lookup",
    "traceroute",
    "check_port",
    "list_processes",
    "list_services",
    "get_service_status",
    "query_event_log",
    "read_registry",
    "echo",
    "screenshot",
}

LOW_RISK_TOOLS = {
    "open_app",
    "open_url",
    "focus_window",
}

INTERACTIVE_TOOLS = {
    "type_text",
    "press_keys",
    "click",
    "move_mouse",
}

HIGH_RISK_TOOLS = {
    "run_command",
    "install_program",
    "uninstall_program",
    "modify_registry",
    "modify_network",
    "delete_file",
    "kill_process",
    "modify_service",
    "modify_firewall",
}

NORMAL_MODIFICATION_TOOLS = {
    "write_file",
    "copy_file",
    "move_file",
    "create_folder",
    "launch_program",
    "keyboard_shortcut",
    "scroll",
    *INTERACTIVE_TOOLS,
}

SENSITIVE_FIELD_KEYWORDS = (
    "password",
    "sifre",
    "şifre",
    "secret",
    "token",
    "api_key",
    "credential",
    "pin",
    "otp",
    "parola",
    "cvv",
)

DESTRUCTIVE_TEXT_PATTERNS = [
    re.compile(r"format\s+[a-z]:", re.IGNORECASE),
    re.compile(r"rm\s+-rf", re.IGNORECASE),
    re.compile(r"del\s+/f\s+/s", re.IGNORECASE),
    re.compile(r"Remove-Item\s+.+\s+-Recurse\s+-Force", re.IGNORECASE),
    re.compile(r"shutdown\s+/s", re.IGNORECASE),
    re.compile(r"Stop-Computer", re.IGNORECASE),
    re.compile(r"Clear-Disk", re.IGNORECASE),
    re.compile(r"diskpart", re.IGNORECASE),
]


class PolicyEngine:
    """Local policy gate between Hermes Server tool requests and Windows execution."""

    def __init__(
        self,
        require_approval_for: list[RiskLevel] | None = None,
    ) -> None:
        self._require_approval_for = require_approval_for or [
            RiskLevel.NORMAL_MODIFICATION,
            RiskLevel.HIGH_RISK,
        ]

    def classify_tool(self, tool_name: str) -> RiskLevel:
        name = tool_name.lower()
        if name in READ_ONLY_TOOLS:
            return RiskLevel.READ_ONLY
        if name in LOW_RISK_TOOLS:
            return RiskLevel.LOW_RISK
        if name in HIGH_RISK_TOOLS or any(
            k in name for k in ("delete", "uninstall", "modify_registry", "firewall")
        ):
            return RiskLevel.HIGH_RISK
        if name in NORMAL_MODIFICATION_TOOLS or any(
            k in name for k in ("write", "move", "copy", "click", "type", "press")
        ):
            return RiskLevel.NORMAL_MODIFICATION
        return RiskLevel.READ_ONLY

    def _evaluate_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
    ) -> PolicyResult | None:
        if not arguments:
            return None

        name = tool_name.lower()
        combined_text = json.dumps(arguments, ensure_ascii=False).lower()

        for pattern in DESTRUCTIVE_TEXT_PATTERNS:
            if pattern.search(combined_text):
                return PolicyResult(
                    PolicyDecision.DENY,
                    reason="Destructive command pattern blocked by local policy.",
                    risk_level=RiskLevel.HIGH_RISK,
                )

        if name == "type_text":
            target_field = str(arguments.get("target_field", "") or arguments.get("field", "")).lower()
            if target_field and any(keyword in target_field for keyword in SENSITIVE_FIELD_KEYWORDS):
                return PolicyResult(
                    PolicyDecision.REQUIRE_APPROVAL,
                    reason=(
                        "UYARI: Sifre/gizli alana yazma istegi algilandi. "
                        "Bu islem icin acik kullanici onayi gerekli."
                    ),
                    risk_level=RiskLevel.NORMAL_MODIFICATION,
                )

            text = str(arguments.get("text", "")).lower()
            if any(keyword in text for keyword in SENSITIVE_FIELD_KEYWORDS):
                return PolicyResult(
                    PolicyDecision.REQUIRE_APPROVAL,
                    reason=(
                        "UYARI: Metin sifre/gizli bilgi iceriyor olabilir. "
                        "Devam etmek icin kullanici onayi gerekli."
                    ),
                    risk_level=RiskLevel.NORMAL_MODIFICATION,
                )

        if name == "press_keys":
            keys = arguments.get("keys") or arguments.get("key") or []
            if isinstance(keys, str):
                keys = [keys]
            normalized = "+".join(str(k).lower() for k in keys)
            if "alt+f4" in normalized.replace(" ", "") and len(keys) <= 2:
                return PolicyResult(
                    PolicyDecision.REQUIRE_APPROVAL,
                    reason="Pencere kapatma kisayolu (Alt+F4) onay gerektirir.",
                    risk_level=RiskLevel.NORMAL_MODIFICATION,
                )

        return None

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        server_risk_level: str | None = None,
    ) -> PolicyResult:
        name = tool_name.lower()

        for pattern in DENIED_TOOL_PATTERNS:
            if re.match(pattern, name):
                return PolicyResult(
                    PolicyDecision.DENY,
                    reason=f"Tool '{tool_name}' is permanently blocked by local policy.",
                    risk_level=RiskLevel.HIGH_RISK,
                )

        argument_result = self._evaluate_arguments(tool_name, arguments)
        if argument_result is not None:
            return argument_result

        if server_risk_level:
            try:
                risk = RiskLevel(server_risk_level)
            except ValueError:
                risk = self.classify_tool(tool_name)
        else:
            risk = self.classify_tool(tool_name)

        if risk == RiskLevel.READ_ONLY:
            return PolicyResult(PolicyDecision.ALLOW, risk_level=risk)

        if risk == RiskLevel.LOW_RISK:
            return PolicyResult(PolicyDecision.ALLOW, risk_level=risk)

        if risk in self._require_approval_for:
            return PolicyResult(
                PolicyDecision.REQUIRE_APPROVAL,
                reason=f"Tool '{tool_name}' requires user approval (risk: {risk.value}).",
                risk_level=risk,
            )

        return PolicyResult(PolicyDecision.ALLOW, risk_level=risk)


class AuditLogger:
    """Append-only audit log with sensitive data redaction."""

    def __init__(self, log_path: str, redact_patterns: list[str] | None = None) -> None:
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._redact_patterns = [p.lower() for p in (redact_patterns or [])]

    def _redact(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                k: "***REDACTED***"
                if any(p in k.lower() for p in self._redact_patterns)
                else self._redact(v)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [self._redact(item) for item in data]
        if isinstance(data, str):
            lower = data.lower()
            for pattern in self._redact_patterns:
                if pattern in lower:
                    return "***REDACTED***"
        return data

    def log(self, event: str, **kwargs: Any) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **self._redact(kwargs),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.debug("audit_log", audit_event=event)
