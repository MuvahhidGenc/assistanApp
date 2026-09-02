from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from hermes.config.settings import RiskLevel
from hermes.mission.models import Mission, MissionStep
from hermes.mission.recovery.classifier import ErrorCategory
from hermes.server.models import ToolResultPayload
from hermes.tools.windows.file_tools import resolve_user_path


class FailureKind(StrEnum):
    EXECUTION = "execution"
    VERIFICATION = "verification"


@dataclass
class RecoveryAction:
    strategy_id: str
    tool_name: str
    tool_arguments: dict[str, Any]
    user_message: str
    risk_level: RiskLevel = RiskLevel.LOW_RISK
    idempotency_key: str | None = None


@dataclass
class RecoveryContext:
    mission: Mission
    step: MissionStep
    failure_kind: FailureKind
    error_category: ErrorCategory
    error_text: str
    last_result: Any = None
    verification_details: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    strategy_attempts: dict[str, int] = field(default_factory=dict)
    total_attempts: int = 0


@dataclass
class IdempotencyResult:
    satisfied: bool
    user_message: str = ""
    observation: dict[str, Any] = field(default_factory=dict)


def _resolve_folder(raw: str) -> Path:
    text = (raw or "").strip() or "Hermes"
    target = Path(text).expanduser()
    if not target.is_absolute():
        target = Path.home() / "Desktop" / target
    return target.resolve()


def _git_target(step: MissionStep, output: Any) -> Path | None:
    data = output if isinstance(output, dict) else {}
    raw = str(data.get("path") or step.tool_arguments.get("target_dir") or "").strip()
    if not raw:
        url = str(step.tool_arguments.get("repo_url") or "")
        name = Path(urlparse(url.replace("git@", "https://")).path).stem
        if name:
            return (Path.home() / "Desktop" / name).resolve()
        return None
    return _resolve_folder(raw)


async def check_idempotency(
    step: MissionStep,
    *,
    observe_tool: Any = None,
    run_id: str = "",
) -> IdempotencyResult:
    tool = step.tool_name or ""
    args = step.tool_arguments

    if tool == "install_program":
        package = str(args.get("package") or "").strip()
        if observe_tool and package:
            observed = await observe_tool(
                "list_installed_programs",
                {"filter_name": package, "limit": 20},
                run_id,
            )
            if observed and observed.success and isinstance(observed.output, dict):
                programs = observed.output.get("programs") or []
                names = [str(p.get("DisplayName", "")) for p in programs if isinstance(p, dict)]
                if any(package.casefold() in n.casefold() for n in names if n):
                    return IdempotencyResult(
                        satisfied=True,
                        user_message=f"{package} zaten kurulu gorunuyor, dogruluyorum.",
                        observation={"installed_programs": names[:5]},
                    )

    if tool == "git_clone":
        target = _git_target(step, step.observation.get("data") or {})
        if target and target.is_dir() and (target / ".git").exists():
            return IdempotencyResult(
                satisfied=True,
                user_message="Repo zaten klonlanmis, dogruluyorum.",
                observation={"path": str(target), "git_dir_exists": True},
            )

    if tool == "create_folder":
        try:
            target = _resolve_folder(str(args.get("path") or ""))
            if target.is_dir():
                return IdempotencyResult(
                    satisfied=True,
                    user_message="Klasor zaten mevcut, dogruluyorum.",
                    observation={"path": str(target)},
                )
        except (ValueError, OSError):
            pass

    if tool == "write_file":
        raw = str(args.get("path") or "").strip()
        if raw:
            try:
                target = resolve_user_path(raw)
                if target.is_file():
                    return IdempotencyResult(
                        satisfied=True,
                        user_message="Dosya zaten mevcut, dogruluyorum.",
                        observation={"path": str(target), "size": target.stat().st_size},
                    )
            except (ValueError, OSError):
                pass

    return IdempotencyResult(satisfied=False)


def step_is_fatal(step: MissionStep) -> bool:
    if "fatal" in step.metadata:
        return bool(step.metadata.get("fatal"))
    tool = step.tool_name or ""
    if tool == "install_program":
        return bool(step.metadata.get("required", False))
    return True
