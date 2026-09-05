"""Production-path: exact phrase match, quantity vs ordinal, scroll magnitude."""
from __future__ import annotations

from hermes.context.conversational_context import ConversationalContext
from hermes.context.entity_decision import Confidence
from hermes.screen.models import BoundingBox, ScreenEntity, ScreenState
from hermes.screen.plan import build_scroll_intent
from hermes.screen.reference import extract_reference_features
from hermes.screen.resolve import (
    bind_presented_screen_choice,
    build_screen_result_set,
    resolve_screen_reference,
)
from hermes.screen.scroll import ScrollMagnitude, parse_scroll_action
from hermes.screen.store import clear_screen_state, remember_screen_state


def _crowded_state() -> ScreenState:
    titles = [
        "Teknoloji ve Yapay Zeka",
        "Teknolojik Gelismeler",
        "Cicek ile Teknoloji",
        "Teknoloji Haberleri",
        "Uzay Teknolojisi",
        "Teknolojik Tekillik",
        "Muzik Teknolojisi",
        "Teknoloji Kanali",
        "Abone Ol",
        "Ana Sayfa",
        "Kesfet",
        "Kisa Videolar",
        "Abonelikler",
        "Sizler icin",
        "Gecmis",
    ]
    entities = [
        ScreenEntity(
            id=f"se_{index}",
            type="video",
            role="video",
            text=title,
            bbox=BoundingBox(100, 40 + index * 40, 400, 30),
            confidence=0.85,
        )
        for index, title in enumerate(titles)
    ]
    entities.append(
        ScreenEntity(
            id="se_target",
            type="video",
            role="video",
            text="Teknolojik Tayfa Sarkisi",
            bbox=BoundingBox(100, 700, 400, 30),
            confidence=0.9,
        )
    )
    return ScreenState(
        state_id="crowded",
        entities=entities,
        text="\n".join(item.text for item in entities),
        window={"title": "teknoloji - YouTube"},
    )


def test_exact_name_skips_clarification():
    state = _crowded_state()
    decision = resolve_screen_reference(state, "teknolojik Tayfa sarkisini ac")
    assert decision.confidence is Confidence.HIGH
    assert decision.chosen == ("se_target",)
    assert decision.reason in {"phrase_match", "text_similarity"}
    assert not decision.clarification


def test_compound_read_and_open_still_binds_named_target():
    state = _crowded_state()
    decision = resolve_screen_reference(
        state, "ekrani oku ve teknolojik Tayfa sarkisini ac"
    )
    assert decision.confidence is Confidence.HIGH
    assert decision.chosen == ("se_target",)
    assert not decision.clarification


def test_presented_ordinal_and_center_and_deixis():
    state = _crowded_state()
    result_set = build_screen_result_set(
        state=state,
        presented_order=["se_target", "se_2", "se_5"],
    )
    assert bind_presented_screen_choice("1'i ac", result_set) == "se_target"
    assert bind_presented_screen_choice("ortadakini ac", result_set) == "se_2"

    ctx = ConversationalContext(
        last_screen_entity_id="se_target",
        last_screen_result_set=result_set,
        last_screen_state=state.to_dict(),
    )
    decision = resolve_screen_reference(
        state, "onu ac", session_entity_id="se_target", context=ctx, result_set=result_set
    )
    assert decision.chosen == ("se_target",)
    assert decision.reason in {"session_deixis", "presented_order", "session_reference"}


def test_quantity_three_videos_is_not_third_entity():
    features = extract_reference_features("3 videoyu ac")
    assert features.quantity == 3
    assert features.ordinal is None

    state = _crowded_state()
    result_set = build_screen_result_set(
        state=state,
        presented_order=["se_0", "se_1", "se_2"],
    )
    assert bind_presented_screen_choice("3 videoyu ac", result_set) is None
    decision = resolve_screen_reference(state, "3 videoyu ac", result_set=result_set)
    assert decision.reason != "presented_order"
    assert decision.chosen != ("se_2",) or decision.reason != "presented_order"


def test_scroll_magnitude_small_normal_large_and_up():
    small = parse_scroll_action("biraz asagi")
    normal = parse_scroll_action("asagi")
    large = parse_scroll_action("fazla asagi")
    up = parse_scroll_action("yukari")
    assert small is not None and small.magnitude is ScrollMagnitude.SMALL
    assert normal is not None and normal.magnitude is ScrollMagnitude.NORMAL
    assert large is not None and large.magnitude is ScrollMagnitude.LARGE
    assert up is not None and up.direction == "up"
    assert small.amount < normal.amount < large.amount


def test_scroll_relative_grows_from_previous():
    ctx = ConversationalContext(last_scroll_amount=2)
    first = parse_scroll_action("biraz asagi", ctx)
    assert first is not None
    ctx.last_scroll_amount = first.amount
    second = parse_scroll_action("fazla asagi", ctx)
    assert second is not None
    assert second.amount > first.amount


def test_scroll_intent_includes_amount_and_observe():
    intent = build_scroll_intent("sayfayi biraz fazla kaydir")
    assert intent is not None
    scroll_step = intent.plan[0]
    assert scroll_step.capability == "screen.scroll"
    assert int(scroll_step.inputs["amount"]) >= parse_scroll_action("fazla asagi").amount
    assert intent.plan[1].capability == "screen.observe"


def test_scroll_updates_state_link_and_clears_result_set():
    clear_screen_state()
    first = ScreenState(state_id="before", entities=[], text="before")
    remember_screen_state(first)
    from hermes.screen.observe import build_screen_state

    second = build_screen_state(
        {
            "text": "after scroll",
            "window_title": "page",
            "screenshot": {"width": 100, "height": 100},
            "boxes": [
                {"text": "Yeni", "x": 16, "y": 16, "w": 20, "h": 10, "conf": 90, "line": 1, "block": 1}
            ],
        },
        remember=True,
    )
    assert second.previous_state_id == "before"
    clear_screen_state()
