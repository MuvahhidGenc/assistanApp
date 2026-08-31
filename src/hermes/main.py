from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(
    name="hermes-client",
    help="HERMES AI — Windows IT Assistant client for Hermes Server",
    no_args_is_help=True,
)
console = Console()


def _load_settings(config: Path | None = None):
    from hermes.config.settings import AppSettings

    return AppSettings.load(config)


@app.command()
def configure() -> None:
    """Save server URL, API key, and model."""
    from hermes.config.settings_store import (
        SettingsFormData,
        load_settings_form,
        save_settings_form,
    )

    current = load_settings_form()
    url = typer.prompt("Server URL", default=current.api_url or "http://50.6.226.228:8642")
    api_key = typer.prompt("API Key", default=current.api_key or "", hide_input=True)
    model = typer.prompt("Model", default=current.model or "hermes-agent")
    data = SettingsFormData(
        api_url=url,
        api_key=api_key,
        model=model,
        prefer_short_responses=current.prefer_short_responses,
        voice_enabled=current.voice_enabled,
        wake_word_enabled=current.wake_word_enabled,
        notifications_enabled=current.notifications_enabled,
    )
    path = save_settings_form(data)
    console.print(f"Kaydedildi: {path}")


@app.command()
def status(
    config: Path | None = typer.Option(None, "--config", help="Config YAML path"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Test API connectivity (GET /v1/models via health)."""
    from hermes.status_report import build_status_report, format_status_lines
    from hermes.utils.logging import setup_cli_logging

    setup_cli_logging(debug=debug)
    settings = _load_settings(config)
    report = asyncio.run(build_status_report(settings, config_path=config))
    for line in format_status_lines(report):
        console.print(line)
    raise typer.Exit(code=0 if report.connection_ok else 1)


@app.command()
def tools(
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    """List local Windows tools."""
    from hermes.tools.registry import create_default_registry

    registry = create_default_registry()
    for definition in registry.list_tools():
        name = getattr(definition, "name", str(definition))
        description = getattr(definition, "description", "")
        console.print(f"{name}" + (f" — {description}" if description else ""))


@app.command()
def chat(
    message: str = typer.Argument(..., help="Message to send through the agent"),
    config: Path | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Send one message through the agent + local tool layer."""
    from hermes.app.bootstrap import create_application
    from hermes.cli.session import CliChatSession
    from hermes.utils.logging import setup_cli_logging

    setup_cli_logging(debug=debug)

    async def _run() -> None:
        application = create_application(
            str(config) if config else None,
            cli_mode=True,
            debug=debug,
            configure_logging=False,
        )
        session = CliChatSession(console, debug=debug)
        application.agent._on_status = session.on_status
        result = await application.agent.process_message(message)
        if result:
            console.print(result)
        await application.shutdown()

    asyncio.run(_run())


@app.command()
def interactive(
    config: Path | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Interactive chat session."""
    from rich.prompt import Prompt

    from hermes.app.bootstrap import create_application
    from hermes.cli.session import CliChatSession
    from hermes.utils.logging import setup_cli_logging

    setup_cli_logging(debug=debug)

    async def _run() -> None:
        application = create_application(
            str(config) if config else None,
            cli_mode=True,
            debug=debug,
            configure_logging=False,
        )
        session = CliChatSession(console, debug=debug)
        application.agent._on_status = session.on_status
        console.print("HERMES interactive. Cikmak icin 'exit' yazin.")
        try:
            while True:
                text = Prompt.ask("[bold cyan]siz[/bold cyan]")
                if text.strip().lower() in {"exit", "quit", "q"}:
                    break
                if not text.strip():
                    continue
                result = await application.agent.process_message(text)
                if result:
                    console.print(result)
        finally:
            await application.shutdown()

    asyncio.run(_run())


@app.command()
def tray(
    config: Path | None = typer.Option(None, "--config"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Start the system-tray UI."""
    from hermes.ui.app import run_tray_app

    run_tray_app(config_path=str(config) if config else None, debug=debug)


@app.command("install-startup")
def install_startup_cmd() -> None:
    """Install HERMES to start with Windows."""
    from hermes.platform.startup import install_startup

    status = install_startup()
    console.print(f"Startup: {status.method} (installed={status.installed})")


@app.command("uninstall-startup")
def uninstall_startup_cmd() -> None:
    """Remove HERMES from Windows startup."""
    from hermes.platform.startup import uninstall_startup

    status = uninstall_startup()
    console.print(f"Startup removed (installed={status.installed})")


if __name__ == "__main__":
    app()
