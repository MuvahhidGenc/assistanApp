from pathlib import Path

from hermes.utils.logging import get_logger, setup_app_logging, setup_logging


def test_setup_app_logging_writes_file(tmp_path, monkeypatch):
    log_file = tmp_path / "logs" / "app.log"
    monkeypatch.setattr("hermes.utils.logging.app_log_path", lambda: log_file)
    monkeypatch.setattr(
        "hermes.utils.logging.ensure_user_dirs",
        lambda: log_file.parent.mkdir(parents=True, exist_ok=True),
    )

    path = setup_app_logging(level="INFO", log_path=log_file)
    assert path == log_file
    assert log_file.exists()


def test_setup_logging_survives_none_stderr(monkeypatch):
    monkeypatch.setattr("sys.stderr", None)
    setup_logging(level="INFO", fmt="json")
    logger = get_logger("test")
    logger.info("stderr_none_ok")
