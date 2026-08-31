"""Frozen Windows tray executable entry point (no console)."""

from __future__ import annotations


def main() -> None:
    from hermes.utils.logging import get_logger, setup_app_logging

    setup_app_logging()
    logger = get_logger(__name__)
    logger.info("tray_entry_start")

    try:
        from hermes.platform.elevation import ensure_admin_or_exit

        ensure_admin_or_exit()
    except SystemExit:
        raise
    except Exception as exc:
        logger.warning("elevation_skip", error=str(exc))

    from hermes.ui.app import run_tray_app

    try:
        run_tray_app()
    except Exception:
        logger.exception("tray_entry_failed")
        raise


if __name__ == "__main__":
    main()
