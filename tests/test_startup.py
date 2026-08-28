from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes.platform.startup import (
    StartupMethod,
    build_launch_command,
    get_startup_status,
    install_startup,
    remove_registry_run,
    set_registry_run,
    uninstall_startup,
)


def test_build_launch_command():
    assert build_launch_command(Path(r"C:\app\hermes-client.exe"), "tray") == (
        '"C:\\app\\hermes-client.exe" tray'
    )


def test_get_startup_status_none(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hermes.platform.startup.startup_shortcut_path",
        lambda: tmp_path / "missing.lnk",
    )
    monkeypatch.setattr("hermes.platform.startup.read_registry_run", lambda: None)
    status = get_startup_status()
    assert not status.installed
    assert status.method == StartupMethod.NONE


def test_get_startup_status_registry(monkeypatch):
    monkeypatch.setattr(
        "hermes.platform.startup.startup_shortcut_path",
        lambda: Path("C:/missing.lnk"),
    )
    monkeypatch.setattr(
        "hermes.platform.startup.read_registry_run",
        lambda: '"C:\\app\\hermes-client.exe" tray',
    )
    status = get_startup_status()
    assert status.installed
    assert status.method == StartupMethod.REGISTRY


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="Windows registry")
def test_registry_roundtrip():
    value = '"C:\\test\\hermes-client.exe" tray'
    try:
        set_registry_run(value)
        status = get_startup_status()
        assert status.installed
        assert status.method == StartupMethod.REGISTRY
    finally:
        remove_registry_run()


def test_install_startup_uses_shortcut(tmp_path, monkeypatch):
    lnk = tmp_path / "startup.lnk"
    target = tmp_path / "hermes-client.exe"
    target.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "hermes.platform.startup.resolve_tray_command",
        lambda: (target, "tray"),
    )
    monkeypatch.setattr(
        "hermes.platform.startup.startup_shortcut_path",
        lambda: lnk,
    )
    monkeypatch.setattr(
        "hermes.platform.startup.create_startup_shortcut",
        lambda **kwargs: lnk.touch() or lnk,
    )
    monkeypatch.setattr("hermes.platform.startup.remove_registry_run", lambda: False)
    monkeypatch.setattr(
        "hermes.platform.startup.get_startup_status",
        lambda: __import__("hermes.platform.startup", fromlist=["StartupStatus"]).StartupStatus(
            installed=True,
            method=StartupMethod.SHORTCUT,
            shortcut_path=str(lnk),
        ),
    )

    result = install_startup()
    assert result.installed
    assert result.method == StartupMethod.SHORTCUT


def test_uninstall_startup(tmp_path, monkeypatch):
    lnk = tmp_path / "HERMES Client.lnk"
    lnk.write_text("fake", encoding="utf-8")
    monkeypatch.setattr("hermes.platform.startup.startup_shortcut_path", lambda: lnk)
    monkeypatch.setattr("hermes.platform.startup.remove_registry_run", lambda: False)

    status = uninstall_startup()
    assert not lnk.exists()
    assert not status.installed
