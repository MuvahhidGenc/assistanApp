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
    promote_folder_in_context,
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
    invalidate_path: str | None = None


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


_REDIRECT = re.compile(
    r"hay[ıi]r|yanl[iı][şs]|de[gğ]il|olmal[ıi]yd[ıi]|olu[sş]turmal[ıi]",
    re.IGNORECASE,
)


def _compact_token(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _mentioned_known_folder(text: str, ctx: ConversationalContext) -> str | None:
    compact_text = _compact_token(text)
    candidates: list[str] = []
    for path in (
        ctx.last_created_folder,
        ctx.active_folder,
        ctx.focus_container(),
        *(ctx.recent_folders or []),
    ):
        if path and path not in candidates:
            candidates.append(path)
    for path in candidates:
        name = Path(str(path)).name
        if name and _compact_token(name) and _compact_token(name) in compact_text:
            existing = find_folder_by_hint(ctx, name) or normalize_existing_path(path)
            if existing:
                return existing
    from hermes.context.folder_reference import extract_named_folder_name

    named = extract_named_folder_name(text)
    if named:
        return find_folder_by_hint(ctx, named) or find_folder_by_hint(ctx, _compact_token(named))
    return None


def _original_goal(ctx: ConversationalContext) -> str:
    if ctx.current_objective:
        return str(ctx.current_objective)
    if isinstance(ctx.last_intent, dict) and ctx.last_intent.get("goal"):
        return str(ctx.last_intent.get("goal") or "")
    return ""


def _current_file_target(ctx: ConversationalContext) -> str | None:
    focus = ctx.active_focus
    if focus is not None and focus.type == "file":
        return focus.identifier
    return ctx.active_file


def _resolve_create_target_correction(text: str, ctx: ConversationalContext) -> CorrectionResult:
    from hermes.context.conversational_context import message_wants_container
    from hermes.context.folder_reference import message_uses_contextual_folder

    folder = _mentioned_known_folder(text, ctx)
    if not folder:
        return CorrectionResult()
    original = _original_goal(ctx)
    current_file = _current_file_target(ctx)
    wants_inside = message_uses_contextual_folder(text) or message_wants_container(text)
    redirects = bool(_REDIRECT.search(text))
    has_goal = bool(original or ctx.last_intent or ctx.active_mission_id)
    if not has_goal or not wants_inside:
        return CorrectionResult()

    wrong_parent = False
    if current_file:
        try:
            wrong_parent = Path(current_file).parent.resolve() != Path(folder).resolve()
        except OSError:
            wrong_parent = True
    if not (redirects or wrong_parent):
        return CorrectionResult()

    invalidate_path = current_file if current_file and wrong_parent else None
    if invalidate_path:
        ctx.invalidate_target(invalidate_path)
    promote_folder_in_context(ctx, folder)
    ctx.save()
    name = Path(folder).name
    if original and original.strip().casefold() != text.strip().casefold():
        return CorrectionResult(
            handled=True,
            response=f"Tamam, {name} klasorune gorevi yeniden planliyorum.",
            repeat_message=original,
            corrected_path=folder,
            invalidate_path=invalidate_path,
        )
    return CorrectionResult(
        handled=True,
        response=f"Tamam, hedefi {name} klasoru olarak guncelledim.",
        corrected_path=folder,
        invalidate_path=invalidate_path,
    )


def resolve_user_correction(text: str, ctx: ConversationalContext) -> CorrectionResult:
    normalized = (text or "").strip()
    retarget = _resolve_create_target_correction(normalized, ctx)
    if retarget.handled:
        return retarget
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
