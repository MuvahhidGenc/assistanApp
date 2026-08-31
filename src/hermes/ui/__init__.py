"""Modern Windows UI — tray icon and chat window."""

from hermes.ui.app import HermesTrayApplication, run_tray_app
from hermes.ui.state import ConnectionStatus, UIState

__all__ = [
    "ConnectionStatus",
    "HermesTrayApplication",
    "UIState",
    "run_tray_app",
]
