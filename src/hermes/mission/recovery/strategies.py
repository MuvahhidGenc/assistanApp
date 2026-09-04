from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from hermes.config.settings import RiskLevel
from hermes.mission.recovery.classifier import ErrorCategory
from hermes.mission.recovery.context import RecoveryAction, RecoveryContext, _git_target, _resolve_folder
from hermes.tools.capabilities import fit_arguments


class RecoveryStrategy(ABC):
    strategy_id: str = ""
    applicable_tools: tuple[str, ...] = ()
    applicable_errors: tuple[ErrorCategory, ...] = ()
    max_attempts: int = 2
    risk_level: RiskLevel = RiskLevel.LOW_RISK

    def matches(self, ctx: RecoveryContext) -> bool:
        tool = ctx.step.tool_name or ""
        if self.applicable_tools and tool not in self.applicable_tools:
            return False
        return ctx.error_category in self.applicable_errors

    @abstractmethod
    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        ...


class RetrySameToolStrategy(RecoveryStrategy):
    strategy_id = "retry_same_tool"
    applicable_tools = ()
    applicable_errors = (
        ErrorCategory.TRANSIENT,
        ErrorCategory.NETWORK_ERROR,
        ErrorCategory.TOOL_FAILURE,
    )
    max_attempts = 2
    risk_level = RiskLevel.LOW_RISK

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        tool = ctx.step.tool_name
        if not tool:
            return []
        label = ctx.step.title or tool
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name=tool,
                tool_arguments=dict(ctx.step.tool_arguments),
                user_message=f"{label} basarisiz oldu. Tekrar deniyorum.",
                risk_level=self.risk_level,
            )
        ]


class InstallAlreadyInstalledStrategy(RecoveryStrategy):
    strategy_id = "install_verify_existing"
    applicable_tools = ("install_program",)
    applicable_errors = (ErrorCategory.ALREADY_EXISTS, ErrorCategory.VERIFICATION_FAILURE)
    max_attempts = 1
    risk_level = RiskLevel.READ_ONLY

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        package = str(ctx.step.tool_arguments.get("package") or "")
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="list_installed_programs",
                tool_arguments={"filter_name": package, "limit": 20},
                user_message=f"{package or 'Program'} kurulumu basarisiz oldu. Kurulu mu kontrol ediyorum.",
                risk_level=RiskLevel.READ_ONLY,
                idempotency_key="install_verify",
            )
        ]


class InstallRetryStrategy(RecoveryStrategy):
    strategy_id = "install_retry"
    applicable_tools = ("install_program",)
    applicable_errors = (ErrorCategory.TRANSIENT, ErrorCategory.NETWORK_ERROR, ErrorCategory.TOOL_FAILURE)
    max_attempts = 2

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        package = str(ctx.step.tool_arguments.get("package") or "")
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="install_program",
                tool_arguments=dict(ctx.step.tool_arguments),
                user_message=f"{package or 'Program'} kurulumu basarisiz oldu. Alternatif denemeyi baslatiyorum.",
                risk_level=RiskLevel.HIGH_RISK,
            )
        ]


class GitCloneRetryStrategy(RecoveryStrategy):
    strategy_id = "git_clone_retry"
    applicable_tools = ("git_clone",)
    applicable_errors = (
        ErrorCategory.TRANSIENT,
        ErrorCategory.NETWORK_ERROR,
        ErrorCategory.TOOL_FAILURE,
    )
    max_attempts = 2

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="git_clone",
                tool_arguments=dict(ctx.step.tool_arguments),
                user_message="Git clone basarisiz oldu. Tekrar deniyorum.",
                risk_level=RiskLevel.NORMAL_MODIFICATION,
            )
        ]


class GitCloneCheckExistingStrategy(RecoveryStrategy):
    strategy_id = "git_clone_existing"
    applicable_tools = ("git_clone",)
    applicable_errors = (
        ErrorCategory.ALREADY_EXISTS,
        ErrorCategory.VERIFICATION_FAILURE,
    )
    max_attempts = 1
    risk_level = RiskLevel.READ_ONLY

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="git_clone",
                tool_arguments=dict(ctx.step.tool_arguments),
                user_message="Git clone basarisiz oldu. Hedef klasoru kontrol ediyorum.",
                risk_level=RiskLevel.READ_ONLY,
                idempotency_key="git_existing",
            )
        ]


class GitCloneAltPathStrategy(RecoveryStrategy):
    strategy_id = "git_clone_alt_path"
    applicable_tools = ("git_clone",)
    applicable_errors = (ErrorCategory.ALREADY_EXISTS,)
    max_attempts = 1

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        args = dict(ctx.step.tool_arguments)
        target = str(args.get("target_dir") or "").strip()
        if target:
            alt = f"{target}-recovery"
        else:
            alt = "repo-recovery"
        args["target_dir"] = alt
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="git_clone",
                tool_arguments=args,
                user_message="Hedef klasor dolu. Alternatif klasore klonluyorum.",
                risk_level=RiskLevel.NORMAL_MODIFICATION,
            )
        ]


class CreateFolderParentStrategy(RecoveryStrategy):
    strategy_id = "create_folder_parent"
    applicable_tools = ("create_folder",)
    applicable_errors = (
        ErrorCategory.NOT_FOUND,
        ErrorCategory.DEPENDENCY_MISSING,
        ErrorCategory.TOOL_FAILURE,
        ErrorCategory.VERIFICATION_FAILURE,
    )
    max_attempts = 1

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        raw = str(ctx.step.tool_arguments.get("path") or "Hermes")
        target = _resolve_folder(raw)
        parent = target.parent
        actions: list[RecoveryAction] = []
        if not parent.is_dir():
            actions.append(
                RecoveryAction(
                    strategy_id=f"{self.strategy_id}_mkdir",
                    tool_name="create_folder",
                    tool_arguments={"path": str(parent)},
                    user_message="Klasor olusturma basarisiz. Ust klasoru olusturuyorum.",
                    risk_level=RiskLevel.NORMAL_MODIFICATION,
                )
            )
        actions.append(
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="create_folder",
                tool_arguments=dict(ctx.step.tool_arguments),
                user_message="Klasoru tekrar olusturmayi deniyorum.",
                risk_level=RiskLevel.NORMAL_MODIFICATION,
            )
        )
        return actions


class WriteFileParentStrategy(RecoveryStrategy):
    strategy_id = "write_file_parent"
    applicable_tools = ("write_file",)
    applicable_errors = (
        ErrorCategory.NOT_FOUND,
        ErrorCategory.DEPENDENCY_MISSING,
        ErrorCategory.TOOL_FAILURE,
        ErrorCategory.VERIFICATION_FAILURE,
    )
    max_attempts = 1

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        args = dict(ctx.step.tool_arguments)
        actions: list[RecoveryAction] = []
        raw = str(args.get("path") or "")
        if raw:
            try:
                from hermes.tools.windows.file_tools import resolve_user_path

                parent = resolve_user_path(raw).parent
                if not parent.is_dir():
                    actions.append(
                        RecoveryAction(
                            strategy_id=f"{self.strategy_id}_mkdir",
                            tool_name="create_folder",
                            tool_arguments={"path": str(parent)},
                            user_message="Dosya yazma basarisiz. Ust klasoru olusturuyorum.",
                            risk_level=RiskLevel.NORMAL_MODIFICATION,
                        )
                    )
            except ValueError:
                pass
        actions.append(
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="write_file",
                tool_arguments=args,
                user_message="Dosyayi tekrar yazmayi deniyorum.",
                risk_level=RiskLevel.NORMAL_MODIFICATION,
            )
        )
        return actions


class DnsRetryStrategy(RecoveryStrategy):
    strategy_id = "set_dns_retry"
    applicable_tools = ("set_dns",)
    applicable_errors = (
        ErrorCategory.TRANSIENT,
        ErrorCategory.PERMISSION_DENIED,
        ErrorCategory.VERIFICATION_FAILURE,
        ErrorCategory.NETWORK_ERROR,
    )
    max_attempts = 2
    risk_level = RiskLevel.HIGH_RISK

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name="set_dns",
                tool_arguments=dict(ctx.step.tool_arguments),
                user_message="DNS ayari basarisiz oldu. Tekrar deniyorum.",
                risk_level=RiskLevel.HIGH_RISK,
            )
        ]


class VerificationRetryStrategy(RecoveryStrategy):
    strategy_id = "verification_retry_original"
    applicable_tools = ()
    applicable_errors = (ErrorCategory.VERIFICATION_FAILURE,)
    max_attempts = 1

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        tool = ctx.step.tool_name
        if not tool:
            return []
        return [
            RecoveryAction(
                strategy_id=self.strategy_id,
                tool_name=tool,
                tool_arguments=dict(ctx.step.tool_arguments),
                user_message="Dogrulama basarisiz. Islem tekrar deneniyor.",
                risk_level=RiskLevel.LOW_RISK,
            )
        ]


class CapabilityFallbackStrategy(RecoveryStrategy):
    """Try a different mechanism for the same goal, not the same one again.

    The alternatives come from each tool's declared `fallback_tools`, so adding
    a new tool with a fallback needs no change here. Arguments are carried over
    by schema intersection rather than per-pair mapping.
    """

    strategy_id = "capability_fallback"
    applicable_tools = ()
    applicable_errors = (
        ErrorCategory.TOOL_FAILURE,
        ErrorCategory.NOT_FOUND,
        ErrorCategory.DEPENDENCY_MISSING,
        ErrorCategory.VERIFICATION_FAILURE,
        ErrorCategory.PERMISSION_DENIED,
    )
    max_attempts = 2
    risk_level = RiskLevel.LOW_RISK

    def build_actions(self, ctx: RecoveryContext) -> list[RecoveryAction]:
        registry = ctx.registry
        tool_name = ctx.step.tool_name or ""
        if registry is None or not tool_name:
            return []

        failed = registry.get(tool_name)
        if failed is None:
            return []

        actions: list[RecoveryAction] = []
        for alternative in failed.get_definition().fallback_tools:
            candidate = registry.get(alternative)
            if candidate is None:
                continue
            adapted = fit_arguments(
                dict(ctx.step.tool_arguments), candidate.get_parameters_schema()
            )
            if adapted is None:
                continue
            actions.append(
                RecoveryAction(
                    strategy_id=f"{self.strategy_id}:{alternative}",
                    tool_name=alternative,
                    tool_arguments=adapted,
                    user_message=f"{tool_name} sonuc vermedi. {alternative} ile deniyorum.",
                    risk_level=candidate.risk_level,
                )
            )
        return actions


def create_default_strategies() -> list[RecoveryStrategy]:
    return [
        InstallAlreadyInstalledStrategy(),
        InstallRetryStrategy(),
        GitCloneCheckExistingStrategy(),
        GitCloneAltPathStrategy(),
        GitCloneRetryStrategy(),
        CreateFolderParentStrategy(),
        WriteFileParentStrategy(),
        DnsRetryStrategy(),
        VerificationRetryStrategy(),
        RetrySameToolStrategy(),
        # Last: prefer a targeted fix or a plain retry before switching mechanism.
        CapabilityFallbackStrategy(),
    ]
