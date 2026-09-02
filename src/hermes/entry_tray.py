"""Frozen Windows tray executable entry point (no console)."""

from __future__ import annotations

import sys


def main() -> None:
    from hermes.platform.runtime import configure_ssl_certificates

    configure_ssl_certificates()

    if "--tts-smoke" in sys.argv:
        import asyncio

        from hermes.utils.logging import setup_app_logging
        from hermes.voice.tts_smoke import main as smoke_main

        setup_app_logging()
        raise SystemExit(asyncio.run(smoke_main(sys.argv)))

    from hermes.utils.logging import get_logger, setup_app_logging

    setup_app_logging()
    logger = get_logger(__name__)
    from hermes.build_info import BUILD_TIMESTAMP, HERMES_BUILD_VERSION

    logger.info(
        "hermes_build",
        HERMES_BUILD_VERSION=HERMES_BUILD_VERSION,
        BUILD_TIMESTAMP=BUILD_TIMESTAMP,
    )
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
