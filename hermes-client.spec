# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
import certifi

root = Path(SPECPATH)
entry = root / "src" / "hermes" / "entry_tray.py"
icon_file = root / "assets" / "tray.ico"

datas = [
    (str(root / "config" / "default.yaml"), "config"),
    # Skills are looked up next to their loader at runtime; without this the
    # frozen build finds an empty procedures directory and silently has none.
    (
        str(root / "src" / "hermes" / "skills" / "procedures"),
        "hermes/skills/procedures",
    ),
]
if icon_file.exists():
    datas.append((str(icon_file), "assets"))
if (root / "assets" / "tray.png").exists():
    datas.append((str(root / "assets" / "tray.png"), "assets"))

datas += collect_data_files("customtkinter")
datas += collect_data_files("edge_tts")
try:
    datas += collect_data_files("faster_whisper")
except Exception:
    pass
datas += [(certifi.where(), "certifi")]

hiddenimports = collect_submodules("customtkinter")
hiddenimports += collect_submodules("edge_tts")
hiddenimports += [
    "aiohttp",
    "certifi",
    "pystray",
    "PIL",
    "PIL.Image",
    "speech_recognition",
    "pyaudio",
    "pyttsx3",
    "edge_tts",
    "faster_whisper",
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
    excludes=[
        # Retired semantic/orchestration stacks are intentionally absent from
        # the V3 production executable. V3 semantics are owned by the VPS
        # ReasoningRuntime contract.
        "hermes.agent",
        "hermes.intent",
        "hermes.mission.engine",
        "hermes.mission.planner",
        "hermes.mission.selection",
    ],
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
    uac_admin=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
)
