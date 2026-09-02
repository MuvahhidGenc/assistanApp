from __future__ import annotations

from dataclasses import dataclass

from hermes.agent.orchestrator import AgentOrchestrator
from hermes.config.credentials import resolve_api_key
from hermes.config.settings import AppSettings
from hermes.server.client import HermesServerClient
from hermes.utils.logging import setup_cli_logging, setup_logging


@dataclass
class HermesApplication:
    settings: AppSettings
    server: HermesServerClient
    agent: AgentOrchestrator

    async def shutdown(self) -> None:
        await self.server.close()


def create_application(
    config_path: str | None = None,
    cli_mode: bool = False,
    debug: bool = False,
    configure_logging: bool = True,
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
    agent = AgentOrchestrator(settings, server)
    return HermesApplication(settings=settings, server=server, agent=agent)
