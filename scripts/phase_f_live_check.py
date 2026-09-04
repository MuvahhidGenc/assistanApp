"""Phase F live check: run the real IntentUnderstanding against the real server.

Read-only. Executes no tools, only the understanding layer. Temporary.
"""
from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, "src")

from hermes.config.credentials import resolve_api_key
from hermes.config.settings import AppSettings
from hermes.intent.router import IntentRouter
from hermes.intent.understanding import IntentUnderstanding
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.server.client import HermesServerClient
from hermes.skills.executor import SkillExecutor
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry

MESSAGES = [
    "Yazicim calismiyor, nedenini bul ve mumkunse duzelt.",
    "Bu makaleyi oku, onemli noktalarini cikar ve Word belgesi hazirla.",
    "Bilgisayarimda gereksiz dosyalari bul ve duzenle.",
    "Chrome'u ac, verdigim siteye git, hesabimdaki bilgileri incele ve ozetle.",
    "Sey yapar misin, hani su dosya vardi.",
    "Masaustunde notlar.txt olustur ve icine toplanti notlarini yaz.",
]


async def main() -> None:
    settings = AppSettings.load()
    registry = create_default_registry()
    understood = 0

    tool_executor = ToolExecutor(
        registry,
        PolicyEngine([], registry=registry),
        AuditLogger("probe-audit.log"),
        ApprovalManager(),
    )
    router = IntentRouter(registry, SkillExecutor(registry, tool_executor))

    async with HermesServerClient(
        base_url=settings.server.url,
        api_key=resolve_api_key(settings.api_key.get_secret_value()),
        model=settings.server.model.strip() or "hermes-agent",
        timeout=float(settings.server.timeout_seconds),
        verify_ssl=settings.server.verify_ssl,
    ) as client:
        understanding = IntentUnderstanding(client, registry)

        for message in MESSAGES:
            started = time.monotonic()
            result = await understanding.understand(message)
            elapsed = time.monotonic() - started

            print(f"\n=== {message}   ({elapsed:.1f}s)")
            if result.intent is None:
                print(f"  NO INTENT  unavailable={result.unavailable} error={result.error}")
                continue

            understood += bool(result.understood)
            intent = result.intent
            print(f"  understood={result.understood}  band={result.confidence}"
                  f"  reported={intent.reported_confidence}")
            print(f"  goal={intent.goal}")
            print(f"  capabilities={list(intent.required_capabilities)}")
            print(f"  multi_capability={intent.is_multi_capability}")
            print(f"  needs_input={intent.needs_user_input} "
                  f"question={intent.clarifying_question[:100]}")
            if result.validation.unknown_capabilities:
                print(f"  UNKNOWN={list(result.validation.unknown_capabilities)}")
            if result.validation.unavailable_capabilities:
                print(f"  UNAVAILABLE={list(result.validation.unavailable_capabilities)}")

            if not result.understood:
                continue
            plan = router.route(intent, confidence=result.confidence)
            print(f"  ROUTE={plan.kind} approval={plan.needs_approval}")
            if plan.kind == "skill":
                print(f"    skill={plan.skill_id} inputs={list(plan.skill_inputs)}")
            elif plan.kind == "capability_plan":
                for step in plan.steps:
                    print(f"    {step.metadata['capability']:22} -> {step.tool_name}"
                          f"  args={list(step.tool_arguments)}  risk={step.risk_level}")
            elif plan.kind == "question":
                print(f"    soru: {plan.question[:110]}")
            else:
                print(f"    eksik={list(plan.unavailable_capabilities)} "
                      f"alternatif={plan.alternatives}")
            if plan.unfillable_capabilities:
                print(f"    doldurulamayan={list(plan.unfillable_capabilities)}")

    print(f"\nUNDERSTOOD: {understood}/{len(MESSAGES)}")


if __name__ == "__main__":
    asyncio.run(main())
