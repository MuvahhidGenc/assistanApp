"""Meta-conversation handlers: what did you do, repeat, disambiguation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hermes.context.conversational_context import ConversationalContext

if TYPE_CHECKING:
    from hermes.mission.store import MissionStore


@dataclass
class ConversationTurn:
    handled: bool = False
    response: str = ""
    repeat_message: str = ""
    source: str = ""


_WHAT_DID = re.compile(
    r"^(?:ne\s+yapt[ıi]n|ne\s+yaptin|neler\s+yapt[ıi]n|"
    r"az\s+önce\s+ne\s+yapt[ıi]n|az\s+once\s+ne\s+yaptin|"
    r"son\s+islem|son\s+işlem)\??$",
    re.IGNORECASE,
)
_REPEAT = re.compile(
    r"^(?:bir\s+daha\s+yap|tekrar\s+yap|ayn[ıi]sini\s+yap|yeniden\s+yap)\.?$",
    re.IGNORECASE,
)
_REJECT = re.compile(
    r"^(?:hay[ıi]r\s+onu\s+de[gğ]il|onu\s+de[gğ]il|di[gğ]erini|öbürünü|oburunu)\.?$",
    re.IGNORECASE,
)
_LAST_FILE_NAME = re.compile(
    r"^son\s+olusturdu(?:gumuz|gun)\s+dosya(?:nin|nın)?\s+ad(?:i|ı)\s+ne\??$",
    re.IGNORECASE,
)
_FILE_CONTENT_QUERY = re.compile(
    r"(?:icinde|içinde)\s+ne\s+yaz|icerigini\s+oku|içeriğini\s+oku|icerigi\s+oku|"
    r"içeriği\s+oku|ne\s+yaziyor|ne\s+yazıyor",
    re.IGNORECASE,
)
_MISSION_PROGRESS = re.compile(
    r"^(?:neredeyiz|g[oö]rev\s+ne\s+durumda|ne\s+durumday(?:iz|ız)|"
    r"nerede\s+kald(?:ik|ık)|devam\s+edebilir\s+misin)\??$",
    re.IGNORECASE,
)
_MISSION_REMAINING = re.compile(
    r"^(?:ne\s+kald[ıi]|neyi\s+tamamlad(?:in|ın)|kalan\s+is|kalan\s+iş)\??$",
    re.IGNORECASE,
)
_LAST_OPERATION = re.compile(
    r"^(?:son\s+i[sş]lem\s+neydi|son\s+islem\s+ne)\??$",
    re.IGNORECASE,
)
_WRONG_FOLDER = re.compile(
    r"^(?:yanl[iıIİ][şs]\s+klas(?:o|ö)r|yanlis\s+klasor)\.?$",
    re.IGNORECASE,
)
_PREVIOUS_FILE = re.compile(
    r"^(?:bir\s+onceki\s+dosya(?:y[ıi])?\s+kastediyorum|"
    r"onceki\s+dosya(?:y[ıi])?\s+kastediyorum|"
    r"diger\s+dosya(?:y[ıi])?|diğer\s+dosya(?:y[ıi])?)\.?$",
    re.IGNORECASE,
)


_INTERNAL_SUMMARY_PATTERN = re.compile(
    r"\b("
    r"open_app|write_file|create_folder|rename_path|open_path|list_directory|"
    r"create_file|delete_path|copy_file|move_file|search_files|tool"
    r")\b|execution[_ ]target|\{|\}",
    re.IGNORECASE,
)


def is_user_facing_summary(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return _INTERNAL_SUMMARY_PATTERN.search(cleaned) is None


def handle_meta_conversation(
    message: str,
    ctx: ConversationalContext,
    store: MissionStore | None = None,
) -> ConversationTurn:
    text = (message or "").strip()
    if not text:
        return ConversationTurn()

    if _WHAT_DID.match(text):
        summary = ctx.natural_action_summary()
        if summary and not is_user_facing_summary(summary):
            summary = ""
        if not summary and store is not None:
            mission = _load_relevant_mission(ctx, store)
            if mission is not None:
                from hermes.agent.mission_progress import build_natural_mission_summary

                summary = build_natural_mission_summary(mission)
        if not summary:
            return ConversationTurn(
                handled=True,
                response="Henuz bu oturumda tamamlanan bir gorev kaydi yok.",
                source="meta_what_did",
            )
        return ConversationTurn(
            handled=True,
            response=summary,
            source="meta_what_did",
        )

    if _MISSION_PROGRESS.match(text) and store is not None:
        mission = _load_relevant_mission(ctx, store)
        if mission is not None:
            from hermes.agent.mission_progress import format_mission_progress

            return ConversationTurn(
                handled=True,
                response=format_mission_progress(mission),
                source="meta_mission_progress",
            )
        return ConversationTurn(
            handled=True,
            response=format_last_operation_fallback(ctx),
            source="meta_mission_progress",
        )

    if _MISSION_REMAINING.match(text) and store is not None:
        mission = _load_relevant_mission(ctx, store)
        if mission is not None:
            from hermes.agent.mission_progress import format_mission_remaining

            return ConversationTurn(
                handled=True,
                response=format_mission_remaining(mission),
                source="meta_mission_remaining",
            )
        from hermes.agent.mission_progress import format_last_operation

        return ConversationTurn(
            handled=True,
            response=format_last_operation(ctx),
            source="meta_mission_remaining",
        )

    if _LAST_OPERATION.match(text):
        from hermes.agent.mission_progress import format_last_operation

        return ConversationTurn(
            handled=True,
            response=format_last_operation(ctx),
            source="meta_last_operation",
        )

    if _WRONG_FOLDER.match(text):
        alts = [item for item in ctx.recent_folders if item != ctx.active_folder]
        if alts:
            chosen = alts[0]
            ctx.active_folder = chosen
            ctx.save()
            return ConversationTurn(
                handled=True,
                repeat_message=f"{Path(chosen).name} klasorunu ac",
                response=f"Tamam, {Path(chosen).name} klasorunu deniyorum.",
                source="meta_wrong_folder",
            )
        return ConversationTurn(
            handled=True,
            response="Hangi klasoru kastettigini netlestirir misin?",
            source="meta_wrong_folder",
        )

    if _PREVIOUS_FILE.match(text):
        alts = ctx.recent_files
        if len(alts) >= 2:
            chosen = alts[1]
            ctx.active_file = chosen
            ctx.save()
            return ConversationTurn(
                handled=True,
                repeat_message=f"{Path(chosen).name} dosyasini ac",
                response=f"Tamam, {Path(chosen).name} dosyasini deniyorum.",
                source="meta_previous_file",
            )
        return ConversationTurn(
            handled=True,
            response="Hangi dosyayi kastettigini netlestirir misin?",
            source="meta_previous_file",
        )

    if _LAST_FILE_NAME.match(text):
        for attr in ("last_renamed_file", "last_created_file", "active_file", "last_opened_file"):
            path = getattr(ctx, attr, None)
            if path:
                return ConversationTurn(
                    handled=True,
                    response=Path(str(path)).name,
                    source="meta_last_file_name",
                )
        return ConversationTurn(
            handled=True,
            response="Henuz olusturulmus bir dosya kaydi yok.",
            source="meta_last_file_name",
        )

    if _FILE_CONTENT_QUERY.search(text):
        from hermes.context.file_intent import resolve_read_file_path

        file_path = resolve_read_file_path(text, ctx)
        if file_path:
            try:
                content = Path(file_path).read_text(encoding="utf-8").strip()
            except OSError:
                content = ""
            if content:
                return ConversationTurn(
                    handled=True,
                    response=content if len(content) <= 4000 else content[:3997] + "...",
                    source="meta_file_content",
                )
        from hermes.context.agent_context import resolve_deictic_file

        resolved = resolve_deictic_file(ctx, text, intent="open_file")
        if resolved:
            try:
                content = Path(resolved.path).read_text(encoding="utf-8").strip()
            except OSError:
                content = ""
            if content:
                return ConversationTurn(
                    handled=True,
                    response=content,
                    source="meta_file_content",
                )
        return ConversationTurn(
            handled=True,
            response="Okuyacagim bir dosya bulamadim.",
            source="meta_file_content",
        )

    if _REPEAT.match(text):
        repeat = ctx.last_user_message or ""
        if not repeat:
            return ConversationTurn(
                handled=True,
                response="Tekrar edecegim bir onceki gorev bulamadim.",
                source="meta_repeat",
            )
        return ConversationTurn(
            handled=True,
            repeat_message=repeat,
            response="Tamam, onceki gorevi tekrarliyorum.",
            source="meta_repeat",
        )

    if _REJECT.match(text):
        alts = ctx.recent_disambiguation_options
        if len(alts) >= 2:
            chosen = alts[1]
            ctx.recent_disambiguation_options = alts[1:] + alts[:1]
            ctx.save()
            return ConversationTurn(
                handled=True,
                repeat_message=chosen.get("message") or chosen.get("label") or "",
                response=f"Tamam, {chosen.get('label', 'diger secenegi')} deniyorum.",
                source="meta_reject",
            )
        return ConversationTurn(
            handled=True,
            response="Hangi secenegi kastettigini netlestirir misin?",
            source="meta_reject",
        )

    return ConversationTurn()


def _load_relevant_mission(ctx: ConversationalContext, store: MissionStore) -> Any | None:
    from hermes.mission.models import MissionStatus

    active = store.load_active()
    if active is not None:
        return active
    if ctx.active_mission_id:
        loaded = store.load(ctx.active_mission_id)
        if loaded is not None:
            return loaded
    for mission_id in (ctx.last_successful_mission_id, ctx.last_failed_mission_id):
        if not mission_id:
            continue
        loaded = store.load(mission_id)
        if loaded is not None:
            return loaded
    for mission_id in reversed(store.list_mission_ids()):
        loaded = store.load(mission_id)
        if loaded is None:
            continue
        if loaded.status not in (MissionStatus.CANCELLED,):
            return loaded
    return None


def format_last_operation_fallback(ctx: ConversationalContext) -> str:
    from hermes.agent.mission_progress import format_last_operation

    return format_last_operation(ctx)
