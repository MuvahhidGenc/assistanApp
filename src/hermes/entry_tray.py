"""Frozen Windows tray executable entry point (no console)."""

from __future__ import annotations


def main() -> None:
    from hermes.utils.logging import get_logger, setup_app_logging

    setup_app_logging()
    logger = get_logger(__name__)
    logger.info("tray_entry_start")

    from hermes.ui.app import run_tray_app

    try:
        run_tray_app()
    except Exception:
        logger.exception("tray_entry_failed")
        raise


if __name__ == "__main__":
    main()
