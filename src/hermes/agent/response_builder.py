from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class ResponseBuilder:
    """Accumulate streamed deltas and dedupe final run.completed output."""

    streamed: str = ""
    extras: list[str] = field(default_factory=list)

    def add_delta(self, content: str) -> None:
        if content:
            self.streamed += str(content)

    def add_extra(self, content: str) -> None:
        text = str(content).strip()
        if not text:
            return
        if text not in self.extras and text != self.streamed.strip():
            self.extras.append(text)

    def add_final_output(self, output: str) -> None:
        text = str(output).strip()
        if not text:
            return
        streamed = self.streamed.strip()
        if streamed and (text == streamed or text.startswith(streamed) or streamed.startswith(text)):
            return
        self.add_extra(text)

    def build(self) -> str:
        sections: list[str] = []
        if self.streamed.strip():
            sections.append(self.streamed.strip())
        for extra in self.extras:
            if extra not in sections:
                sections.append(extra)
        return "\n\n".join(sections)
