"""Phase 10.1 — filesystem / browser / PDF reality checks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_IMPORTANCE_KEYWORDS = re.compile(
    r"\b(?:fatura|invoice|sozlesme|sözleşme|contract|rapor|report|acil|urgent|"
    r"vergi|tax|onemli|önemli|critical|deadline|son\s+tarih|odeme|ödeme|payment)\b",
    re.IGNORECASE,
)
_WINDOW_TITLE_NOISE = re.compile(
    r"^(?:google chrome|chrome|microsoft edge|edge|mozilla firefox|firefox|"
    r"internet explorer|yeni sekme|new tab)\s*[-—|]?\s*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PdfExtractResult:
    path: str
    text: str
    ok: bool
    error: str = ""


@dataclass(frozen=True)
class SnapshotDiff:
    moved: list[str]
    created: list[str]
    removed: list[str]
    unchanged: bool


def filesystem_snapshot(folder: Path) -> dict[str, Any]:
    if not folder.is_dir():
        return {"path": str(folder), "files": {}, "file_count": 0}
    files: dict[str, int] = {}
    for entry in folder.iterdir():
        if entry.is_file():
            try:
                files[entry.name] = entry.stat().st_size
            except OSError:
                files[entry.name] = -1
    return {
        "path": str(folder.resolve()),
        "files": files,
        "file_count": len(files),
    }


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> SnapshotDiff:
    before_files = dict(before.get("files") or {})
    after_files = dict(after.get("files") or {})
    before_names = set(before_files)
    after_names = set(after_files)
    removed = sorted(before_names - after_names)
    created = sorted(after_names - before_names)
    moved: list[str] = []
    for name in before_names & after_names:
        if before_files.get(name) != after_files.get(name):
            moved.append(name)
    unchanged = not removed and not created and not moved
    return SnapshotDiff(moved=moved, created=created, removed=removed, unchanged=unchanged)


def verify_file_exists(path: str | Path, *, min_size: int = 1) -> dict[str, Any]:
    target = Path(str(path))
    if not target.is_file():
        return {"ok": False, "reason": "file_missing", "path": str(target)}
    try:
        size = target.stat().st_size
    except OSError as exc:
        return {"ok": False, "reason": str(exc), "path": str(target)}
    if size < min_size:
        return {"ok": False, "reason": "file_empty", "path": str(target), "size": size}
    return {"ok": True, "path": str(target.resolve()), "size": size}


def verify_copy_result(source: Path, destination: Path) -> dict[str, Any]:
    if not destination.is_file():
        return {"ok": False, "reason": "destination_missing", "destination": str(destination)}
    if not source.is_file():
        return {"ok": True, "reason": "source_already_moved", "destination": str(destination)}
    try:
        if destination.stat().st_size != source.stat().st_size:
            return {
                "ok": False,
                "reason": "size_mismatch",
                "source": str(source),
                "destination": str(destination),
            }
    except OSError as exc:
        return {"ok": False, "reason": str(exc)}
    return {"ok": True, "source": str(source), "destination": str(destination)}


def verify_move_result(source: Path, destination: Path) -> dict[str, Any]:
    if source.exists():
        return {"ok": False, "reason": "source_still_exists", "source": str(source)}
    if not destination.is_file():
        return {"ok": False, "reason": "destination_missing", "destination": str(destination)}
    return {"ok": True, "destination": str(destination.resolve())}


def safe_move_file(source: Path, destination: Path) -> dict[str, Any]:
    """Copy to destination, verify, then remove source. Source kept if verification fails."""
    import shutil

    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        return {"ok": False, "reason": "source_missing", "source": str(source), "intent": "MOVE"}
    if destination.is_file() and destination.stat().st_size == source.stat().st_size:
        check = verify_copy_result(source, destination)
        if check.get("ok"):
            try:
                source.unlink()
            except OSError as exc:
                return {
                    "ok": False,
                    "reason": f"source_delete_failed: {exc}",
                    "source": str(source),
                    "destination": str(destination),
                    "intent": "MOVE",
                }
            move_check = verify_move_result(source, destination)
            return {
                "ok": move_check.get("ok"),
                "source": str(source.resolve()),
                "destination": str(destination.resolve()),
                "intent": "MOVE",
                "reason": move_check.get("reason"),
            }
    try:
        shutil.copy2(source, destination)
    except OSError as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "source": str(source),
            "destination": str(destination),
            "intent": "MOVE",
        }
    check = verify_copy_result(source, destination)
    if not check.get("ok"):
        return {
            "ok": False,
            "reason": check.get("reason"),
            "source": str(source),
            "destination": str(destination),
            "intent": "MOVE",
        }
    try:
        source.unlink()
    except OSError as exc:
        return {
            "ok": False,
            "reason": f"source_delete_failed: {exc}",
            "source": str(source),
            "destination": str(destination),
            "intent": "MOVE",
        }
    move_check = verify_move_result(source, destination)
    return {
        "ok": bool(move_check.get("ok")),
        "source": str(source),
        "destination": str(destination.resolve()),
        "intent": "MOVE",
        "reason": move_check.get("reason"),
    }


def extract_pdf_text(path: str | Path) -> PdfExtractResult:
    target = Path(str(path))
    if not target.is_file():
        return PdfExtractResult(path=str(target), text="", ok=False, error="dosya bulunamadi")
    try:
        try:
            from pypdf import PdfReader  # type: ignore[import-untyped]

            reader = PdfReader(str(target))
            chunks: list[str] = []
            for page in reader.pages[:20]:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    chunks.append(page_text.strip())
            text = "\n".join(chunks).strip()
            if text:
                return PdfExtractResult(path=str(target.resolve()), text=text[:12000], ok=True)
        except ImportError:
            pass
        raw = target.read_bytes()[:500_000]
        text_parts = re.findall(rb"\(([^()\\]{3,200})\)", raw)
        decoded = " ".join(
            part.decode("latin-1", errors="ignore").strip()
            for part in text_parts[:80]
            if part.strip()
        ).strip()
        if len(decoded) >= 8:
            return PdfExtractResult(path=str(target.resolve()), text=decoded[:12000], ok=True)
        return PdfExtractResult(
            path=str(target.resolve()),
            text="",
            ok=False,
            error="PDF icerigi okunamadi",
        )
    except OSError as exc:
        return PdfExtractResult(path=str(target), text="", ok=False, error=str(exc))


def classify_pdf_as_important(text: str, filename: str, *, content_required: bool = True) -> bool:
    combined = f"{filename}\n{text}".casefold()
    if _IMPORTANCE_KEYWORDS.search(combined):
        return True
    if not content_required:
        return False
    # Long readable documents are more likely to matter than empty scans.
    return len(text.strip()) >= 200


def classify_pdf_by_filename(filename: str) -> bool:
    """Filename-only heuristic — used only as explicit replan fallback."""
    return classify_pdf_as_important("", filename, content_required=False) or bool(
        _IMPORTANCE_KEYWORDS.search(filename.casefold())
    )


def is_substantive_screen_text(
    text: str,
    *,
    window_title: str = "",
    lines: list[str] | None = None,
) -> bool:
    cleaned = (text or "").strip()
    title = strip_window_title_noise(window_title or "")
    title_cf = title.casefold()

    line_items = [str(line).strip() for line in (lines or []) if str(line).strip()]
    if line_items:
        filtered = [
            line
            for line in line_items
            if not title or line.casefold() not in {title_cf, f"{title_cf} - chrome", f"{title_cf} - google chrome"}
        ]
        if not filtered:
            filtered = line_items
        combined_lines = " ".join(filtered)
        alpha_lines = sum(1 for ch in combined_lines if ch.isalpha())
        if len(filtered) >= 2 and alpha_lines >= 12:
            return True
        if len(combined_lines) >= 20 and alpha_lines >= 10:
            return True
        if cleaned and len(cleaned) >= 20 and alpha_lines >= 10:
            return True

    if len(cleaned) < 20:
        return False
    if title and cleaned.casefold() == title_cf:
        return False
    if title and len(cleaned) <= len(title) + 8:
        return False
    alpha = sum(1 for ch in cleaned if ch.isalpha())
    if alpha < 15:
        return False
    return True


def normalize_visible_page_text(screen_output: dict[str, Any]) -> tuple[str, str]:
    """Return (text, error). Rejects window-title-only payloads."""
    window_title = str(screen_output.get("window_title") or screen_output.get("title") or "").strip()
    lines_raw = screen_output.get("lines") or []
    line_items = [str(line).strip() for line in lines_raw if str(line).strip()]
    text = str(screen_output.get("text") or "").strip()
    if line_items:
        joined = "\n".join(line_items).strip()
        if len(joined) > len(text):
            text = joined
    if not text and line_items:
        text = "\n".join(line_items).strip()
    if is_substantive_screen_text(text, window_title=window_title, lines=line_items):
        return text, ""
    err = str(screen_output.get("error") or "").strip()
    if err:
        return "", err
    if window_title and not text:
        return "", "Yalnizca pencere basligi okundu; sayfa metni alinamadi."
    return "", "Sayfa metni yeterince okunamadi."


def build_report_content(
    *,
    url: str = "",
    page_title: str = "",
    visible_text: str = "",
    extraction_timestamp: str = "",
) -> str:
    header = [
        "# Hermes Web Raporu",
        f"URL: {url or 'bilinmiyor'}",
        f"Baslik: {page_title or 'bilinmiyor'}",
        f"Okuma zamani: {extraction_timestamp or 'bilinmiyor'}",
        "",
        "## Gorunen metin",
        visible_text.strip() or "(metin okunamadi)",
    ]
    return "\n".join(header).strip()


def strip_window_title_noise(title: str) -> str:
    return _WINDOW_TITLE_NOISE.sub("", (title or "").strip()).strip() or title.strip()
