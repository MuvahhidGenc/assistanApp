from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes.config.credentials import resolve_api_key
from hermes.config.settings import AppSettings
from hermes.runtime.bootstrap import (
    ApprovalProvider,
    build_v3_application,
)
from hermes.security.approval_manager import ParsedApproval
from hermes.server.client import HermesServerClient
from hermes.utils.logging import setup_cli_logging, setup_logging


@dataclass
class HermesApplication:
    """The V3 production application container.

    Exposes the same public surface that the V2 UI/voice layer expects
    (``settings``, ``server``, ``agent``) so existing call sites work
    without source edits. The ``agent`` is a V3 orchestrator that owns
    the reasoning loop end-to-end.
    """

    settings: AppSettings
    server: HermesServerClient
    agent: Any  # V3Orchestrator — typed in the runtime package.

    async def shutdown(self) -> None:
        await self.server.close()


class _SettingsApprovalProvider:
    """Production default approval provider.

    Resolves every pending approval according to the user's settings.
    The provider never auto-approves by default; the
    ``settings.ui.auto_approve_local_tools`` flag is kept as a
    *secondary* knob that the user must explicitly enable. When that
    knob is off (the production default), high-risk actions are
    rejected at the policy layer and require an explicit user prompt.
    """

    def __init__(self, auto_approve_local_tools: bool, user_input_provider: Any | None = None) -> None:
        self._auto_approve_local_tools = auto_approve_local_tools
        self._user_input_provider = user_input_provider

    async def __call__(self, pending: Any) -> ParsedApproval:
        from hermes.server.models import ApprovalDecision

        tool = getattr(pending.request, "tool", "") or ""
        risk_level = getattr(pending.request, "risk_level", "") or ""
        reason = getattr(pending.request, "reason", "") or ""
        # Read-only actions never need human approval. They should not
        # reach this provider in the first place — the runtime returns
        # APPROVE directly when the risk is READ_ONLY — but if the
        # chain ever does, treat them as auto-approved.
        if risk_level == "read_only":
            return ParsedApproval(decision=ApprovalDecision.APPROVE, message="read_only")

        if self._auto_approve_local_tools:
            # The user has explicitly opted into auto-approval of local
            # tools. This is *not* a wholesale APPROVE_ALL — we approve
            # exactly the requested action. Bulk approval is the
            # provider's choice; the runtime does not assume it.
            return ParsedApproval(decision=ApprovalDecision.APPROVE, message="user_auto_approve_enabled")

        # Default production path: require the user to answer. If no
        # user-input provider is wired (e.g. headless run) we reject
        # the action — fail closed.
        if self._user_input_provider is None:
            return ParsedApproval(
                decision=ApprovalDecision.REJECT,
                message=f"approval required but no provider wired (tool={tool}, reason={reason})",
            )
        answer = await self._user_input_provider(pending)
        return answer


async def _prompt_user(pending: Any) -> ParsedApproval:
    """Default CLI / voice prompt for the user.

    Tests inject their own provider; production runs the UI handler
    that forwards to the chat window.
    """
    # No interactive provider available at bootstrap time — fail closed
    # by default. The UI / voice layers override this by setting their
    # own approval_provider on the application before any tool call.
    from hermes.server.models import ApprovalDecision

    return ParsedApproval(
        decision=ApprovalDecision.REJECT,
        message="approval required; no interactive provider wired",
    )


def create_application(
    config_path: str | None = None,
    cli_mode: bool = False,
    debug: bool = False,
    configure_logging: bool = True,
    approval_provider: ApprovalProvider | None = None,
    memory: Any | None = None,
) -> HermesApplication:
    from pathlib import Path

    from hermes.platform.runtime import configure_ssl_certificates

    configure_ssl_certificates()
    settings = AppSettings.load(Path(config_path) if config_path else None)
    use_debug = debug or settings.client.debug

    if configure_logging:
        if cli_mode:
            setup_cli_logging(debug=use_debug, level=settings.logging.level)
        else:
            setup_logging(settings.logging.level, settings.logging.format)

    api_key = resolve_api_key(settings.api_key.get_secret_value())
    if not api_key:
        raise ValueError(
            "HERMES_API_KEY not configured. Run 'hermes-client configure' or set the environment variable."
        )

    server = HermesServerClient(
        base_url=settings.server.url,
        api_key=api_key,
        model=settings.server.model.strip() or "hermes-agent",
        timeout=float(settings.server.timeout_seconds),
        verify_ssl=settings.server.verify_ssl,
        session_key=None,
    )
    # Production approval wiring: callers may inject their own
    # provider. The default uses ``settings.ui.auto_approve_local_tools``
    # as an opt-in knob but never falls back to ``APPROVE_ALL`` and never
    # calls ``mark_run_bulk_approved`` ahead of time.
    if approval_provider is None:
        approval_provider = _SettingsApprovalProvider(
            auto_approve_local_tools=bool(settings.ui.auto_approve_local_tools),
            user_input_provider=_prompt_user,
        )
    v3 = build_v3_application(
        server=server,
        approval_provider=approval_provider,
        memory=memory,
        turn_timeout_seconds=float(settings.client.agent_step_timeout_seconds),
    )
    return HermesApplication(settings=settings, server=server, agent=v3.orchestrator)
