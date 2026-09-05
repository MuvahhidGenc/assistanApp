"""Turning a user message into a structured goal.

This is the layer that stops Hermes needing a keyword for every task. It asks
the model what the user wants in terms of capabilities this machine actually
has, then hands the result to the existing capability/policy/guard chain.

It deliberately does not decide *whether* to run: it reports what it
understood and how sure it is, and the router acts on that.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any

import structlog

from hermes.context.entity_decision import Confidence
from hermes.intent.models import (
    AgentIntent,
    IntentValidation,
    decide_confidence,
    llm_named_a_tool,
    repair_intent_json,
    validate_intent,
)
from hermes.server.models import ChatRequest
from hermes.tools.capabilities import capability_risk_floor, known_capabilities
from hermes.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

# Measured against the live server: understanding a novel goal took 10-45s,
# and the first call of a session is the slowest.
DEFAULT_TIMEOUT = 75.0
JSON_RETRY_TIMEOUT = 30.0

_RETRY_PROMPT = """Onceki yanitin gecerli JSON degildi. SADECE semaya uyan tek bir
JSON nesnesi dondur. Baska metin, kod blogu veya aciklama yazma.

Kullanilabilir yetenekler:
{capabilities}

Sema alanlari: goal, subgoals, plan, required_capabilities, constraints,
ambiguity, needs_user_input, clarifying_question, risk_hint, confidence,
expected_outcome, mode, reply. Asla arac adi yazma.

Hatali yanit:
{broken}

Kullanici mesaji: {message}
"""

_PROMPT = """Sen bir Windows masaustu asistaninin niyet cozumleyicisisin.
Kullanicinin mesajini analiz et ve SADECE asagidaki JSON semasina uyan tek bir
JSON nesnesi dondur. Baska hicbir metin, aciklama veya kod bloğu yazma.

Bu bilgisayarda kullanilabilir yetenekler:
{capabilities}

Sema:
{{
  "goal": "kullanicinin nihai amaci, tek cumle",
  "subgoals": ["sirali alt hedefler"],
  "plan": [
    {{"capability": "yetenek adi", "inputs": {{"parametre": "deger"}}}}
  ],
  "required_capabilities": ["plan ile ayni yetenekler"],
  "constraints": ["varsa kisitlar"],
  "ambiguity": ["belirsiz kalan noktalar"],
  "needs_user_input": false,
  "clarifying_question": "eksik bilgi varsa sorulacak tek soru, yoksa bos string",
  "risk_hint": "read_only | low_risk | normal_modification | high_risk",
  "confidence": 0.0,
  "expected_outcome": "basarili olursa bilgisayarda ne degismis olacak",
  "mode": "task",
  "reply": ""
}}

plan icin parametre adlari (yalnizca gerekeni kullan):
- path: dosya veya klasor yolu
- content: dosyaya yazilacak metin
- url: web adresi
- app: uygulama adi
- query: aranacak metin veya desen
- destination: hedef klasor yolu
- name: servis, surec veya uygulama adi
- reference: ekrandaki bir nesneye dogal dil referansi

Ekrandaki bir nesneye (ilk/orta/sag/su video, buton, pencere) tiklanacaksa
browser.navigate aramasina indirgeme. Sirayla screen.observe, screen.resolve,
screen.click, gerekirse tekrar screen.observe kullan. Koordinatlari uydurma;
onlari screen.resolve uretir.

Kurallar:
- Asla arac (tool) adi yazma; yalnizca yukaridaki yetenek adlarini kullan.
- plan icindeki her adim gercekten yapilacak bir isi temsil etmeli;
  emin olmadigin bir adimi ekleme.
- Bir adim icin gereken degeri bilmiyorsan o adimi plana KOYMA ve
  bunu clarifying_question ile sor.
- filesystem.write icin yazilacak metni bilmiyorsan content uydurma;
  needs_user_input=true yap ve ne yazilacagini sor.
- Gereken yetenek listede yoksa onu uydurma, ambiguity icinde belirt.
- mode: "task" (bir is yapilacak), "conversation" (selam, sohbet, yetenek sorusu),
  "question" (ne istendigi belirsiz). Sohbet icin plan ve yetenek bos birak,
  kullaniciya soylenecek metni reply alanina yaz.
- reply: yalnizca conversation veya question ise doldur; task icin bos birak.
- confidence: kullanicinin ne istedigini ne kadar iyi anladigini yaz.
  Istek acikca anlasiliyorsa eksik bir ayrinti olsa bile yuksek ver;
  yalnizca ne istendigi gercekten belirsizse dusuk ver.
- Tum metin alanlarini Turkce yaz ve resmi bir dil kullan.
{context_block}
Kullanici mesaji: {message}
"""


@dataclass
class IntentResult:
    """What understanding produced, including why it failed when it did."""

    intent: AgentIntent | None = None
    confidence: Confidence = Confidence.LOW
    validation: IntentValidation = field(default_factory=IntentValidation)
    raw_response: str = ""
    error: str = ""
    unavailable: bool = False

    @property
    def understood(self) -> bool:
        return self.intent is not None and self.validation.ok


def _extract_chat_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if choices and isinstance(choices[0], dict):
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str):
            return content
    for key in ("output", "content", "text"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def build_context_block(context: Any = None, history: list[str] | None = None) -> str:
    """Give the model what the session already knows, so "onu" resolves.

    Only facts that are actually present are listed. Missing context is left
    blank so the model cannot treat an invented path or URL as known.
    """
    lines: list[str] = []
    if context is not None:
        focus = getattr(context, "active_focus", None)
        focus_type = getattr(focus, "type", "") if focus is not None else ""
        focus_id = getattr(focus, "identifier", None) if focus is not None else None
        container = None
        getter = getattr(context, "focus_container", None)
        if callable(getter):
            container = getter()
        facts = {
            "aktif odak": f"{focus_type}: {focus_id}" if focus_id else None,
            "konteyner": container,
            "acik dosya": focus_id
            if focus_type == "file"
            else None,
            "acik klasor": focus_id
            if focus_type == "folder"
            else container,
            "son olusturulan dosya": getattr(context, "last_created_file", None),
            "son acilan dosya": getattr(context, "last_opened_file", None),
            "son uygulama": getattr(context, "last_application", None),
            "son tarayici adresi": getattr(context, "last_browser_url", None),
            "son adres": getattr(context, "last_url", None),
            "suregelen gorev": getattr(context, "current_task", None),
            "onceki gorev sonucu": getattr(context, "last_task_result", None)
            or getattr(context, "last_action_summary", None)
            or getattr(context, "last_mission_summary", None),
        }
        known = [f"- {label}: {value}" for label, value in facts.items() if value]
        previous = getattr(context, "last_intent", None)
        if isinstance(previous, dict) and previous.get("goal"):
            known.append(f"- onceki niyet: {previous.get('goal')}")
            caps = previous.get("required_capabilities") or []
            if caps:
                known.append("- onceki yetenekler: " + ", ".join(str(c) for c in caps))
        refs = getattr(context, "resolved_references", None)
        resolved = refs() if callable(refs) else refs
        if isinstance(resolved, dict) and resolved:
            shown = ", ".join(f"{key}={value}" for key, value in resolved.items() if value)
            if shown:
                known.append(f"- cozulmus referanslar: {shown}")
        if known:
            lines.append("Mevcut oturum durumu:")
            lines.extend(known)

    if history:
        lines.append("Onceki konusma:")
        lines.extend(f"- {turn}" for turn in history[-6:])

    return "\n".join(lines) + "\n" if lines else ""


class IntentUnderstanding:
    """Asks the model for a structured goal expressed in local capabilities."""

    def __init__(
        self,
        client: Any,
        registry: ToolRegistry,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._client = client
        self._registry = registry
        self._timeout = timeout

    def _available(self) -> list[str]:
        return self._registry.capabilities()

    def build_prompt(
        self, message: str, *, context: Any = None, history: list[str] | None = None
    ) -> str:
        available = self._available()
        return _PROMPT.format(
            capabilities="\n".join(f"- {name}" for name in available),
            context_block=build_context_block(context, history),
            message=message,
        )

    async def understand(
        self,
        message: str,
        *,
        context: Any = None,
        history: list[str] | None = None,
    ) -> IntentResult:
        prompt = self.build_prompt(message, context=context, history=history)
        raw = await self._ask(prompt)
        if raw is None:
            return IntentResult(
                error="anlama katmani cagrilabilir degil", unavailable=True
            )
        if isinstance(raw, IntentResult):
            return raw

        data = repair_intent_json(raw)
        if data is None:
            retried = await self._retry_json(message, raw)
            if isinstance(retried, IntentResult):
                return retried
            data, raw = retried
        if data is None:
            logger.warning("intent_understanding_unparseable", preview=raw[:200])
            return IntentResult(
                raw_response=raw,
                error="model yanitindan yapilandirilmis niyet cikarilamadi",
            )
        if llm_named_a_tool(data):
            logger.warning("intent_understanding_named_a_tool")
            data = {key: value for key, value in data.items() if key not in ("tool_name", "tool")}
            plan = data.get("plan")
            if isinstance(plan, list):
                data["plan"] = [
                    item
                    for item in plan
                    if not (isinstance(item, dict) and (item.get("tool_name") or item.get("tool")))
                ]

        return self.evaluate(data, raw_response=raw)

    async def _ask(self, prompt: str, *, timeout: float | None = None) -> str | IntentResult | None:
        chat = self._client.chat(ChatRequest(message=prompt, stream=False))
        if not inspect.isawaitable(chat):
            return None
        try:
            response = await asyncio.wait_for(chat, timeout=timeout or self._timeout)
        except TimeoutError:
            logger.warning("intent_understanding_timeout", timeout=timeout or self._timeout)
            return IntentResult(error="anlama katmani zaman asimina ugradi", unavailable=True)
        except Exception as exc:  # noqa: BLE001 - any transport failure degrades
            logger.warning("intent_understanding_unavailable", error=str(exc))
            return IntentResult(error=str(exc), unavailable=True)
        return _extract_chat_content(response)

    async def _retry_json(
        self, message: str, broken: str
    ) -> tuple[dict[str, Any] | None, str] | IntentResult:
        prompt = _RETRY_PROMPT.format(
            capabilities="\n".join(f"- {name}" for name in self._available()),
            broken=(broken or "")[:1500],
            message=message,
        )
        raw = await self._ask(prompt, timeout=min(self._timeout, JSON_RETRY_TIMEOUT))
        if raw is None:
            return IntentResult(
                error="anlama katmani cagrilabilir degil", unavailable=True
            )
        if isinstance(raw, IntentResult):
            return raw
        return repair_intent_json(raw), raw

    def evaluate(self, data: dict[str, Any], *, raw_response: str = "") -> IntentResult:
        """Validate parsed intent data and place it on a decision band."""
        intent = AgentIntent.from_dict(data)
        validation = validate_intent(
            intent, set(known_capabilities()), set(self._available())
        )
        confidence = decide_confidence(intent, self.risk_floor(intent))
        return IntentResult(
            intent=intent,
            confidence=confidence,
            validation=validation,
            raw_response=raw_response,
        )

    def risk_floor(self, intent: AgentIntent) -> Any:
        """Highest unavoidable risk across the capabilities this intent needs."""
        floors = [
            floor
            for floor in (
                capability_risk_floor(self._registry, capability)
                for capability in intent.required_capabilities
            )
            if floor is not None
        ]
        if not floors:
            return None
        return max(floors, key=lambda level: level.severity)
