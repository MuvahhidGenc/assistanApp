from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock
from typing import Any, Callable


class ConnectionStatus(StrEnum):
    UNKNOWN = "unknown"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class ActivityMode(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    EXECUTING = "executing"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass
class ChatMessage:
    role: str
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class UIState:
    """Thread-safe shared UI state."""

    connection: ConnectionStatus = ConnectionStatus.UNKNOWN
    voice_enabled: bool = True
    wake_word_enabled: bool = True
    microphone_available: bool = False
    busy: bool = False
    activity: ActivityMode = ActivityMode.IDLE
    status_text: str = "Hazir"
    audio_level: float = 0.0
    messages: list[ChatMessage] = field(default_factory=list)
    mission_id: str | None = None
    mission_status: str | None = None
    mission_goal: str | None = None
    mission_progress: float = 0.0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connection": self.connection.value,
                "voice_enabled": self.voice_enabled,
                "wake_word_enabled": self.wake_word_enabled,
                "microphone_available": self.microphone_available,
                "busy": self.busy,
                "activity": self.activity.value,
                "status_text": self.status_text,
                "message_count": len(self.messages),
                "mission_id": self.mission_id,
                "mission_status": self.mission_status,
                "mission_goal": self.mission_goal,
                "mission_progress": self.mission_progress,
            }

    def set_connection(self, status: ConnectionStatus, *, detail: str = "") -> None:
        with self._lock:
            self.connection = status
            if detail:
                self.status_text = detail

    def set_busy(self, busy: bool, *, status: str | None = None, activity: ActivityMode | None = None) -> None:
        with self._lock:
            self.busy = busy
            if status is not None:
                self.status_text = status
            if activity is not None:
                self.activity = activity
            elif not busy and self.activity not in (
                ActivityMode.LISTENING,
                ActivityMode.AWAITING_APPROVAL,
            ):
                self.activity = ActivityMode.IDLE

    def set_activity(self, activity: ActivityMode, *, status: str | None = None) -> None:
        with self._lock:
            self.activity = activity
            if status is not None:
                self.status_text = status
            self.busy = activity not in (ActivityMode.IDLE, ActivityMode.LISTENING)
            energy = {
                ActivityMode.LISTENING: 0.72,
                ActivityMode.SPEAKING: 0.88,
                ActivityMode.THINKING: 0.45,
                ActivityMode.EXECUTING: 0.55,
                ActivityMode.AWAITING_APPROVAL: 0.35,
                ActivityMode.IDLE: 0.12,
            }.get(activity, 0.2)
            self.audio_level = max(self.audio_level * 0.6, energy)

    def set_audio_level(self, level: float) -> None:
        with self._lock:
            self.audio_level = max(0.0, min(1.0, float(level)))

    def set_voice(
        self,
        *,
        voice_enabled: bool | None = None,
        wake_word_enabled: bool | None = None,
    ) -> None:
        with self._lock:
            if voice_enabled is not None:
                self.voice_enabled = voice_enabled
            if wake_word_enabled is not None:
                self.wake_word_enabled = wake_word_enabled

    def set_microphone_available(self, available: bool) -> None:
        with self._lock:
            self.microphone_available = available

    def set_mission_snapshot(
        self,
        *,
        mission_id: str | None = None,
        mission_status: str | None = None,
        mission_goal: str | None = None,
        mission_progress: float | None = None,
    ) -> None:
        with self._lock:
            if mission_id is not None:
                self.mission_id = mission_id or None
            if mission_status is not None:
                self.mission_status = mission_status or None
            if mission_goal is not None:
                self.mission_goal = mission_goal or None
            if mission_progress is not None:
                self.mission_progress = max(0.0, min(1.0, float(mission_progress)))

    def clear_mission_snapshot(self) -> None:
        with self._lock:
            self.mission_id = None
            self.mission_status = None
            self.mission_goal = None
            self.mission_progress = 0.0

    def append_message(self, role: str, text: str) -> ChatMessage:
        msg = ChatMessage(role=role, text=text.strip())
        with self._lock:
            self.messages.append(msg)
        return msg

    def connection_label(self) -> str:
        labels = {
            ConnectionStatus.CONNECTED: "Bagli",
            ConnectionStatus.CONNECTING: "Baglaniyor...",
            ConnectionStatus.DISCONNECTED: "Baglanti yok",
            ConnectionStatus.ERROR: "Hata",
            ConnectionStatus.UNKNOWN: "Bilinmiyor",
        }
        return labels.get(self.connection, self.connection.value)

    def connection_color(self) -> str:
        colors = {
            ConnectionStatus.CONNECTED: "#22c55e",
            ConnectionStatus.CONNECTING: "#eab308",
            ConnectionStatus.DISCONNECTED: "#ef4444",
            ConnectionStatus.ERROR: "#ef4444",
            ConnectionStatus.UNKNOWN: "#94a3b8",
        }
        return colors.get(self.connection, "#94a3b8")

    def activity_label(self) -> str:
        labels = {
            ActivityMode.IDLE: "Hazir",
            ActivityMode.LISTENING: "Dinliyorum...",
            ActivityMode.THINKING: "Dusunuyorum...",
            ActivityMode.SPEAKING: "Konusuyorum...",
            ActivityMode.EXECUTING: "Calistiriyorum...",
            ActivityMode.AWAITING_APPROVAL: "Onay bekleniyor",
        }
        if self.status_text and self.activity != ActivityMode.IDLE:
            return self.status_text
        return labels.get(self.activity, self.activity.value)

    def activity_color(self) -> str:
        colors = {
            ActivityMode.IDLE: "#22d3ee",
            ActivityMode.LISTENING: "#4ade80",
            ActivityMode.THINKING: "#fbbf24",
            ActivityMode.SPEAKING: "#c084fc",
            ActivityMode.EXECUTING: "#38bdf8",
            ActivityMode.AWAITING_APPROVAL: "#fb923c",
        }
        return colors.get(self.activity, "#22d3ee")


StateListener = Callable[[UIState], None]
