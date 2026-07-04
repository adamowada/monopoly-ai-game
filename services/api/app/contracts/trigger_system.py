from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.rules.state import GameState


@dataclass(frozen=True)
class TriggerMatch:
    matched: bool
    trigger_name: str
    reason: str
    context: dict[str, Any]


def rent_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    trigger = _trigger(schedule)
    context_type = _context_type(trigger_context)
    expected_type = str(trigger.get("type", ""))
    if expected_type not in {"rent_collected", "rent", "property_landed"}:
        return _no_match("rent_trigger", "obligation is not rent-triggered")
    if context_type not in {"rent_collected", "rent", "property_landed"}:
        return _no_match("rent_trigger", "context is not a rent trigger")
    if not _matches_optional_field(trigger, trigger_context, "property_id"):
        return _no_match("rent_trigger", "property_id does not match")
    return _match("rent_trigger", "rent trigger matched", trigger_context)


def turn_start_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    return _turn_boundary_trigger(
        schedule,
        trigger_context,
        trigger_type="turn_start",
        trigger_name="turn_start_trigger",
    )


def turn_end_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    return _turn_boundary_trigger(
        schedule,
        trigger_context,
        trigger_type="turn_end",
        trigger_name="turn_end_trigger",
    )


def property_transfer_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    trigger = _trigger(schedule)
    if str(trigger.get("type", "")) != "property_transfer":
        return _no_match("property_transfer_trigger", "obligation is not property-transfer-triggered")
    if _context_type(trigger_context) != "property_transfer":
        return _no_match("property_transfer_trigger", "context is not a property transfer trigger")
    for field in ("property_id", "from_player_id", "to_player_id"):
        if not _matches_optional_field(trigger, trigger_context, field):
            return _no_match("property_transfer_trigger", f"{field} does not match")
    return _match("property_transfer_trigger", "property transfer trigger matched", trigger_context)


def bankruptcy_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    trigger = _trigger(schedule)
    if str(trigger.get("type", "")) != "bankruptcy":
        return _no_match("bankruptcy_trigger", "obligation is not bankruptcy-triggered")
    if _context_type(trigger_context) != "bankruptcy":
        return _no_match("bankruptcy_trigger", "context is not a bankruptcy trigger")
    if not _matches_optional_field(trigger, trigger_context, "player_id"):
        return _no_match("bankruptcy_trigger", "player_id does not match")
    return _match("bankruptcy_trigger", "bankruptcy trigger matched", trigger_context)


def time_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    trigger = _trigger(schedule)
    if str(trigger.get("type", "")) != "time":
        return _no_match("time_trigger", "obligation is not time-triggered")
    due_at = _parse_datetime(trigger.get("due_at"))
    at = _parse_datetime(trigger_context.get("at") or trigger_context.get("now"))
    if due_at is None or at is None:
        return _no_match("time_trigger", "time trigger requires due_at and context time")
    if at < due_at:
        return _no_match("time_trigger", "time trigger is not due")
    return _match("time_trigger", "time trigger matched", trigger_context)


def round_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    trigger = _trigger(schedule)
    if str(trigger.get("type", "")) not in {"round", "turn"}:
        return _no_match("round_trigger", "obligation is not round-triggered")
    due_round = _positive_int(trigger.get("round") or trigger.get("due_turn") or trigger.get("turn"))
    context_round = _positive_int(trigger_context.get("round") or trigger_context.get("due_turn") or trigger_context.get("turn"))
    if due_round is None or context_round is None:
        return _no_match("round_trigger", "round trigger requires due round and context round")
    if context_round < due_round:
        return _no_match("round_trigger", "round trigger is not due")
    return _match("round_trigger", "round trigger matched", trigger_context)


def default_trigger(schedule: Mapping[str, Any], trigger_context: Mapping[str, Any]) -> TriggerMatch:
    trigger = _trigger(schedule)
    if str(trigger.get("type", "")) != "default":
        return _no_match("default_trigger", "obligation is not default-triggered")
    if _context_type(trigger_context) != "default":
        return _no_match("default_trigger", "context is not a default trigger")
    if not _matches_optional_field(trigger, trigger_context, "instrument_id"):
        return _no_match("default_trigger", "instrument_id does not match")
    return _match("default_trigger", "default trigger matched", trigger_context)


def trigger_system(
    schedule: Mapping[str, Any],
    *,
    state: GameState,
    trigger_context: Mapping[str, Any] | None = None,
) -> TriggerMatch:
    context = dict(trigger_context or {})
    trigger = _trigger(schedule)
    trigger_type = str(trigger.get("type", "immediate"))
    if trigger_type == "immediate":
        return _match("immediate_trigger", "immediate obligation is due", context)

    if not context and trigger_type in {"round", "turn"}:
        context = {"type": "round", "round": state.turn.turn_number, "turn": state.turn.turn_number}
    elif not context and trigger_type == "turn_start":
        context = {"type": "turn_start", "turn": state.turn.turn_number}
    elif not context and trigger_type == "turn_end":
        context = {"type": "turn_end", "turn": state.turn.turn_number}
    elif not context and trigger_type == "time":
        context = {"type": "time", "at": datetime.now(UTC).isoformat()}

    recognizers = (
        rent_trigger,
        turn_start_trigger,
        turn_end_trigger,
        property_transfer_trigger,
        bankruptcy_trigger,
        time_trigger,
        round_trigger,
        default_trigger,
    )
    for recognizer in recognizers:
        match = recognizer(schedule, context)
        if match.matched:
            return match
    return _no_match("trigger_system", "no trigger matched")


def _turn_boundary_trigger(
    schedule: Mapping[str, Any],
    trigger_context: Mapping[str, Any],
    *,
    trigger_type: str,
    trigger_name: str,
) -> TriggerMatch:
    trigger = _trigger(schedule)
    if str(trigger.get("type", "")) != trigger_type:
        return _no_match(trigger_name, f"obligation is not {trigger_type}-triggered")
    if _context_type(trigger_context) != trigger_type:
        return _no_match(trigger_name, f"context is not {trigger_type}")
    due_turn = _positive_int(trigger.get("turn") or trigger.get("due_turn"))
    context_turn = _positive_int(trigger_context.get("turn") or trigger_context.get("due_turn"))
    if due_turn is not None and context_turn is not None and context_turn < due_turn:
        return _no_match(trigger_name, f"{trigger_type} trigger is not due")
    if due_turn is not None and context_turn is None:
        return _no_match(trigger_name, f"{trigger_type} trigger requires context turn")
    return _match(trigger_name, f"{trigger_type} trigger matched", trigger_context)


def _trigger(schedule: Mapping[str, Any]) -> Mapping[str, Any]:
    trigger = schedule.get("trigger")
    return trigger if isinstance(trigger, Mapping) else {"type": "immediate"}


def _context_type(trigger_context: Mapping[str, Any]) -> str:
    raw = trigger_context.get("type")
    return raw.strip() if isinstance(raw, str) else ""


def _matches_optional_field(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    field: str,
) -> bool:
    expected_value = expected.get(field)
    return expected_value is None or str(actual.get(field)) == str(expected_value)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _match(trigger_name: str, reason: str, context: Mapping[str, Any]) -> TriggerMatch:
    return TriggerMatch(matched=True, trigger_name=trigger_name, reason=reason, context=dict(context))


def _no_match(trigger_name: str, reason: str) -> TriggerMatch:
    return TriggerMatch(matched=False, trigger_name=trigger_name, reason=reason, context={})


__all__ = [
    "TriggerMatch",
    "bankruptcy_trigger",
    "property_transfer_trigger",
    "rent_trigger",
    "round_trigger",
    "time_trigger",
    "trigger_system",
    "turn_end_trigger",
    "turn_start_trigger",
]
