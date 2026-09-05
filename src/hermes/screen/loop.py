"""Generic observe → resolve → scroll → observe search.

Bounded by ExecutionGuard via the caller's `guard_allows` callback. No
fixed retry count and no site-specific scroll recipes.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

ResolveFn = Callable[[], Awaitable[Any]]
ScrollFn = Callable[[], Awaitable[Any]]
ObserveFn = Callable[[], Awaitable[Any]]
GuardFn = Callable[[str, dict[str, Any]], bool]
RecordFn = Callable[[str, dict[str, Any], bool], None]


def needs_visible_search(result: Any) -> bool:
    output = getattr(result, "output", None)
    if not isinstance(output, dict):
        return False
    if output.get("needs_user"):
        return False
    if output.get("found"):
        return False
    if output.get("needs_scroll"):
        return True
    return not bool(getattr(result, "success", False))


def is_user_disambiguation(result: Any) -> bool:
    output = getattr(result, "output", None)
    return isinstance(output, dict) and bool(output.get("needs_user"))


async def search_visible_area(
    *,
    resolve: ResolveFn,
    scroll: ScrollFn,
    observe: ObserveFn,
    guard_allows: GuardFn,
    record_action: RecordFn | None = None,
    scroll_arguments: dict[str, Any] | None = None,
    initial: Any = None,
) -> Any:
    """After a miss, scroll and re-observe until resolve hits or the guard stops."""
    args = dict(scroll_arguments or {"direction": "down"})
    result = initial if initial is not None else await resolve()
    if not needs_visible_search(result):
        return result

    while needs_visible_search(result):
        if not guard_allows("scroll", args):
            return result
        scrolled = await scroll()
        if record_action is not None:
            record_action("scroll", args, bool(getattr(scrolled, "success", False)))
        observed = await observe()
        if record_action is not None:
            record_action(
                "read_screen_text",
                {},
                bool(getattr(observed, "success", False)),
            )
        result = await resolve()
    return result
