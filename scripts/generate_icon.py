"""Generate tray icon assets for packaging."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hermes.ui.icon import create_tray_icon


def main() -> None:
    assets = ROOT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    png_path = assets / "tray.png"
    ico_path = assets / "tray.ico"

    image = create_tray_icon(256)
    image.save(png_path, format="PNG")
    image.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"Generated {png_path} and {ico_path}")


if __name__ == "__main__":
    main()
