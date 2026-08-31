from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from hermes.config.paths import app_log_path, ensure_user_dirs


def _log_stream():
    """Return a usable stream for console logging (PyInstaller GUI may have stderr=None)."""
    if sys.stderr is not None:
        return sys.stderr
    if sys.stdout is not None:
        return sys.stdout
    return open(os.devnull, "w")  # noqa: SIM115


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    stream = _log_stream()

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if fmt == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    use_stdlib = sys.stderr is None
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory()
        if use_stdlib
        else structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=True,
    )

    if use_stdlib:
        if not logging.getLogger().handlers:
            logging.basicConfig(level=log_level, format="%(message)s")
    else:
        logging.basicConfig(level=log_level, format="%(message)s", stream=stream)
    suppress_http_logs()


def setup_app_logging(*, level: str = "INFO", log_path: Path | None = None) -> Path:
    """File logging for tray/GUI mode under %LOCALAPPDATA%/HermesClient/logs/app.log."""
    ensure_user_dirs()
    path = log_path or app_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    file_handler = RotatingFileHandler(
        path,
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(log_level)
    root.addHandler(file_handler)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    suppress_http_logs()
    get_logger(__name__).info("app_logging_initialized", log_path=str(path))
    return path


def setup_cli_logging(*, debug: bool = False, level: str = "INFO") -> None:
    """Configure logging for chat/interactive CLI.

    Normal mode: suppress JSON/HTTP noise; only warnings+ to stderr.
    Debug mode: full JSON structlog + HTTP traces.
    """
    if debug:
        setup_logging(level=level, fmt="json")
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
    else:
        setup_logging(level="WARNING", fmt="console")
        suppress_http_logs()


def suppress_http_logs() -> None:
    for name in ("httpx", "httpcore", "hpack"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
