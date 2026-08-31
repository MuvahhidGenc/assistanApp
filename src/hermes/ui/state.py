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
    PROCESSING = "processing"
    SPEAKING = "speaking"


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
    status_text: str = "Hazir"
    activity: ActivityMode = ActivityMode.IDLE
    messages: list[ChatMessage] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "connection": self.connection.value,
                "voice_enabled": self.voice_enabled,
                "wake_word_enabled": self.wake_word_enabled,
                "microphone_available": self.microphone_available,
                "busy": self.busy,
                "status_text": self.status_text,
                "activity": self.activity.value,
                "message_count": len(self.messages),
            }

    def set_activity(self, mode: ActivityMode) -> None:
        with self._lock:
            self.activity = mode

    def set_connection(self, status: ConnectionStatus, *, detail: str = "") -> None:
        with self._lock:
            self.connection = status
            if detail:
                self.status_text = detail

    def set_busy(self, busy: bool, *, status: str | None = None) -> None:
        with self._lock:
            self.busy = busy
            if status is not None:
                self.status_text = status

    def set_voice(self, *, voice_enabled: bool | None = None, wake_word_enabled: bool | None = None) -> None:
        with self._lock:
            if voice_enabled is not None:
                self.voice_enabled = voice_enabled
            if wake_word_enabled is not None:
                self.wake_word_enabled = wake_word_enabled

    def set_microphone_available(self, available: bool) -> None:
        with self._lock:
            self.microphone_available = available

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


StateListener = Callable[[UIState], None]
