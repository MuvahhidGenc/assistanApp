"""EXE-parity proof: exact Tayfa match + scroll magnitude difference."""
from __future__ import annotations

from hermes.build_info import BUILD_LABEL, HERMES_BUILD_VERSION
from hermes.context.conversational_context import ConversationalContext
from hermes.context.entity_decision import Confidence
from hermes.screen.models import BoundingBox, ScreenEntity, ScreenState
from hermes.screen.resolve import resolve_screen_reference
from hermes.screen.scroll import parse_scroll_action


def _state() -> ScreenState:
    titles = [
        "Teknoloji ve Yapay Zeka",
        "Teknolojik Gelismeler",
        "Cicek ile Teknoloji",
        "Teknoloji Haberleri",
        "Teknolojik Tekillik",
        "Abone Ol",
        "Ana Sayfa",
        "Kesfet",
    ]
    entities = [
        ScreenEntity(
            id=f"se_{i}",
            type="video",
            role="video",
            text=t,
            bbox=BoundingBox(100, 40 + i * 40, 400, 30),
            confidence=0.85,
        )
        for i, t in enumerate(titles)
    ]
    entities.append(
        ScreenEntity(
            id="se_tayfa",
            type="video",
            role="video",
            text="Teknolojik Tayfa Sarkisi",
            bbox=BoundingBox(100, 500, 400, 30),
            confidence=0.92,
        )
    )
    return ScreenState(state_id="proof", entities=entities, text="\n".join(e.text for e in entities))


def main() -> None:
    print(f"HERMES_BUILD_VERSION={HERMES_BUILD_VERSION}")
    print(f"BUILD_LABEL={BUILD_LABEL}")
    decision = resolve_screen_reference(_state(), "teknolojik Tayfa sarkisini ac")
    assert decision.confidence is Confidence.HIGH, decision
    assert decision.chosen == ("se_tayfa",), decision
    assert not decision.clarification
    print("exact_match", decision.chosen[0], decision.reason, "clarification=NO")

    ctx = ConversationalContext()
    a = parse_scroll_action("biraz asagi", ctx)
    assert a is not None
    ctx.last_scroll_amount = a.amount
    b = parse_scroll_action("fazla asagi", ctx)
    assert b is not None
    assert b.amount > a.amount
    print("scroll", f"biraz={a.amount}", f"fazla={b.amount}", "delta", b.amount - a.amount)
    print("PROOF_OK")


if __name__ == "__main__":
    main()
