"""Quick API probe — reads credentials from .env, never prints secrets."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    base = os.environ["HERMES_SERVER_URL"].rstrip("/")
    key = os.environ["HERMES_API_KEY"]
    headers = {"Authorization": f"Bearer {key}"}

    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15) as client:
        endpoints = [
            ("GET", "/models"),
            ("GET", "/capabilities"),
            ("GET", "/sessions"),
            ("POST", "/chat/completions"),
        ]
        for method, path in endpoints:
            try:
                if method == "GET":
                    r = await client.get(path)
                else:
                    r = await client.post(
                        path,
                        json={
                            "model": os.environ.get("HERMES_MODEL", "hermes-agent"),
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 16,
                        },
                    )
                print(f"\n=== {method} {path} -> {r.status_code} ===")
                try:
                    print(json.dumps(r.json(), indent=2)[:2000])
                except Exception:
                    print(r.text[:500])
            except Exception as exc:
                print(f"\n=== {method} {path} -> ERROR: {exc} ===")


if __name__ == "__main__":
    asyncio.run(main())
