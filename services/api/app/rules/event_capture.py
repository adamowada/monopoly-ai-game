from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.rules.events import GameEvent


_CAPTURED_EVENTS: ContextVar[list[GameEvent] | None] = ContextVar(
    "captured_rule_events",
    default=None,
)


@contextmanager
def capture_rule_events() -> Iterator[list[GameEvent]]:
    captured_events: list[GameEvent] = []
    token = _CAPTURED_EVENTS.set(captured_events)
    try:
        yield captured_events
    finally:
        _CAPTURED_EVENTS.reset(token)


def record_rule_event(event: GameEvent) -> None:
    captured_events = _CAPTURED_EVENTS.get()
    if captured_events is not None:
        captured_events.append(event)


__all__ = ["capture_rule_events", "record_rule_event"]
