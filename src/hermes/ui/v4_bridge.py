"""Phase 1B minimal bridge — event/command separation.

Worker → Store via projected UI events (read-only).
No backend mutation; no new execution path.
"""
from __future__ import annotations
from hermes.ui.v4_store import V4UIStore

_TR_TO_ASCII = str.maketrans({
    "ı": "i", "İ": "i",
    "ş": "s", "Ş": "s",
    "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u",
    "ö": "o", "Ö": "o",
    "ç": "c", "Ç": "c",
})

try:
    from hermes.voice.wake_word import normalize_voice_text as _ww_norm
except Exception:  # pragma: no cover
    def _ww_norm(s: str) -> str:
        import unicodedata as _u
        lowered = str(s or "").strip().casefold()
        norm = _u.normalize("NFKD", lowered)
        return "".join(ch for ch in norm if not _u.combining(ch))


def _norm_v(s: str) -> str:
    t = str(s or "").strip()
    t = t.translate(_TR_TO_ASCII)
    t = t.casefold()
    import unicodedata as _u
    norm = _u.normalize("NFKD", t)
    return "".join(ch for ch in norm if not _u.combining(ch))

_PHASE_FROM_STATUS = (
    ("dinliyorum", "listening"),
    ("dinle", "listening"),
    ("duydum", "listening"),
    ("calistir", "executing"),
    ("calis", "executing"),
    ("adim", "executing"),
    ("aciyor", "executing"),
    ("ac", "executing"),
    ("anladim", "thinking"),
    ("isliyorum", "thinking"),
    ("gonderiliyor", "thinking"),
    ("isleniyor", "thinking"),
    ("dusun", "thinking"),
    ("dusunuyorum", "thinking"),
    ("hazir", "idle"),
    ("tamamlandi", "completed"),
    ("bitti", "completed"),
)


def _phase_from_status_message(message: str, fallback: str = "thinking") -> str:
    m_norm = _norm_v(message or "")
    m_cf = str(message or "").casefold()
    for key, phase in _PHASE_FROM_STATUS:
        if key in m_norm or key in m_cf:
            return phase
    return fallback


def bind_store_events(worker, store: V4UIStore) -> None:
    """Wire worker callbacks to V4UIStore subscription (not execution).

    - Preserves existing worker.on_event chain by wrapping the previous
      handler if one was installed — no other UI module loses its
      callback wiring.
    - Only projects READ-ONLY signals the canonical worker already
      emits; no backend payloads are mutated.
    """
    _prev_on_event = getattr(worker, "on_event", None)

    def _subscriber(evt: str, payload) -> None:
        # Pass-through so the previous worker.on_event handler (if any)
        # still fires — order: outer consumers first, store second.
        if _prev_on_event is not None and callable(_prev_on_event):
            try:
                _prev_on_event(evt, payload or {})
            except Exception:
                pass
        # Bridge: worker's canonical "message" emit → store chat list.
        # Roles: "user" | "assistant" | "status" | "system" (worker.py
        # L119/L313/L321/L377/L409/L473/L525/L543/L549).
        if evt == "message" and isinstance(payload, dict):
            role = str(payload.get("role") or "system")
            text = str(payload.get("text") or "").strip()
            if text:
                store.append_chat_message(
                    role,
                    text,
                    source=str(payload.get("source")) if payload.get("source") else None,
                )
        elif evt == "status":
            msg = payload.get("message") if isinstance(payload, dict) else str(payload or "")
            if msg:
                store.status_message = str(msg)
                new_phase = _phase_from_status_message(str(msg))
                store.phase = new_phase
                store._publish("status_update", {"message": str(msg), "phase": new_phase})
        elif evt == "state":
            try:
                snap = None
                if hasattr(worker, "state") and worker.state is not None and hasattr(worker.state, "snapshot"):
                    snap = worker.state.snapshot()
                elif isinstance(payload, dict):
                    snap = dict(payload)
                if isinstance(snap, dict):
                    store.connection_state = str(snap.get("connection") or store.connection_state)
                    store.connection_detail = str(snap.get("status_text") or store.connection_detail)
                    act = snap.get("activity")
                    if act:
                        store.activity = str(act)
                        if str(act) in {"listening", "thinking", "speaking", "executing", "awaiting_approval", "error", "idle"}:
                            store.phase = str(act)
                    store.voice_enabled = bool(snap.get("voice_enabled", store.voice_enabled))
                    store.wake_word_enabled = bool(snap.get("wake_word_enabled", store.wake_word_enabled))
                    store.microphone_available = bool(snap.get("microphone_available", store.microphone_available))
                    store._publish("state_update", snap)
            except Exception:
                pass
        elif evt == "task_completed":
            try:
                text_val = (payload or {}).get("text") if isinstance(payload, dict) else payload
                store.task_completed_count += 1
                record = {
                    "text": str(text_val or ""),
                    "index": store.task_completed_count,
                }
                store.completed_tasks.append(record)
                store.phase = "completed"
                store._publish("task_completed", record)
            except Exception:
                pass
        elif evt == "approval_required":
            store.approval_state = "required"
        elif evt == "approval_resolved":
            store.approval_state = "resolved"
        elif evt == "approval_timeout":
            store.approval_state = "timeout"
        elif evt == "error":
            msg = payload.get("message") if isinstance(payload, dict) else str(payload)
            if msg:
                store.append_chat_message("system", msg)
                store.error_state = str(msg)
                store.phase = "failed"
                store._publish("error_update", {"message": str(msg)})

    worker.on_event = _subscriber
