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


DENIED_TOOL_PATTERNS = (
    r"^registry_delete",
    r"^format_disk",
    r"^delete_user",
    r"^disable_firewall",
    r"^shutdown_system",
    r"^wipe_",
)

READ_ONLY_TOOLS = frozenset(
    {
        "get_system_info",
        "get_service_status",
        "get_network_config",
        "list_processes",
        "read_registry",
        "list_installed_programs",
        "dns_lookup",
        "echo",
        "traceroute",
        "list_services",
        "ping_host",
        "query_event_log",
        "get_disk_info",
        "check_port",
        "screenshot",
        "list_windows",
        "get_clipboard",
        "read_screen_text",
        "resolve_screen_entity",
        "list_directory",
    }
)

LOW_RISK_TOOLS = frozenset(
    {
        "open_app",
        "open_url",
        "open_path",
        "focus_window",
        "set_volume",
        "set_clipboard",
        "show_desktop",
        "scroll",
        "click_text",
        "browser_nav",
    }
)

INTERACTIVE_TOOLS = frozenset({"click", "click_text", "press_keys", "move_mouse", "type_text", "scroll", "browser_nav"})

HIGH_RISK_TOOLS = frozenset(
    {
        "run_command",
        "modify_firewall",
        "modify_network",
        "set_dns",
        "control_service",
        "delete_file",
        "delete_path",
        "modify_service",
        "kill_process",
        "modify_registry",
        "uninstall_program",
        "install_program",
    }
)

NORMAL_MODIFICATION_TOOLS = frozenset(
    {
        "write_file",
        "create_word_document",
        "copy_file",
        "move_file",
        "create_folder",
        "download_file",
        "git_clone",
        "launch_program",
        "keyboard_shortcut",
    }
)

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

DESTRUCTIVE_TEXT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"format\s+[a-z]:",
        r"rm\s+-rf",
        r"del\s+/f\s+/s",
        r"Remove-Item\s+.+\s+-Recurse\s+-Force",
        r"shutdown\s+/s",
        r"Stop-Computer",
        r"Clear-Disk",
        r"diskpart",
    )
)


class PolicyEngine:
    """Local policy gate between Hermes Server tool requests and Windows execution."""

    def __init__(
        self,
        require_approval_for: list[RiskLevel] | None = None,
        *,
        registry: Any | None = None,
    ) -> None:
        self._require_approval_for = require_approval_for or [
            RiskLevel.NORMAL_MODIFICATION,
            RiskLevel.HIGH_RISK,
        ]
        self._registry = registry
        self._declared_risk: dict[str, RiskLevel] | None = None

    def _declared_risk_map(self) -> dict[str, RiskLevel]:
        """Risk as declared by the tool classes themselves.

        The name lists below cannot see new or renamed tools, so a tool that is
        registered but unlisted used to fall through to READ_ONLY and skip
        approval. The declaration is authoritative; the lists remain as the
        fallback for server-side tool names with no local class.
        """
        if self._declared_risk is None:
            try:
                registry = self._registry
                if registry is None:
                    from hermes.tools.registry import create_default_registry

                    registry = create_default_registry()
                self._declared_risk = {
                    name.lower(): risk for name, risk in registry.risk_map().items()
                }
            except Exception:  # pragma: no cover - registry must never break policy
                self._declared_risk = {}
        return self._declared_risk

    def classify_tool(self, tool_name: str) -> RiskLevel:
        name = tool_name.lower()
        declared = self._declared_risk_map().get(name)
        if declared is not None:
            return declared
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
        self, tool_name: str, arguments: dict[str, Any] | None
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
            target_field = str(
                arguments.get("target_field", "") or arguments.get("field", "")
            ).lower()
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
        server_risk_level: str | None = None,
        *,
        runtime: object | None = None,
        envelope_target: str | None = None,
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

        risk = self.classify_tool(tool_name)
        if server_risk_level:
            # A caller-supplied level may raise the bar but never lower it:
            # ToolCallRequest defaults to read_only, which would otherwise let
            # any caller walk a high-risk tool past the approval gate.
            try:
                claimed = RiskLevel(server_risk_level)
            except ValueError:
                claimed = risk
            if claimed.severity > risk.severity:
                risk = claimed

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
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._path = p
        self._redact_patterns = [item.lower() for item in (redact_patterns or [])]

    def _redact(self, data: Any) -> Any:
        if isinstance(data, dict):
            return {
                k: (
                    "***REDACTED***"
                    if any(p in str(k).lower() for p in self._redact_patterns)
                    else self._redact(v)
                )
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
