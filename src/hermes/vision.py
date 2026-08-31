from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def capture_screen(*, region: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    """Capture a screenshot and return metadata including file path."""
    from PIL import ImageGrab

    bbox = None
    if region:
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)
    image = ImageGrab.grab(bbox=bbox)
    path = Path(tempfile.gettempdir()) / f"hermes_screen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    image.save(path, format="PNG")
    return {"path": str(path), "width": image.width, "height": image.height}


def ocr_screen(*, lang: str = "tur+eng") -> dict[str, Any]:
    """Run OCR on the current screen."""
    shot = capture_screen()
    path = shot.get("path", "")
    if not path or not Path(path).is_file():
        return {"ocr": False, "text": "", "path": path}

    try:
        import pytesseract
        from PIL import Image

        text = pytesseract.image_to_string(Image.open(path), lang=lang)
        cleaned = text.strip()
        return {"ocr": bool(cleaned), "text": cleaned, "path": path}
    except Exception:
        return {"ocr": False, "text": "", "path": path}


def find_text_on_screen(text: str) -> dict[str, Any]:
    """Locate text on screen using OCR bounding boxes."""
    shot = capture_screen()
    path = shot.get("path", "")
    if not path:
        return {"found": False, "text": text}

    try:
        import pytesseract
        from PIL import Image

        data = pytesseract.image_to_data(Image.open(path), output_type=pytesseract.Output.DICT)
        target = text.lower()
        for i, word in enumerate(data.get("text", [])):
            if word and target in word.lower():
                x = data["left"][i]
                y = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]
                cx = x + w // 2
                cy = y + h // 2
                import pyautogui

                pyautogui.click(cx, cy)
                return {"found": True, "text": text, "x": cx, "y": cy}
        return {"found": False, "text": text}
    except Exception as exc:
        return {"found": False, "text": text, "error": str(exc)}
