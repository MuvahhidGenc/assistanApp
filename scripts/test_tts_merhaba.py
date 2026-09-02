"""Speak through Edge TTS directly — independent of Hermes orchestrator."""
from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    from hermes.voice.tts_smoke import main as smoke_main

    return await smoke_main(sys.argv)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
