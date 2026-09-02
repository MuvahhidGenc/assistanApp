"""User correction / repair intents for wrong file or folder selection."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hermes.context.agent_context import (
    find_file_in_folder_hint,
    find_folder_by_hint,
    normalize_existing_path,
    path_exists,
    promote_file_in_context,
)
from hermes.context.conversational_context import ConversationalContext

_CORRECTION = re.compile(
    r"(?:"
    r"yanl[iıIİ][şs]\s+(?:dosya(?:y[ıi])?\s+)?a[çc]t[ıi]n|"
    r"yanlis\s+dosya(?:yi|yi)?\s+actin|"
    r"hay[ıi]r\s*,?\s*(?:o\s+)?de[gğ]il|"
    r"onu\s+de[gğ]il|"
    r"dogru\s+degil|"
    r"do[gğ]ru\s+de[gğ]il"
    r")",
    re.IGNORECASE,
)

_FOLDER_HINT = re.compile(
    r"\b([A-Za-z0-9_][\w\d_-]{1,40})\s+olmal[ıi]yd[ıi]\b",
    re.IGNORECASE,
)

_EXPLICIT_CORRECTION_OPEN = re.compile(
    r"(?:hay[ıi]r\s*,?\s*)?"
    r"(?:"
    r"([A-Za-z0-9_][\w\d_-]{0,40})\s*['']?deki\s+"
    r"([\w\d_.\-]+(?:\.(?:txt|md|docx))?)\s+dosya(?:sını|sini|yı|yi|sin[iı]|s[iı]n[iı])?\s+a[çc]|"
    r"([A-Za-z0-9_][\w\d_-]{0,40})\s*klas(?:o|ö)r(?:u|ü|nde|ünde)?(?:ki)?\s+"
    r"([\w\d_.\-]+\.(?:txt|md|docx))\s*(?:dosya(?:sını|sini|yı|yi)?\s+)?a[çc]"
    r")",
    re.IGNORECASE,
)

_BIR_ONCEKI = re.compile(
    r"bir\s+onceki\s+(?:olusturdugun|oluşturduğun|)\s*dosya(?:y[ıi])?\s+a[çc]",
    re.IGNORECASE,
)


@dataclass
class CorrectionResult:
    handled: bool = False
    response: str = ""
    repeat_message: str = ""
    corrected_path: str | None = None


def is_user_correction_message(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    if _CORRECTION.search(normalized):
        return True
    if _EXPLICIT_CORRECTION_OPEN.search(normalized):
        return True
    if _BIR_ONCEKI.search(normalized):
        return True
    if re.search(r"^hay[ıi]r\s*,", normalized, re.IGNORECASE) and re.search(
        r"dosya.*a[çc]|a[çc].*dosya", normalized, re.IGNORECASE
    ):
        return True
    if _FOLDER_HINT.search(normalized) and re.search(
        r"yanl[iıIİ][şs]|dogru\s+degil|do[gğ]ru\s+de[gğ]il|olmal[ıi]yd[ıi]",
        normalized,
        re.IGNORECASE,
    ):
        return True
    return False


def resolve_user_correction(text: str, ctx: ConversationalContext) -> CorrectionResult:
    normalized = (text or "").strip()
    if not is_user_correction_message(normalized):
        return CorrectionResult()

    explicit = _EXPLICIT_CORRECTION_OPEN.search(normalized)
    if explicit:
        folder_hint = explicit.group(1) or explicit.group(3) or ""
        file_hint = explicit.group(2) or explicit.group(4) or ""
        if folder_hint and file_hint:
            resolved = find_file_in_folder_hint(ctx, folder_hint, file_hint)
            if not resolved and "." not in file_hint:
                resolved = find_file_in_folder_hint(ctx, folder_hint, f"{file_hint}.txt")
            if resolved:
                promote_file_in_context(ctx, resolved)
                ctx.save()
                name = Path(resolved).name
                return CorrectionResult(
                    handled=True,
                    response=f"Tamam, {folder_hint} klasorundeki {name} dosyasini aciyorum.",
                    repeat_message=f"{name} dosyasini ac",
                    corrected_path=resolved,
                )

    folder_hint_match = _FOLDER_HINT.search(normalized)
    folder_hint = folder_hint_match.group(1) if folder_hint_match else None

    if not folder_hint:
        for folder in ctx.recent_folders[:5]:
            name = Path(str(folder)).name
            if name and re.search(re.escape(name), normalized, re.IGNORECASE):
                folder_hint = name
                break

    if folder_hint:
        folder_path = find_folder_by_hint(ctx, folder_hint)
        if folder_path:
            file_path = find_file_in_folder_hint(ctx, folder_hint, "rapor.txt")
            if not file_path:
                for candidate in ctx.recent_files + ctx.created_files:
                    if not candidate:
                        continue
                    try:
                        if Path(str(candidate)).parent.resolve() == Path(folder_path).resolve():
                            if path_exists(candidate):
                                file_path = str(Path(str(candidate)).resolve())
                                break
                    except OSError:
                        continue
            if not file_path:
                folder = Path(folder_path)
                if folder.is_dir():
                    txts = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if txts:
                        file_path = str(txts[0].resolve())
            if file_path:
                promote_file_in_context(ctx, file_path)
                ctx.save()
                name = Path(file_path).name
                return CorrectionResult(
                    handled=True,
                    response=f"Tamam, {folder_hint} klasorundeki {name} dosyasini aciyorum.",
                    repeat_message=f"{name} dosyasini ac",
                    corrected_path=file_path,
                )

    if _BIR_ONCEKI.search(normalized) and len(ctx.recent_files) >= 2:
        chosen = ctx.recent_files[1]
        verified = normalize_existing_path(chosen)
        if verified:
            promote_file_in_context(ctx, verified)
            ctx.save()
            return CorrectionResult(
                handled=True,
                response=f"Tamam, {Path(verified).name} dosyasini aciyorum.",
                repeat_message=f"{Path(verified).name} dosyasini ac",
                corrected_path=verified,
            )

    if ctx.recent_disambiguation_options and len(ctx.recent_disambiguation_options) >= 2:
        chosen = ctx.recent_disambiguation_options[1]
        msg = chosen.get("message") or chosen.get("label") or ""
        if msg:
            ctx.recent_disambiguation_options = (
                ctx.recent_disambiguation_options[1:] + ctx.recent_disambiguation_options[:1]
            )
            ctx.save()
            return CorrectionResult(
                handled=True,
                response="Tamam, diger secenegi deniyorum.",
                repeat_message=msg,
            )

    for candidate in ctx.recent_files:
        verified = normalize_existing_path(candidate)
        if not verified:
            continue
        if ctx.last_opened_file and verified.casefold() == ctx.last_opened_file.casefold():
            continue
        promote_file_in_context(ctx, verified)
        ctx.save()
        return CorrectionResult(
            handled=True,
            response=f"Tamam, {Path(verified).name} dosyasini aciyorum.",
            repeat_message=f"{Path(verified).name} dosyasini ac",
            corrected_path=verified,
        )

    return CorrectionResult(
        handled=True,
        response="Hangi dosyayi kastettigini netlestirir misin?",
    )
