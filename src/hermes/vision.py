from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hermes.utils.logging import get_logger

logger = get_logger(__name__)

_TESSERACT_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)
_TESSDATA_URL = "https://github.com/tesseract-ocr/tessdata/raw/main/{lang}.traineddata"


def _user_tessdata_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", ".")) / "HermesClient" / "tessdata"


def ensure_tessdata(langs: tuple[str, ...] = ("eng", "tur")) -> Path:
    dest = _user_tessdata_dir()
    dest.mkdir(parents=True, exist_ok=True)
    system_dir = Path(r"C:\Program Files\Tesseract-OCR\tessdata")
    for lang in langs:
        target = dest / f"{lang}.traineddata"
        if target.exists() and target.stat().st_size > 1000:
            continue
        system_file = system_dir / f"{lang}.traineddata"
        if system_file.exists():
            target.write_bytes(system_file.read_bytes())
            continue
        try:
            from urllib.request import urlretrieve

            urlretrieve(_TESSDATA_URL.format(lang=lang), target)
        except Exception as exc:
            logger.info("tessdata_download_skip", lang=lang, error=str(exc))
    return dest


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.04
    return pyautogui


def _configure_tesseract() -> str:
    import pytesseract

    configured = str(getattr(pytesseract.pytesseract, "tesseract_cmd", "") or "")
    if configured and Path(configured).is_file():
        return configured
    env = os.environ.get("TESSERACT_CMD", "").strip()
    if env and Path(env).is_file():
        pytesseract.pytesseract.tesseract_cmd = env
        return env
    for candidate in _TESSERACT_CANDIDATES:
        if Path(candidate).is_file():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return candidate
    return configured


def capture_screen(region: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    """Take a screenshot and return path + size."""
    from hermes.tools.windows.input_backend import take_screenshot

    return take_screenshot(region=region)


def preprocess_for_ocr(image: Any) -> Any:
    try:
        import cv2
        import numpy as np

        array = np.array(image)
        if array.ndim == 3:
            gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
        else:
            gray = array
        scaled = cv2.resize(gray, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return thresh
    except Exception as exc:
        logger.info("vision_opencv_skip", error=str(exc))
        return image


def ocr_image(image: Any, lang: str = "tur+eng") -> str:
    import pytesseract

    _configure_tesseract()
    tessdata = ensure_tessdata()
    os.environ["TESSDATA_PREFIX"] = str(tessdata.parent) + os.sep
    config = f"--tessdata-dir {tessdata.as_posix()}"
    prepared = preprocess_for_ocr(image)
    try:
        text = pytesseract.image_to_string(prepared, lang=lang, config=config)
    except Exception:
        text = pytesseract.image_to_string(prepared, lang="eng", config=config)
    return str(text or "").strip()


def ocr_screen(
    region: tuple[int, int, int, int] | None = None,
    lang: str = "tur+eng",
    *,
    include_boxes: bool = False,
) -> dict[str, Any]:
    from PIL import Image

    shot = capture_screen(region=region)
    path = str(shot.get("path") or "")
    text = ""
    error = ""
    boxes: list[dict[str, Any]] = []
    if path and Path(path).is_file():
        try:
            image = Image.open(path)
            text = ocr_image(image, lang=lang)[:4000]
            if include_boxes:
                boxes = ocr_word_boxes(image, lang=lang)
        except Exception as exc:
            error = str(exc)
            logger.warning("vision_ocr_failed", error=error)
    return {
        "screenshot": shot,
        "text": text,
        "ocr": bool(text),
        "error": error,
        "boxes": boxes,
        "lines": _boxes_to_lines(boxes) if boxes else [],
    }


def ocr_word_boxes(image: Any, lang: str = "tur+eng") -> list[dict[str, Any]]:
    import pytesseract

    _configure_tesseract()
    tessdata = ensure_tessdata()
    os.environ["TESSDATA_PREFIX"] = str(tessdata.parent) + os.sep
    config = f"--tessdata-dir {tessdata.as_posix()}"
    prepared = preprocess_for_ocr(image)
    try:
        data = pytesseract.image_to_data(
            prepared,
            lang=lang,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        data = pytesseract.image_to_data(
            prepared,
            lang="eng",
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    boxes: list[dict[str, Any]] = []
    count = len(data.get("text") or [])
    for index in range(count):
        word = str(data["text"][index] or "").strip()
        try:
            conf = int(float(data["conf"][index]))
        except (TypeError, ValueError):
            conf = -1
        if not word or conf < 35:
            continue
        boxes.append(
            {
                "text": word,
                "x": int(data["left"][index]),
                "y": int(data["top"][index]),
                "w": int(data["width"][index]),
                "h": int(data["height"][index]),
                "conf": conf,
                "line": int(data["line_num"][index]),
                "block": int(data["block_num"][index]),
            }
        )
    return boxes


def _group_box_lines(boxes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for box in boxes:
        key = (int(box.get("block") or 0), int(box.get("line") or 0))
        grouped.setdefault(key, []).append(box)
    lines: list[dict[str, Any]] = []
    for items in grouped.values():
        items.sort(key=lambda item: int(item["x"]))
        text = " ".join(str(item.get("text") or "") for item in items).strip()
        if not text:
            continue
        xs = [int(item["x"]) for item in items]
        ys = [int(item["y"]) for item in items]
        x2 = max(int(item["x"]) + int(item["w"]) for item in items)
        y2 = max(int(item["y"]) + int(item["h"]) for item in items)
        lines.append(
            {
                "text": text,
                "x": int((min(xs) + x2) / 2),
                "y": int((min(ys) + y2) / 2),
            }
        )
    return lines


def _boxes_to_lines(boxes: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("text") or "") for item in _group_box_lines(boxes)]


def find_text_on_screen(
    query: str,
    *,
    partial: bool = True,
    lang: str = "tur+eng",
) -> dict[str, Any] | None:
    """OCR ile ekranda metin ara; merkez koordinat dondur."""
    from PIL import Image

    cleaned = (query or "").strip()
    if len(cleaned) < 2:
        return None
    shot = capture_screen()
    path = str(shot.get("path") or "")
    if not path or not Path(path).is_file():
        return None
    image = Image.open(path)
    boxes = ocr_word_boxes(image, lang=lang)
    lines = _group_box_lines(boxes)
    needle = cleaned.casefold()
    best: dict[str, Any] | None = None
    best_score = 0.0

    def score_match(candidate: str) -> float:
        cand = candidate.casefold()
        if cand == needle:
            return 1.0
        if partial and needle in cand:
            return 0.85 + min(0.14, len(needle) / max(len(cand), 1))
        if partial:
            query_words = [w for w in needle.split() if len(w) > 2]
            if query_words and sum(1 for w in query_words if w in cand) >= max(1, len(query_words) - 1):
                return 0.72
        return 0.0

    for line in lines:
        rating = score_match(str(line.get("text") or ""))
        if rating > best_score:
            best_score = rating
            best = {**line, "match": cleaned, "score": rating}

    if best:
        scale = 1.0 / 1.6
        best["x"] = int(best["x"] * scale)
        best["y"] = int(best["y"] * scale)
        return best
    return None


def move_mouse(x: int, y: int) -> dict[str, int]:
    _pyautogui().moveTo(int(x), int(y), duration=0.15)
    return {"x": int(x), "y": int(y)}


def click(x: int | None = None, y: int | None = None, button: str = "left") -> dict[str, Any]:
    gui = _pyautogui()
    if x is not None and y is not None:
        gui.click(int(x), int(y), button=button)
        return {"x": int(x), "y": int(y), "button": button}
    gui.click(button=button)
    return {"button": button}


def screen_size() -> dict[str, int]:
    width, height = _pyautogui().size()
    return {"width": int(width), "height": int(height)}
