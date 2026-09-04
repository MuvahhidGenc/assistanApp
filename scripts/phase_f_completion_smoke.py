"""Phase F completion smoke: real server, production understanding + local routing.

Does not execute mutating tools. Proves USER -> Intent -> capability -> local tool
for the architecture cases, including context follow-ups.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hermes.config.credentials import resolve_api_key
from hermes.config.settings import AppSettings
from hermes.context.conversational_context import ConversationalContext
from hermes.intent.router import IntentRouter, RouteKind
from hermes.intent.understanding import IntentUnderstanding
from hermes.security.approval_manager import ApprovalManager
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.server.client import HermesServerClient
from hermes.skills.executor import SkillExecutor
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry

CASES = [
    ("A", "Notlar klasorunde test.txt olustur."),
    ("B", "Chrome'u ac, Google'a gir ve OpenAI sitesine git."),
    ("C", "Son olusturdugun dosyayi ac."),
    ("D", "Bu web sayfasini oku, ozetle ve Word dosyasi hazirla."),
    ("E", "Yazicimi kontrol et, sorun varsa nedenini bul."),
    ("F", "Bir dosyayi yeniden adlandir."),
    ("G", "Sunu ac."),
]


def _report(label: str, message: str, result, plan) -> None:
    intent = result.intent
    print(f"\n=== {label} ===")
    print(f"USER INPUT: {message}")
    if result.unavailable:
        print(f"INTENT: unavailable error={result.error}")
        return
    if intent is None:
        print(f"INTENT: unparseable error={result.error}")
        return
    print(f"INTENT: goal={intent.goal!r} mode={intent.mode} band={result.confidence}")
    print(f"REQUIRED CAPABILITIES: {list(intent.required_capabilities)}")
    print(f"SELECTED SKILL/MISSION: {plan.kind} skill={plan.skill_id or '-'}")
    tools = [step.tool_name for step in plan.steps if step.tool_name]
    print(f"SELECTED LOCAL TOOLS: {tools}")
    print("TOOL SOURCE: capability_resolver" if tools else "TOOL SOURCE: n/a")
    print(f"POLICY/RISK: {[step.risk_level for step in plan.steps]}")
    print(f"APPROVAL: {plan.needs_approval}")
    print("EXECUTION: skipped (routing smoke)")
    print("VERIFICATION: n/a")
    print("RECOVERY: n/a")
    if plan.kind is RouteKind.QUESTION:
        print(f"FINAL RESULT: QUESTION {plan.question[:160]}")
    elif plan.kind is RouteKind.UNSUPPORTED:
        print(f"FINAL RESULT: UNSUPPORTED {plan.unavailable_capabilities}")
    elif plan.kind is RouteKind.CONVERSATION:
        print(f"FINAL RESULT: CONVERSATION {plan.question[:160]}")
    else:
        print(f"FINAL RESULT: executable={plan.is_executable}")
    if plan.unfillable_capabilities:
        print(f"UNFILLABLE: {list(plan.unfillable_capabilities)}")
    if result.validation.unknown_capabilities:
        print(f"UNKNOWN CAPS: {list(result.validation.unknown_capabilities)}")


async def main() -> int:
    settings = AppSettings.load()
    registry = create_default_registry()
    tool_executor = ToolExecutor(
        registry,
        PolicyEngine([], registry=registry),
        AuditLogger("probe-audit.log"),
        ApprovalManager(),
    )
    router = IntentRouter(registry, SkillExecutor(registry, tool_executor))
    context = ConversationalContext()
    failures: list[str] = []

    async with HermesServerClient(
        base_url=settings.server.url,
        api_key=resolve_api_key(settings.api_key.get_secret_value()),
        model=settings.server.model.strip() or "hermes-agent",
        timeout=float(settings.server.timeout_seconds),
        verify_ssl=settings.server.verify_ssl,
    ) as client:
        understanding = IntentUnderstanding(client, registry)

        for label, message in CASES:
            started = time.monotonic()
            result = await understanding.understand(message, context=context)
            elapsed = time.monotonic() - started
            print(f"\n[{label}] {elapsed:.1f}s unavailable={result.unavailable}")
            if result.intent is None:
                _report(label, message, result, None)
                failures.append(label)
                continue
            plan = router.route(result.intent, confidence=result.confidence, context=context)
            _report(label, message, result, plan)
            if result.unavailable:
                failures.append(label)
            elif label == "G" and plan.kind is not RouteKind.QUESTION:
                failures.append("G expected QUESTION")
            elif label == "C" and not context.last_created_file and plan.kind is not RouteKind.QUESTION:
                failures.append("C expected QUESTION without last file")

        print("\n=== H context chain ===")
        context.last_created_file = str(Path.home() / "Desktop" / "Notlar" / "test.txt")
        chain = [
            "Son olusturdugun dosyayi ac.",
            "Basligini degistir.",
            "Simdi PDF olarak kaydet.",
        ]
        previous = None
        for index, message in enumerate(chain, start=1):
            result = await understanding.understand(message, context=context)
            if result.intent is None:
                print(f"H{index} FAIL unavailable={result.unavailable} error={result.error}")
                failures.append(f"H{index}")
                continue
            plan = router.route(result.intent, confidence=result.confidence, context=context)
            _report(f"H{index}", message, result, plan)
            context.record_intent(result.intent.to_dict(), route_kind=str(plan.kind), result=str(plan.kind))
            if previous and not (result.intent.references or plan.steps or plan.kind is RouteKind.QUESTION):
                failures.append(f"H{index} lost context")
            previous = result.intent

    print("\n" + json.dumps({"failures": failures}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
