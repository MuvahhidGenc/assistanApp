from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from hermes.config.paths import resolve_tray_command

REGISTRY_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_VALUE_NAME = "HERMES Client"


class StartupMethod(StrEnum):
    NONE = "none"
    REGISTRY = "registry"
    SHORTCUT = "shortcut"


@dataclass
class StartupStatus:
    installed: bool
    method: StartupMethod
    shortcut_path: str | None = None
    command: str | None = None


def build_launch_command(executable: Path, argument: str) -> str:
    return f'"{executable}" {argument}'


def startup_shortcut_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
        / "HERMES Client.lnk"
    )


def _open_run_key(access: int):
    import winreg

    return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, access)


def set_registry_run(value: str) -> None:
    import winreg

    key = _open_run_key(winreg.KEY_SET_VALUE)
    try:
        winreg.SetValueEx(key, REGISTRY_VALUE_NAME, 0, winreg.REG_SZ, value)
    finally:
        winreg.CloseKey(key)


def read_registry_run() -> str | None:
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_READ)
    except OSError:
        return None
    try:
        value, _ = winreg.QueryValueEx(key, REGISTRY_VALUE_NAME)
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)
    return str(value) if value else None


def remove_registry_run() -> bool:
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_RUN_PATH, 0, winreg.KEY_SET_VALUE)
    except OSError:
        return False
    try:
        winreg.DeleteValue(key, REGISTRY_VALUE_NAME)
        return True
    except OSError:
        return False
    finally:
        winreg.CloseKey(key)


def create_startup_shortcut(
    *,
    target: Path,
    arguments: str = "tray",
    shortcut_path: Path | None = None,
) -> Path:
    dest = shortcut_path or startup_shortcut_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(dest))
        shortcut.Targetpath = str(target)
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = str(target.parent)
        shortcut.WindowStyle = 7
        shortcut.save()
    except Exception:
        dest.write_text(build_launch_command(target, arguments), encoding="utf-8")
    return dest


def get_startup_status() -> StartupStatus:
    shortcut = startup_shortcut_path()
    if shortcut.exists():
        return StartupStatus(
            installed=True,
            method=StartupMethod.SHORTCUT,
            shortcut_path=str(shortcut),
        )
    command = read_registry_run()
    if command:
        return StartupStatus(installed=True, method=StartupMethod.REGISTRY, command=command)
    return StartupStatus(installed=False, method=StartupMethod.NONE)


def install_startup() -> StartupStatus:
    target, argument = resolve_tray_command()
    create_startup_shortcut(
        target=target,
        arguments=argument,
        shortcut_path=startup_shortcut_path(),
    )
    remove_registry_run()
    return get_startup_status()


def uninstall_startup() -> StartupStatus:
    shortcut = startup_shortcut_path()
    if shortcut.exists():
        shortcut.unlink()
    remove_registry_run()
    return get_startup_status()
