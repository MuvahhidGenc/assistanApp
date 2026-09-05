from __future__ import annotations

import asyncio

from hermes.config.settings import RiskLevel
from hermes.tools.verifiers.base import BaseVerifier, VerificationResult, VerificationStatus
from hermes.tools.verifiers.context import VerifierContext
from hermes.tools.verifiers.generic import GenericVerifier
from hermes.tools.verifiers.specific import (
    ClickScreenVerifier,
    CopyFileVerifier,
    CreateFolderVerifier,
    GetSystemInfoVerifier,
    GitCloneVerifier,
    InstallProgramVerifier,
    OpenAppVerifier,
    OpenPathVerifier,
    OpenUrlVerifier,
    ReadFileVerifier,
    ReadScreenTextVerifier,
    ResolveScreenEntityVerifier,
    ScrollScreenVerifier,
    SearchFilesVerifier,
    SetDnsVerifier,
    WriteFileVerifier,
)

READ_ONLY_OBSERVE_TOOLS = frozenset(
    {
        "get_network_config",
        "list_installed_programs",
        "list_directory",
        "get_system_info",
        "list_processes",
        "list_services",
        "read_screen_text",
        "dns_lookup",
        "ping_host",
    }
)


class VerifierRegistry:
    def __init__(self) -> None:
        self._by_tool: dict[str, BaseVerifier] = {}
        self._generic = GenericVerifier()

    def register(self, verifier: BaseVerifier) -> None:
        for name in verifier.tool_names:
            self._by_tool[name] = verifier

    def get(self, tool_name: str) -> BaseVerifier | None:
        return self._by_tool.get(tool_name)

    async def verify(self, ctx: VerifierContext) -> VerificationResult:
        verifier = self.get(ctx.tool_name)
        timeout = ctx.timeout_seconds

        try:
            if verifier is not None:
                result = await asyncio.wait_for(verifier.verify(ctx), timeout=timeout)
            else:
                result = await asyncio.wait_for(self._generic.verify(ctx), timeout=timeout)
        except TimeoutError:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                method=f"{ctx.tool_name}_timeout",
                details={"reason": "verification_timeout", "timeout_seconds": timeout},
            )

        return result


def create_default_verifier_registry() -> VerifierRegistry:
    registry = VerifierRegistry()
    for verifier in (
        GitCloneVerifier(),
        InstallProgramVerifier(),
        CreateFolderVerifier(),
        GetSystemInfoVerifier(),
        WriteFileVerifier(),
        SetDnsVerifier(),
        OpenUrlVerifier(),
        ReadScreenTextVerifier(),
        ResolveScreenEntityVerifier(),
        ClickScreenVerifier(),
        ScrollScreenVerifier(),
        SearchFilesVerifier(),
        CopyFileVerifier(),
        ReadFileVerifier(),
        OpenPathVerifier(),
        OpenAppVerifier(),
    ):
        registry.register(verifier)
    return registry


def is_read_only_observe_tool(tool_name: str, risk_level: RiskLevel | None) -> bool:
    if tool_name in READ_ONLY_OBSERVE_TOOLS:
        return True
    return risk_level == RiskLevel.READ_ONLY
