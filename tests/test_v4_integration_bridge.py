"""V4 MUVAHHİD — minimal integration smoke tests.

No real Hermes/V3/STT/TTS service is started; only verifies the wiring
contract between the new V4 shell surface and the canonical worker path.
"""
from __future__ import annotations

import pytest

from hermes.ui.v4_store import V4UIStore
from hermes.ui.v4_bridge import bind_store_events, _phase_from_status_message
from hermes.ui.worker import WorkerCommand
from hermes.voice.wake_word import DEFAULT_WAKE_WORDS, detect_wake_word


class FakeWorkerState:
    def __init__(self, **fields):
        for k, v in fields.items():
            setattr(self, k, v)

    def snapshot(self):
        return {
            "connection": getattr(self, "connection", "unknown"),
            "status_text": getattr(self, "status_text", ""),
            "activity": getattr(self, "activity", "idle"),
            "voice_enabled": getattr(self, "voice_enabled", True),
            "wake_word_enabled": getattr(self, "wake_word_enabled", True),
            "microphone_available": getattr(self, "microphone_available", False),
        }


class FakeWorker:
    def __init__(self):
        self.on_event = None
        self.state = FakeWorkerState(
            connection="connected",
            status_text="Hazır",
            activity="idle",
            voice_enabled=True,
            wake_word_enabled=True,
            microphone_available=True,
        )


# ─────────── 1. V4UIStore new state fields + snapshot ───────────
def test_v4_store_new_state_fields_defaults():
    store = V4UIStore()
    assert store.connection_state == "unknown"
    assert store.voice_enabled is True
    assert store.wake_word_enabled is True
    assert store.microphone_available is False
    assert store.status_message == ""
    assert store.task_completed_count == 0
    snap = store.get_snapshot()
    for key in (
        "connection_state", "voice_enabled", "wake_word_enabled",
        "microphone_available", "status_message", "task_completed_count",
        "chat_message_count", "activity",
    ):
        assert key in snap, f"snapshot missing {key}"


def test_v4_store_publish_notifies_subscribers():
    store = V4UIStore()
    seen = []
    store.subscribe(lambda sig, payload: seen.append((sig, payload)))
    store._publish("custom_signal", {"a": 1})
    assert ("custom_signal", {"a": 1}) in seen


# ─────────── 2. v4_bridge extended events (status/state/task_completed) ──
def test_bind_store_events_message_still_works():
    worker = FakeWorker()
    store = V4UIStore()
    bind_store_events(worker, store)
    assert callable(worker.on_event)
    worker.on_event("message", {"role": "user", "text": "merhaba"})
    assert len(store.chat_messages) == 1
    assert store.chat_messages[0]["role"] == "user"
    assert "merhaba" in store.chat_messages[0]["text"]


def test_bind_store_events_approval_chain_preserved():
    worker = FakeWorker()
    store = V4UIStore()
    bind_store_events(worker, store)
    for evt, expected in (
        ("approval_required", "required"),
        ("approval_resolved", "resolved"),
        ("approval_timeout", "timeout"),
    ):
        worker.on_event(evt, {})
        assert store.approval_state == expected, f"{evt} → {expected}"


def test_bind_store_events_status_updates_phase():
    worker = FakeWorker()
    store = V4UIStore()
    bind_store_events(worker, store)
    worker.on_event("status", {"message": "Dinliyorum"})
    assert store.phase == "listening"
    assert store.status_message == "Dinliyorum"
    worker.on_event("status", {"message": "Çalıştırıyorum"})
    assert store.phase == "executing"
    worker.on_event("status", {"message": "Hazır"})
    assert store.phase == "idle"


def test_bind_store_events_state_syncs_connection_and_voice():
    worker = FakeWorker()
    store = V4UIStore()
    bind_store_events(worker, store)
    worker.state = FakeWorkerState(
        connection="connected",
        status_text="Sunucuya bağlı",
        activity="executing",
        voice_enabled=True,
        wake_word_enabled=True,
        microphone_available=True,
    )
    worker.on_event("state", {})
    assert store.connection_state == "connected"
    assert store.connection_detail == "Sunucuya bağlı"
    assert store.activity == "executing"
    assert store.phase == "executing"
    assert store.voice_enabled is True
    assert store.wake_word_enabled is True
    assert store.microphone_available is True


def test_bind_store_events_state_payload_fallback():
    worker = FakeWorker()
    store = V4UIStore()
    bind_store_events(worker, store)
    worker.state = None  # force payload fallback
    worker.on_event(
        "state",
        {
            "connection": "disconnected",
            "status_text": "Bağlantı yok",
            "activity": "error",
            "voice_enabled": False,
            "wake_word_enabled": False,
            "microphone_available": False,
        },
    )
    assert store.connection_state == "disconnected"
    assert store.voice_enabled is False


def test_bind_store_events_task_completed_increments_counter():
    worker = FakeWorker()
    store = V4UIStore()
    bind_store_events(worker, store)
    for i in range(3):
        worker.on_event("task_completed", {"text": f"görev {i+1}"})
    assert store.task_completed_count == 3
    assert len(store.completed_tasks) == 3
    assert store.phase == "completed"
    assert store.completed_tasks[-1]["text"] == "görev 3"


def test_bind_store_events_error_still_sets_phase_failed():
    worker = FakeWorker()
    store = V4UIStore()
    bind_store_events(worker, store)
    worker.on_event("error", {"message": "bir hata"})
    assert store.phase == "failed"
    assert store.error_state == "bir hata"
    assert any(m["text"] == "bir hata" for m in store.chat_messages)


def test_bind_store_events_chains_previous_handler():
    worker = FakeWorker()
    outer_events = []
    worker.on_event = lambda e, p: outer_events.append(e)
    store = V4UIStore()
    bind_store_events(worker, store)
    worker.on_event("message", {"role": "assistant", "text": "selam"})
    # previous handler fired first
    assert "message" in outer_events
    # store also updated
    assert len(store.chat_messages) == 1


def test_phase_from_status_message_fallback():
    assert _phase_from_status_message("Tamamen yeni mesaj") == "thinking"
    assert _phase_from_status_message("") == "thinking"
    assert _phase_from_status_message("işliyorum") == "thinking"
    assert _phase_from_status_message("Gönderiliyor...") == "thinking"
    assert _phase_from_status_message("Tamamlandı") == "completed"


# ─────────── 3. Wake-word: MUVAHHİD default ──────────────────
def test_default_wake_words_contains_muvahhid():
    assert "muvahhid" in DEFAULT_WAKE_WORDS


@pytest.mark.parametrize(
    "utterance",
    [
        "muvahhid tarih saat",
        "MUVAHHİD tarih saat",  # Turkish İ → normalize strips accent
        "Muvahhid  not Defteri ac",
        "hey muvahhid youtube aç",
        "selam muvahhid hava durumu",
    ],
)
def test_detect_wake_word_matches_muvahhid_variants(utterance):
    wake, rest = detect_wake_word(utterance, DEFAULT_WAKE_WORDS)
    assert wake == "muvahhid", f"wake not matched for: {utterance}"


def test_detect_wake_word_no_match_returns_none():
    wake, rest = detect_wake_word("selam naber", DEFAULT_WAKE_WORDS)
    assert wake is None


# ─────────── 4. Worker + VoiceAssistant public API contracts ───
def test_worker_command_press_to_talk_in_enum():
    assert "PRESS_TO_TALK" in WorkerCommand.__members__
    assert WorkerCommand.PRESS_TO_TALK.value == "press_to_talk"


def test_backgroundworker_has_press_to_talk_method():
    from hermes.ui.worker import BackgroundWorker
    w = BackgroundWorker()
    assert hasattr(w, "press_to_talk")
    assert callable(getattr(w, "press_to_talk"))


def test_voice_assistant_has_press_to_talk_public_api():
    # Import-only contract check: VoiceAssistant class exposes the wrapper.
    from hermes.voice.assistant import VoiceAssistant
    assert hasattr(VoiceAssistant, "press_to_talk")
    assert callable(getattr(VoiceAssistant, "press_to_talk"))


def test_backgroundworker_send_message_enqueues_no_crash_without_loop():
    from hermes.ui.worker import BackgroundWorker
    w = BackgroundWorker()
    # Without an event loop the command is silently dropped — must not raise.
    try:
        w.send_message("hello from smoke test")
    except Exception:
        pytest.fail("send_message raised unexpectedly without running loop")
    # Voice API also shouldn't raise without loop.
    try:
        w.set_voice_enabled(True)
        w.set_wake_word_enabled(True)
        w.press_to_talk("muvahhid", "")
    except Exception:
        pytest.fail("voice control raised unexpectedly without running loop")


# ─────────── 5. V4Chat signature + _on_mic contracts ───────
def test_v4chat_constructor_signature_accepts_worker_kwarg():
    import inspect
    from hermes.ui.v4_chat import V4Chat

    sig = inspect.signature(V4Chat.__init__)
    params = list(sig.parameters.keys())
    # Must accept worker keyword (4th positional after self/parent/store).
    assert "worker" in params, f"V4Chat.__init__ params: {params}"


def test_v4chat__on_mic_no_worker_emits_status_via_store():
    """Without a connected worker, _on_mic must write a status bubble
    through the canonical store path (no crash)."""
    try:
        import customtkinter as _ctk  # noqa: F401
    except Exception:  # pragma: no cover
        pytest.skip("customtkinter not available")
    from hermes.ui.v4_store import V4UIStore
    from hermes.ui.v4_chat import V4Chat

    s = V4UIStore()

    class _Stub:
        pass

    p = _Stub()
    p._worker = None
    p._voice_last_state = {"voice": False, "wake": False}
    p.store = s
    p.tile_mode = None
    V4Chat._on_mic(p)
    assert len(s.chat_messages) >= 1
    roles = [m["role"] for m in s.chat_messages]
    assert "status" in roles
    any_status = next(m for m in s.chat_messages if m["role"] == "status")
    assert "arka plan servisi" in str(any_status["text"]).lower()


def test_backgroundworker_public_api_contract():
    """Full surface used by V4 pages must be available on the worker."""
    from hermes.ui.worker import BackgroundWorker
    w = BackgroundWorker()
    required_methods = (
        "start",
        "stop",
        "send_message",
        "set_voice_enabled",
        "set_wake_word_enabled",
        "resolve_approval",
        "press_to_talk",
        "reload_config",
        "refresh_connection",
    )
    for name in required_methods:
        fn = getattr(w, name, None)
        assert fn is not None, f"BackgroundWorker missing {name}"
        assert callable(fn), f"BackgroundWorker.{name} not callable"
