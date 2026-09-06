"""Active-target look asks must not start screen.observe missions."""
from __future__ import annotations

from hermes.agent.conversation_flow import handle_meta_conversation
from hermes.context.conversational_context import ConversationalContext


def test_o_nasil_uses_conversation_state() -> None:
    ctx = ConversationalContext()
    ctx.last_application = "notepad"
    ctx.record_verified_action("Not Defteri açıldı.")
    turn = handle_meta_conversation("O nasıl diye bakar mısın?", ctx)
    assert turn.handled
    assert turn.source == "active_target_report"
    assert "notepad" in turn.response.casefold() or "not defteri" in turn.response.casefold()


def test_screen_spatial_not_swallowed() -> None:
    ctx = ConversationalContext()
    ctx.last_application = "notepad"
    turn = handle_meta_conversation("Ekrandaki ilk videoyu aç", ctx)
    assert not turn.handled
