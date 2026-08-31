from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hermes import __version__
from hermes.agent.orchestrator import AgentPhase
from hermes.app.bootstrap import create_application
from hermes.cli.session import CliChatSession
from hermes.config.credentials import resolve_api_key, set_api_key
from hermes.config.settings import AppSettings
from hermes.security import NaturalLanguageApprovalParser
from hermes.server.client import HermesServerError
from hermes.tools.registry import create_default_registry

app = typer.Typer(
    name="hermes-client",
    help="HERMES Windows Client — API client for Hermes Agent Server",
    no_args_is_help=True,
)
console = Console()


def _load_settings(config: str | None) -> AppSettings:
    from pathlib import Path

    return AppSettings.load(Path(config) if config else None)


@app.command()
def version() -> None:
    """Show version."""
    console.print(f"HERMES Client v{__version__}")


@app.command()
def configure(
    server_url: str = typer.Option(..., prompt="Hermes Server URL"),
    api_key: str = typer.Option(..., prompt="API Key", hide_input=True),
    model: str = typer.Option("hermes-agent", prompt="Model"),
) -> None:
    """Save server URL to .env and API key to Windows Credential Manager."""
    env_path = ".env"
    lines = []
    if __import__("pathlib").Path(env_path).exists():
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()

    url_written = False
    model_written = False
    new_lines = []
    for line in lines:
        if line.startswith("HERMES_SERVER_URL="):
            new_lines.append(f"HERMES_SERVER_URL={server_url}\n")
            url_written = True
        elif line.startswith("HERMES_MODEL="):
            new_lines.append(f"HERMES_MODEL={model}\n")
            model_written = True
        elif line.startswith("HERMES_API_KEY="):
            continue  # never store API key in .env
        else:
            new_lines.append(line)
    if not url_written:
        new_lines.append(f"HERMES_SERVER_URL={server_url}\n")
    if not model_written:
        new_lines.append(f"HERMES_MODEL={model}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    set_api_key(api_key)
    from hermes.config.settings_store import upsert_user_config_server

    user_cfg = upsert_user_config_server(url=server_url, model=model)
    console.print("[green]Configuration saved.[/green]")
    console.print("  Server URL → .env + user config")
    console.print("  Model → .env + user config")
    console.print(f"  User config → {user_cfg}")
    console.print("  API Key → Windows Credential Manager")


@app.command()
def status(config: str | None = typer.Option(None, "--config", "-c")) -> None:
    """Check Hermes API connection, config, voice/mic, and startup status."""
    import asyncio
    from pathlib import Path

    from hermes.status_report import build_status_report, format_status_lines, load_status_settings

    settings = load_status_settings(Path(config) if config else None)
    report = asyncio.run(build_status_report(settings, config_path=Path(config) if config else None))

    color = "green" if report.connection_ok else "red"
    console.print(Panel(f"[bold {color}]HERMES Windows Client Status[/bold {color}]"))
    for line in format_status_lines(report):
        console.print(f"  {line}")

    if not report.connection_ok and report.error:
        raise typer.Exit(1)


@app.command("install-startup")
def install_startup_cmd() -> None:
    """Add HERMES tray app to current-user Windows startup."""
    from hermes.platform.startup import install_startup
    from hermes.utils.logging import get_logger, setup_app_logging

    setup_app_logging()
    logger = get_logger(__name__)
    try:
        result = install_startup()
    except Exception as exc:
        logger.exception("install_startup_failed")
        console.print(f"[red]Startup install failed: {exc}[/red]")
        raise typer.Exit(1)

    if result.installed:
        console.print(f"[green]Startup installed via {result.method.value}[/green]")
        if result.shortcut_path:
            console.print(f"  Shortcut: {result.shortcut_path}")
        elif result.target:
            console.print(f"  Target:   {result.target}")
        logger.info("install_startup_success", method=result.method.value)
    else:
        console.print("[red]Startup install did not complete.[/red]")
        raise typer.Exit(1)


@app.command("uninstall-startup")
def uninstall_startup_cmd() -> None:
    """Remove HERMES from current-user Windows startup."""
    from hermes.platform.startup import uninstall_startup
    from hermes.utils.logging import get_logger, setup_app_logging

    setup_app_logging()
    logger = get_logger(__name__)
    before = uninstall_startup()
    if before.installed:
        console.print("[green]Startup entry removed.[/green]")
        logger.info("uninstall_startup_success")
    else:
        console.print("[yellow]No startup entry found.[/yellow]")


@app.command()
def tools() -> None:
    """List locally available Windows IT tools."""
    registry = create_default_registry()
    console.print(f"[bold]Windows IT Tools[/bold] ({len(registry)} total)\n")
    for category in registry.categories():
        table = Table(title=category.capitalize())
        table.add_column("Name")
        table.add_column("Risk")
        table.add_column("Description")
        for tool in registry.list_by_category(category):
            table.add_row(tool.name, tool.risk_level.value, tool.description)
        console.print(table)
        console.print()


@app.command("tool-run")
def tool_run(
    name: str = typer.Argument(..., help="Tool name"),
    args_json: str = typer.Option("{}", "--args", "-a", help='JSON arguments, e.g. {"host":"8.8.8.8"}'),
) -> None:
    """Run a local Windows tool directly (for testing)."""
    registry = create_default_registry()
    tool = registry.get(name)
    if not tool:
        console.print(f"[red]Unknown tool: {name}[/red]")
        raise typer.Exit(1)

    try:
        args = json.loads(args_json)
        if not isinstance(args, dict):
            raise ValueError("--args must be a JSON object")
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON: {exc}[/red]")
        raise typer.Exit(1)

    async def _run():
        return await tool.execute(**args)

    result = asyncio.run(_run())
    if result.success:
        console.print(Panel(json.dumps(result.output, indent=2, ensure_ascii=False), title=name))
    else:
        console.print(f"[red]Error:[/red] {result.error}")
        raise typer.Exit(1)


@app.command()
def chat(
    message: str = typer.Argument(..., help="Message to send to Hermes"),
    config: str | None = typer.Option(None, "--config", "-c"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Show JSON debug events"),
) -> None:
    """Send a message and run the agent loop."""
    settings = _load_settings(config)
    api_key = resolve_api_key(settings.api_key.get_secret_value())

    if not settings.server.url or not api_key:
        console.print("[red]Not configured. Run: hermes-client configure[/red]")
        raise typer.Exit(1)

    async def _run() -> None:
        application = create_application(config, cli_mode=True, debug=debug)
        session = CliChatSession(console, debug=debug)
        session.attach(application.agent)

        console.print(f"[bold]You:[/bold] {message}")
        try:
            response = await application.agent.process_message(message)
            if response.strip():
                console.print(Panel(response.strip(), title="HERMES"))
        finally:
            await application.shutdown()

    try:
        asyncio.run(_run())
    except HermesServerError as exc:
        console.print(f"[red]Server error: {exc}[/red]")
        raise typer.Exit(1)


@app.command()
def interactive(
    config: str | None = typer.Option(None, "--config", "-c"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Show JSON debug events"),
) -> None:
    """Interactive chat session."""
    settings = _load_settings(config)
    api_key = resolve_api_key(settings.api_key.get_secret_value())

    if not settings.server.url or not api_key:
        console.print("[red]Not configured. Run: hermes-client configure[/red]")
        raise typer.Exit(1)

    async def _run() -> None:
        application = create_application(config, cli_mode=True, debug=debug)
        session = CliChatSession(console, debug=debug)
        session.attach(application.agent)

        console.print("[bold]HERMES Interactive[/bold] — 'exit' to quit\n")
        try:
            await application.agent.initialize_session()
            while True:
                user_input = typer.prompt("\nYou")
                if user_input.strip().lower() in ("exit", "quit", "çık"):
                    break
                session.reset_status()
                response = await application.agent.process_message(user_input)
                if response.strip():
                    console.print(Panel(response.strip(), title="HERMES"))
        finally:
            await application.shutdown()

    asyncio.run(_run())


@app.command()
def tray(
    config: str | None = typer.Option(None, "--config", "-c"),
    debug: bool = typer.Option(False, "--debug", "-d"),
) -> None:
    """Run HERMES in the Windows system tray with chat window."""
    settings = _load_settings(config)
    api_key = resolve_api_key(settings.api_key.get_secret_value())

    if not settings.server.url or not api_key:
        console.print("[red]Not configured. Run: hermes-client configure[/red]")
        raise typer.Exit(1)

    try:
        from hermes.ui.app import run_tray_app
        from hermes.utils.logging import setup_app_logging

        setup_app_logging()
        run_tray_app(config_path=config, debug=debug)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        pass


@app.command()
def voice(
    config: str | None = typer.Option(None, "--config", "-c"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Show JSON debug events"),
    no_wake_word: bool = typer.Option(
        False,
        "--no-wake-word",
        help="Disable wake word listening (text input still works)",
    ),
    wake_word: bool = typer.Option(
        False,
        "--wake-word",
        help="Force enable wake word listening",
    ),
) -> None:
    """Voice assistant with optional wake word (abi/akhi/dostum). Text input always works."""
    settings = _load_settings(config)
    api_key = resolve_api_key(settings.api_key.get_secret_value())

    if not settings.server.url or not api_key:
        console.print("[red]Not configured. Run: hermes-client configure[/red]")
        raise typer.Exit(1)

    voice_settings = settings.voice.model_copy()
    if no_wake_word:
        voice_settings.wake_word_enabled = False
    elif wake_word:
        voice_settings.wake_word_enabled = True

    async def _run() -> None:
        from hermes.voice.assistant import build_voice_assistant

        application = create_application(config, cli_mode=True, debug=debug)
        session = CliChatSession(console, debug=debug)
        session.attach(application.agent)

        assistant = build_voice_assistant(application.agent, voice_settings)

        async def on_status(message: str) -> None:
            console.print(f"[cyan]{message}[/cyan]")

        async def on_response(response: str, source: str) -> None:
            title = "HERMES" if source == "text" else "HERMES (sesli)"
            console.print(Panel(response, title=title))

        assistant.on_status = on_status
        assistant.on_response = on_response

        await application.agent.initialize_session()
        await assistant.start()

        wake_label = ", ".join(assistant.wake_words)
        console.print("[bold]HERMES Voice[/bold]")
        console.print("[dim]Metin girisi her zaman acik — yazip Enter.[/dim]")
        if assistant.wake_word_enabled:
            if assistant.microphone_available:
                console.print(f"[dim]Wake word dinleniyor: {wake_label}[/dim]")
            else:
                console.print("[yellow]Wake word acik ama mikrofon yok — sadece metin.[/yellow]")
        else:
            console.print("[dim]Wake word kapali (--no-wake-word veya config).[/dim]")
        console.print("[dim]Durdur: dur / iptal / sus  |  Cikis: exit[/dim]\n")

        try:
            while True:
                line = await asyncio.to_thread(input, "You> ")
                if line.strip().lower() in ("exit", "quit", "cik", "çık"):
                    break
                if line.strip():
                    await assistant.handle_text_input(line)
        finally:
            await assistant.stop()
            await application.shutdown()

    try:
        asyncio.run(_run())
    except HermesServerError as exc:
        console.print(f"[red]Server error: {exc}[/red]")
        raise typer.Exit(1)


@app.command("parse-approval")
def parse_approval(
    text: str = typer.Argument(...),
    steps: int = typer.Option(5, "--steps", "-n"),
) -> None:
    """Test natural language approval parsing."""
    result = NaturalLanguageApprovalParser.parse(text, total_steps=steps)
    console.print(f"Decision: [bold]{result.decision.value}[/bold]")
    if result.approved_steps:
        console.print(f"Approved steps: {result.approved_steps}")
    if result.excluded_steps:
        console.print(f"Excluded steps: {result.excluded_steps}")


if __name__ == "__main__":
    app()
