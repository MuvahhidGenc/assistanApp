from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[3]


def app_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "HermesClient"


def logs_dir() -> Path:
    return app_data_dir() / "logs"


def app_log_path() -> Path:
    return logs_dir() / "app.log"


def user_config_dir() -> Path:
    return app_data_dir() / "config"


def user_config_path() -> Path:
    return user_config_dir() / "default.yaml"


def user_env_path() -> Path:
    return app_data_dir() / ".env"


def client_state_dir() -> Path:
    return app_data_dir() / "state"


def client_state_path() -> Path:
    return client_state_dir() / "client.json"


def bundled_config_path() -> Path:
    return bundle_root() / "config" / "default.yaml"


def bundled_assets_dir() -> Path:
    return bundle_root() / "assets"


def portable_config_path() -> Path | None:
    """Config shipped next to the executable (portable release layout)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent / "config" / "default.yaml"
    return None


def resolve_config_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    if user_config_path().exists():
        return user_config_path()
    portable = portable_config_path()
    if portable is not None and portable.exists():
        return portable
    if bundled_config_path().exists():
        return bundled_config_path()
    return Path("config/default.yaml")


def resolve_env_file() -> Path | None:
    if user_env_path().exists():
        return user_env_path()
    project_env = Path(".env")
    if project_env.exists():
        return project_env
    return None


def ensure_user_dirs() -> None:
    logs_dir().mkdir(parents=True, exist_ok=True)
    user_config_dir().mkdir(parents=True, exist_ok=True)
    client_state_dir().mkdir(parents=True, exist_ok=True)


def resolve_executable_path() -> Path:
    if is_frozen():
        return Path(sys.executable)
    return Path(sys.executable)


def resolve_tray_command() -> tuple[Path, str]:
    """Return (executable, arguments) to launch tray mode."""
    if is_frozen():
        return Path(sys.executable), ""

    dist_exe = bundle_root() / "dist" / "hermes-client.exe"
    if dist_exe.exists():
        return dist_exe, "tray"

    scripts_exe = bundle_root() / ".venv" / "Scripts" / "hermes-client.exe"
    if scripts_exe.exists():
        return scripts_exe, "tray"

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else Path(sys.executable)
    return launcher, "-m hermes.main tray"
