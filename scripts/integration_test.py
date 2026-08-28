"""Integration smoke tests against Hermes Agent API."""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> int:
    from hermes.config.credentials import resolve_api_key
    from hermes.config.settings import AppSettings
    from hermes.server.client import HermesServerClient, HermesServerError
    from hermes.tools.system import EchoTool, GetSystemInfoTool

    settings = AppSettings.load()
    api_key = resolve_api_key(settings.api_key.get_secret_value())
    errors: list[str] = []
    passed: list[str] = []

    print("=== HERMES Windows Client - Integration Test ===\n")

    # 1. Config
    if settings.server.url:
        passed.append(f"Config URL: {settings.server.url}")
    else:
        errors.append("HERMES_SERVER_URL not set")

    if api_key:
        passed.append("API key: configured")
    else:
        errors.append("API key missing (run: hermes-client configure)")

    # 2. Local tools
    for tool_cls in (EchoTool, GetSystemInfoTool):
        tool = tool_cls()
        result = await tool.execute(message="test") if tool.name == "echo" else await tool.execute()
        if result.success:
            passed.append(f"Local tool '{tool.name}': OK")
        else:
            errors.append(f"Local tool '{tool.name}': {result.error}")

    if not api_key or not settings.server.url:
        _report(passed, errors)
        return 1

    # 3. API tests
    async with HermesServerClient(
        settings.server.url,
        api_key,
        model=settings.server.model,
        timeout=30,
        verify_ssl=settings.server.verify_ssl,
    ) as client:
        try:
            health = await client.health()
            passed.append(f"GET /health: {health.status}")
        except Exception as exc:
            errors.append(f"GET /health: {exc}")

        try:
            models = await client.get_models()
            ids = [m.id for m in models.data]
            if ids:
                passed.append(f"GET /v1/models: {', '.join(ids)}")
            else:
                errors.append("GET /v1/models: empty response")
        except Exception as exc:
            errors.append(f"GET /v1/models: {exc}")

        try:
            caps = await client.get_capabilities()
            feats = [k for k, v in caps.features.items() if v is True][:5]
            passed.append(f"GET /v1/capabilities: {caps.platform or 'ok'} ({', '.join(feats)}...)")
        except Exception as exc:
            errors.append(f"GET /v1/capabilities: {exc}")

        try:
            run = await client.create_run("Kisa test: sadece 'pong' yaz.", session_id=None)
            passed.append(f"POST /v1/runs: {run.id} ({run.status.value})")

            chunks: list[str] = []
            event_types: list[str] = []
            async for event in client.stream_events(run.id):
                event_types.append(event.type.value)
                if event.type.value in ("message", "message.delta", "assistant.delta"):
                    c = event.data.get("content") or event.data.get("delta") or event.data.get("text", "")
                    if c:
                        chunks.append(str(c))
                if event.type.value in ("run.completed", "run.failed", "run.cancelled", "done"):
                    break
                if len(event_types) > 200:
                    await client.stop_run(run.id)
                    break

            output = "".join(chunks)[:200]
            if output:
                passed.append(f"SSE events: {len(event_types)} events, output preview: {output[:80]}...")
            else:
                # poll run status
                final = await client.get_run(run.id)
                if final.output:
                    passed.append(f"Run completed: {final.output[:80]}...")
                elif event_types:
                    passed.append(f"SSE events received: {event_types[:8]}...")
                else:
                    errors.append("Run: no events or output received")
        except HermesServerError as exc:
            errors.append(f"POST /v1/runs: [{exc.status_code}] {exc}")
        except Exception as exc:
            errors.append(f"Run flow: {type(exc).__name__}: {exc}")

    _report(passed, errors)
    return 0 if not errors else 1


def _report(passed: list[str], errors: list[str]) -> None:
    print("PASSED:")
    for p in passed:
        print(f"  [OK] {p}")
    if errors:
        print("\nFAILED:")
        for e in errors:
            print(f"  [X] {e}")
    print(f"\nResult: {len(passed)} passed, {len(errors)} failed")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
