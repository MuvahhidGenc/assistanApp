import logging

import pytest

from hermes.utils.logging import setup_cli_logging, suppress_http_logs


def test_cli_logging_quiet_suppresses_info(caplog):
    caplog.set_level(logging.WARNING)
    setup_cli_logging(debug=False)
    logger = logging.getLogger("test_cli_quiet")
    logger.info("should not appear")
    logger.warning("warning visible")
    assert not any("should not appear" in r.message for r in caplog.records)
    assert any("warning visible" in r.message for r in caplog.records)


def test_cli_logging_debug_shows_structlog(capsys):
    setup_cli_logging(debug=True, level="DEBUG")
    from hermes.utils.logging import get_logger

    get_logger("test").info("debug event", key="value")
    captured = capsys.readouterr()
    assert "debug event" in captured.err or "key" in captured.err


def test_suppress_http_logs():
    suppress_http_logs()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
