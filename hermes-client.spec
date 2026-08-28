# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
entry = root / "src" / "hermes" / "entry_tray.py"
icon_file = root / "assets" / "tray.ico"

datas = [
    (str(root / "config" / "default.yaml"), "config"),
]
if icon_file.exists():
    datas.append((str(icon_file), "assets"))
if (root / "assets" / "tray.png").exists():
    datas.append((str(root / "assets" / "tray.png"), "assets"))

datas += collect_data_files("customtkinter")

hiddenimports = collect_submodules("customtkinter")
hiddenimports += [
    "pystray",
    "PIL",
    "PIL.Image",
    "speech_recognition",
    "pyttsx3",
    "winotify",
    "keyring.backends",
    "keyring.backends.Windows",
    "httpx",
    "h2",
    "anyio",
    "sniffio",
]

a = Analysis(
    [str(entry)],
    pathex=[str(root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="hermes-client",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
)
