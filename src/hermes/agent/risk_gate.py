"""Phase 9 — risk assessment and confirmation gates."""
from __future__ import annotations

import re
from dataclasses import dataclass

_BULK_DELETE = re.compile(
    r"\b(?:toplu|tum|tüm|hepsini|butun|bütün)\s+(?:sil|delete|kaldir|kaldır)\b",
    re.IGNORECASE,
)
_BULK_MOVE = re.compile(
    r"\b(?:toplu|tum|tüm|hepsini)\s+(?:tasi|taşı|move|kopyala|copy)\b",
    re.IGNORECASE,
)
_MASS_DELETE = re.compile(r"\b(?:sil|delete|kaldir|kaldır)\b.*\b(?:hepsi|tumu|tümü|all)\b", re.IGNORECASE)
_EXTERNAL_SEND = re.compile(
    r"\b(?:gonder|gönder|upload|yukle|yükle|email|mail)\b",
    re.IGNORECASE,
)
_SAFE_SINGLE = re.compile(
    r"\b(?:olustur|oluştur|yaz|ac|aç|oku|read|listele|goster|göster|rename|adini|adını)\b",
    re.IGNORECASE,
)


@dataclass
class RiskAssessment:
    requires_confirmation: bool = False
    reason: str = ""
    risk_level: str = "low"


def assess_message_risk(message: str) -> RiskAssessment:
    text = (message or "").strip()
    if not text:
        return RiskAssessment()

    if _BULK_DELETE.search(text) or _MASS_DELETE.search(text):
        return RiskAssessment(
            requires_confirmation=True,
            reason="Toplu silme islemi onay gerektiriyor.",
            risk_level="high",
        )

    if _BULK_MOVE.search(text):
        return RiskAssessment(
            requires_confirmation=True,
            reason="Buyuk olcekli tasima/kopyalama islemi onay gerektiriyor.",
            risk_level="medium",
        )

    if _EXTERNAL_SEND.search(text) and not _SAFE_SINGLE.search(text):
        return RiskAssessment(
            requires_confirmation=True,
            reason="Dis sisteme gonderim onay gerektiriyor.",
            risk_level="medium",
        )

    return RiskAssessment()


def assess_plan_risk(step_count: int, *, has_delete: bool = False, has_bulk_copy: bool = False) -> RiskAssessment:
    if has_delete and step_count > 1:
        return RiskAssessment(
            requires_confirmation=True,
            reason="Birden fazla silme adimi iceren plan onay gerektiriyor.",
            risk_level="high",
        )
    if has_bulk_copy and step_count >= 4:
        return RiskAssessment(
            requires_confirmation=True,
            reason="Cok adimli toplu kopyalama plani onay gerektiriyor.",
            risk_level="medium",
        )
    return RiskAssessment()
