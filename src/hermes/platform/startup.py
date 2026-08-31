from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from hermes.config.paths import resolve_tray_command
from hermes.utils.logging import get_logger

logger = get_logger(__name__)

STARTUP_LINK_NAME = "HERMES Client.lnk"
REGISTRY_VALUE_NAME = "HermesClient"
REGISTRY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class StartupMethod(StrEnum):
    NONE = "none"
    SHORTCUT = "shortcut"
    REGISTRY = "registry"


@dataclass
class StartupStatus:
    installed: bool
    method: StartupMethod
    target: str = ""
    shortcut_path: str = ""


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA not set")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_shortcut_path() -> Path:
    return startup_folder() / STARTUP_LINK_NAME


def _powershell_escape(value: str) -> str:
    return value.replace("'", "''")


def create_startup_shortcut(
    *,
    target: Path,
    arguments: str,
    shortcut_path: Path | None = None,
) -> Path:
    lnk = shortcut_path or startup_shortcut_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    working_dir = str(target.parent)
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{_powershell_escape(str(lnk))}'); "
        f"$s.TargetPath = '{_powershell_escape(str(target))}'; "
        f"$s.Arguments = '{_powershell_escape(arguments)}'; "
        f"$s.WorkingDirectory = '{_powershell_escape(working_dir)}'; "
        "$s.WindowStyle = 7; "
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not lnk.exists():
        raise RuntimeError(result.stderr.strip() or "Failed to create startup shortcut")
    logger.info("startup_shortcut_created", path=str(lnk), target=str(target))
    return lnk


def remove_startup_shortcut() -> bool:
    lnk = startup_shortcut_path()
    if lnk.exists():
        lnk.unlink()
        logger.info("startup_shortcut_removed", path=str(lnk))
        return True
    return False


def set_registry_run(value: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Registry startup is supported on Windows only")
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        REGISTRY_RUN_KEY,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, REGISTRY_VALUE_NAME, 0, winreg.REG_SZ, value)
    logger.info("startup_registry_set", value=value)


def remove_registry_run() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, REGISTRY_VALUE_NAME)
        logger.info("startup_registry_removed")
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def read_registry_run() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, REGISTRY_VALUE_NAME)
            return str(value)
    except OSError:
        return None


def get_startup_status() -> StartupStatus:
    lnk = startup_shortcut_path()
    if lnk.exists():
        return StartupStatus(
            installed=True,
            method=StartupMethod.SHORTCUT,
            shortcut_path=str(lnk),
            target=str(lnk),
        )
    reg = read_registry_run()
    if reg:
        return StartupStatus(installed=True, method=StartupMethod.REGISTRY, target=reg)
    return StartupStatus(installed=False, method=StartupMethod.NONE)


def build_launch_command(target: Path, arguments: str) -> str:
    if arguments:
        return f'"{target}" {arguments}'
    return f'"{target}"'


def install_startup(
    *,
    target: Path | None = None,
    arguments: str | None = None,
    prefer_shortcut: bool = True,
) -> StartupStatus:
    exe, args = resolve_tray_command()
    launch_target = target or exe
    launch_args = arguments if arguments is not None else args

    if prefer_shortcut:
        try:
            create_startup_shortcut(target=launch_target, arguments=launch_args)
            remove_registry_run()
            return get_startup_status()
        except Exception as exc:
            logger.warning("startup_shortcut_failed", error=str(exc))

    set_registry_run(build_launch_command(launch_target, launch_args))
    return get_startup_status()


def uninstall_startup() -> StartupStatus:
    removed_shortcut = remove_startup_shortcut()
    removed_registry = remove_registry_run()
    if not removed_shortcut and not removed_registry:
        logger.info("startup_nothing_to_remove")
    return get_startup_status()
