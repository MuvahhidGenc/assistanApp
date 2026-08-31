from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


def create_tray_icon(size: int = 64) -> Image.Image:
    """Generate a simple HERMES tray icon (blue circle + H)."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = size // 8
    draw.ellipse((margin, margin, size - margin, size - margin), fill="#2563eb")
    text = "H"
    try:
        font = ImageFont.truetype("segoeui.ttf", size // 2)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 2), text, fill="white", font=font)
    return image
