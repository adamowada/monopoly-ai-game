from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, NoReturn, Protocol, cast

from app.rules.atomic import is_atomic_section_active
from app.rules.debt import (
    DEBT_FORBIDDEN_ACTION_TYPES,
    DEBT_LIQUIDATION_ACTION_TYPES,
    clear_active_debt,
    debt_issue_for_action,
    is_debt_active,
    outstanding_debt_amount,
    settle_debt_with_cash,
)
from app.rules.event_capture import capture_rule_events
from app.rules.events import DiceRolledPayload, GameEvent
from app.rules.mechanics import (
    JAIL_FINE,
    IllegalRuleActionError,
    apply_dice_roll,
    buy_house,
    buy_property,
    close_auction,
    declare_bankruptcy,
    mortgage_property,
    pass_auction,
    pay_jail_fine,
    place_auction_bid,
    sell_house,
    start_auction,
    unmortgage_property,
    use_get_out_of_jail_card,
)
from app.rules.rng import generate_dice_roll_event
from app.rules.static_data import (
    BoardSpace,
    CardData,
    PropertyData,
    load_classic_monopoly_data,
)
from app.rules.state import GameState, PlayerState, PropertyOwnershipState
from app.rules.timing import ActionTimingIssue, is_action_allowed_now, timing_issue_for_action


SUPPORTED_ACTION_TYPES: Final[frozenset[str]] = frozenset(
    {
        "ROLL_DICE",
        "BUY_PROPERTY",
        "START_AUCTION",
        "BID_AUCTION",
        "PASS_AUCTION",
        "PAY_JAIL_FINE",
        "USE_GET_OUT_OF_JAIL_CARD",
        "BUY_HOUSE",
        "SELL_HOUSE",
        "MORTGAGE_PROPERTY",
        "UNMORTGAGE_PROPERTY",
        "DECLARE_BANKRUPTCY",
        "SETTLE_DEBT",
    }
)


class _ActionAdder(Protocol):
    def __call__(
        self,
        action_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        schema: Mapping[str, object] | None = None,
        description: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ActionValidationIssue:
    code: str
    message: str
    field: str | None = None

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
        }


class ActionValidationError(ValueError):
    def __init__(self, errors: tuple[ActionValidationIssue, ...]) -> None:
        self.errors = errors
        message = "; ".join(error.message for error in errors)
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GameAction:
    actor_id: str
    type: str
    payload: Mapping[str, object] = field(default_factory=dict)
    expected_state_hash: str = ""
    expected_event_sequence: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.payload, Mapping):
            object.__setattr__(self, "payload", _freeze_mapping(self.payload))

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "type": self.type,
            "payload": _thaw_value(self.payload, mode=mode),
            "expected_state_hash": self.expected_state_hash,
            "expected_event_sequence": self.expected_event_sequence,
        }


@dataclass(frozen=True, slots=True)
class LegalAction:
    actor_id: str
    type: str
    payload: Mapping[str, object]
    expected_state_hash: str
    expected_event_sequence: int
    description: str | None = None
    schema: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))
        object.__setattr__(self, "schema", _freeze_mapping(self.schema))

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "type": self.type,
            "payload": _thaw_value(self.payload, mode=mode),
            "expected_state_hash": self.expected_state_hash,
            "expected_event_sequence": self.expected_event_sequence,
            "description": self.description,
            "schema": _thaw_value(self.schema, mode=mode),
        }


@dataclass(frozen=True, slots=True)
class ValidatedAction:
    action: GameAction

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {"action": self.action.model_dump(mode=mode)}


@dataclass(frozen=True, slots=True)
class ActionExecutionResult:
    action: GameAction
    events: tuple[GameEvent, ...]
    state: GameState

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {
            "action": self.action.model_dump(mode=mode),
            "events": [
                {
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "type": event.type,
                    "payload": event.payload.model_dump(mode=mode),
                }
                for event in self.events
            ],
            "state": self.state.model_dump(mode=mode),
        }


def list_legal_actions(state: GameState, actor_id: str) -> tuple[LegalAction, ...]:
    player = _player_by_id(state, actor_id)
    if player is None or player.is_bankrupt:
        return ()
    if is_atomic_section_active(state):
        return ()

    actions: list[LegalAction] = []

    def add(
        action_type: str,
        payload: Mapping[str, object] | None = None,
        *,
        schema: Mapping[str, object] | None = None,
        description: str | None = None,
    ) -> None:
        if not is_action_allowed_now(state, action_type, actor_id=actor_id):
            return
        actions.append(
            LegalAction(
                actor_id=actor_id,
                type=action_type,
                payload={} if payload is None else payload,
                expected_state_hash=state.state_hash(),
                expected_event_sequence=state.event_sequence,
                description=description,
                schema=_empty_payload_schema() if schema is None else schema,
            )
        )

    if is_debt_active(state):
        active_payment = state.active_payment
        if active_payment is None or actor_id != active_payment.debtor_id:
            return ()

        outstanding = outstanding_debt_amount(state)
        settle_amount = min(player.cash, outstanding)
        if settle_amount > 0:
            add(
                "SETTLE_DEBT",
                {"amount": settle_amount},
                schema=_object_schema(
                    {
                        "amount": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": settle_amount,
                        }
                    },
                    required=("amount",),
                ),
                description=f"Pay {settle_amount} toward {active_payment.reason}.",
            )
        _add_management_actions(
            state,
            player,
            add,
            allowed_action_types=DEBT_LIQUIDATION_ACTION_TYPES,
        )
        add(
            "DECLARE_BANKRUPTCY",
            {"creditor_id": active_payment.creditor_id},
            schema=_bankruptcy_schema(state, actor_id),
            description="Declare bankruptcy and liquidate assets.",
        )
        return tuple(actions)

    if state.active_auction is not None:
        auction = state.active_auction
        if actor_id not in auction.passed_player_ids and actor_id != auction.high_bidder_id:
            minimum_bid = _minimum_auction_bid(state)
            if player.cash >= minimum_bid:
                add(
                    "BID_AUCTION",
                    {"property_id": auction.property_id, "amount": minimum_bid},
                    schema=_object_schema(
                        {
                            "property_id": _const_string_schema(auction.property_id),
                            "amount": {"type": "integer", "minimum": minimum_bid},
                        },
                        required=("amount",),
                    ),
                    description=f"Bid at least {minimum_bid} for {_property_data(auction.property_id).name}.",
                )
            add(
                "PASS_AUCTION",
                {"property_id": auction.property_id},
                schema=_object_schema(
                    {"property_id": _const_string_schema(auction.property_id)},
                ),
                description=f"Pass on the auction for {_property_data(auction.property_id).name}.",
            )

        add(
            "DECLARE_BANKRUPTCY",
            {"creditor_id": None},
            schema=_bankruptcy_schema(state, actor_id),
            description="Declare bankruptcy and liquidate assets.",
        )
        return tuple(actions)

    if actor_id == state.turn.current_player_id:
        add("ROLL_DICE", description="Roll deterministic dice for the current turn.")

        if player.in_jail:
            if player.cash >= JAIL_FINE:
                add(
                    "PAY_JAIL_FINE",
                    {"amount": JAIL_FINE},
                    schema=_object_schema({"amount": {"type": "integer", "const": JAIL_FINE}}),
                    description=f"Pay {JAIL_FINE} to leave jail.",
                )
            for card_id in player.get_out_of_jail_card_ids:
                add(
                    "USE_GET_OUT_OF_JAIL_CARD",
                    {"card_id": card_id},
                    schema=_object_schema(
                        {"card_id": _const_string_schema(card_id)},
                        required=("card_id",),
                    ),
                    description=f"Use {_card_data(card_id).title}.",
                )

        space = _space_for_position(player.position)
        if space.property_id is not None:
            ownership = _property_ownership(state, space.property_id)
            property_data = _property_data(space.property_id)
            if ownership.owner_id is None:
                if player.cash >= property_data.price:
                    add(
                        "BUY_PROPERTY",
                        {"property_id": property_data.id, "price": property_data.price},
                        schema=_object_schema(
                            {
                                "property_id": _const_string_schema(property_data.id),
                                "price": {"type": "integer", "const": property_data.price},
                            },
                            required=("property_id",),
                        ),
                        description=f"Buy {property_data.name} for {property_data.price}.",
                    )
                add(
                    "START_AUCTION",
                    {"property_id": property_data.id},
                    schema=_object_schema(
                        {"property_id": _const_string_schema(property_data.id)},
                        required=("property_id",),
                    ),
                    description=f"Start an auction for {property_data.name}.",
                )

    _add_management_actions(state, player, add)

    add(
        "DECLARE_BANKRUPTCY",
        {"creditor_id": None},
        schema=_bankruptcy_schema(state, actor_id),
        description="Declare bankruptcy and liquidate assets.",
    )
    return tuple(actions)


def validate_action(state: GameState, action: GameAction) -> ValidatedAction:
    stale_issues = _stale_issues(state, action)
    if stale_issues:
        raise ActionValidationError(stale_issues)

    if not isinstance(action.type, str):
        _raise_issue("unknown_action", f"unknown action type {action.type}", "type")

    payload = _payload_mapping(action)

    actor = _player_by_id(state, action.actor_id)
    if actor is None:
        _raise_issue("illegal_action", f"unknown actor {action.actor_id}", "actor_id")
    if actor.is_bankrupt:
        _raise_issue("illegal_action", f"{action.actor_id} is bankrupt", "actor_id")

    early_debt_issue = debt_issue_for_action(state, action.type, action.actor_id)
    if early_debt_issue is not None and (
        action.type in SUPPORTED_ACTION_TYPES or action.type in DEBT_FORBIDDEN_ACTION_TYPES
    ):
        raise ActionValidationError(
            (
                ActionValidationIssue(
                    code=early_debt_issue.code,
                    message=early_debt_issue.message,
                    field=early_debt_issue.field,
                ),
            )
        )

    if action.type not in SUPPORTED_ACTION_TYPES:
        _raise_issue("unknown_action", f"unknown action type {action.type}", "type")

    _validate_payload_shape(action.type, payload)

    timing_issue = timing_issue_for_action(state, action.type, actor_id=action.actor_id)
    if timing_issue is not None:
        _raise_timing_issue(timing_issue)

    if action.type == "ROLL_DICE":
        _validate_roll_timing(state, action.actor_id)
    elif action.type == "BUY_PROPERTY":
        _validate_purchase_action(state, actor, _required_str(payload, "property_id"), require_cash=True)
    elif action.type == "START_AUCTION":
        _validate_purchase_action(state, actor, _required_str(payload, "property_id"), require_cash=False)
    elif action.type == "BID_AUCTION":
        _validate_auction_bid_action(state, actor, payload)
    elif action.type == "PASS_AUCTION":
        _validate_auction_pass_action(state, actor, payload)
    elif action.type == "PAY_JAIL_FINE":
        _validate_jail_fine_action(state, actor, payload)
    elif action.type == "USE_GET_OUT_OF_JAIL_CARD":
        _validate_jail_card_action(state, actor, _required_str(payload, "card_id"))
    elif action.type == "BUY_HOUSE":
        _validate_management_action(
            state,
            action.actor_id,
            _required_str(payload, "property_id"),
            "BUY_HOUSE",
            buy_house,
        )
    elif action.type == "SELL_HOUSE":
        _validate_management_action(
            state,
            action.actor_id,
            _required_str(payload, "property_id"),
            "SELL_HOUSE",
            sell_house,
        )
    elif action.type == "MORTGAGE_PROPERTY":
        _validate_management_action(
            state,
            action.actor_id,
            _required_str(payload, "property_id"),
            "MORTGAGE_PROPERTY",
            mortgage_property,
        )
    elif action.type == "UNMORTGAGE_PROPERTY":
        _validate_management_action(
            state,
            action.actor_id,
            _required_str(payload, "property_id"),
            "UNMORTGAGE_PROPERTY",
            unmortgage_property,
        )
    elif action.type == "DECLARE_BANKRUPTCY":
        _validate_bankruptcy_action(state, actor, payload)
    elif action.type == "SETTLE_DEBT":
        _validate_settle_debt_action(state, actor, payload)

    return ValidatedAction(action=action)


def apply_action(state: GameState, action: GameAction, event_id_prefix: str) -> GameState:
    validate_action(state, action)
    payload = _payload_mapping(action)

    if action.type == "ROLL_DICE":
        dice_event = generate_dice_roll_event(
            state,
            f"{event_id_prefix}-{state.event_sequence + 1}",
            action.actor_id,
        )
        dice_payload = cast(DiceRolledPayload, dice_event.payload)
        return apply_dice_roll(
            state,
            action.actor_id,
            dice_payload.die_1,
            dice_payload.die_2,
            event_id_prefix,
        )

    if action.type == "BUY_PROPERTY":
        return buy_property(state, action.actor_id, _required_str(payload, "property_id"), event_id_prefix)

    if action.type == "START_AUCTION":
        return start_auction(state, _required_str(payload, "property_id"), event_id_prefix)

    if action.type == "BID_AUCTION":
        next_state = place_auction_bid(
            state,
            action.actor_id,
            _required_int(payload, "amount"),
            event_id_prefix,
        )
        return _close_auction_if_resolved(next_state, event_id_prefix)

    if action.type == "PASS_AUCTION":
        next_state = pass_auction(state, action.actor_id, event_id_prefix)
        return _close_auction_if_resolved(next_state, event_id_prefix)

    if action.type == "PAY_JAIL_FINE":
        return pay_jail_fine(state, action.actor_id, event_id_prefix)

    if action.type == "USE_GET_OUT_OF_JAIL_CARD":
        return use_get_out_of_jail_card(
            state,
            action.actor_id,
            _required_str(payload, "card_id"),
            event_id_prefix,
        )

    if action.type == "BUY_HOUSE":
        return buy_house(state, action.actor_id, _required_str(payload, "property_id"), event_id_prefix)

    if action.type == "SELL_HOUSE":
        return sell_house(state, action.actor_id, _required_str(payload, "property_id"), event_id_prefix)

    if action.type == "MORTGAGE_PROPERTY":
        return mortgage_property(
            state,
            action.actor_id,
            _required_str(payload, "property_id"),
            event_id_prefix,
        )

    if action.type == "UNMORTGAGE_PROPERTY":
        return unmortgage_property(
            state,
            action.actor_id,
            _required_str(payload, "property_id"),
            event_id_prefix,
        )

    if action.type == "DECLARE_BANKRUPTCY":
        creditor_id = _bankruptcy_creditor_for_payload(state, action.actor_id, payload)
        next_state = declare_bankruptcy(state, action.actor_id, creditor_id, event_id_prefix)
        if state.active_payment is not None and state.active_payment.debtor_id == action.actor_id:
            return clear_active_debt(next_state, event_id_prefix)
        return next_state

    if action.type == "SETTLE_DEBT":
        return settle_debt_with_cash(
            state,
            action.actor_id,
            _required_int(payload, "amount"),
            event_id_prefix,
        )

    _raise_issue("unknown_action", f"unknown action type {action.type}", "type")


def execute_action(state: GameState, action: GameAction, event_id_prefix: str) -> ActionExecutionResult:
    with capture_rule_events() as captured_events:
        next_state = apply_action(state, action, event_id_prefix)

    if not captured_events:
        _raise_issue("illegal_action", f"{action.type} produced no rules events", "type")

    return ActionExecutionResult(
        action=action,
        events=tuple(captured_events),
        state=next_state,
    )


def _add_management_actions(
    state: GameState,
    player: PlayerState,
    add: _ActionAdder,
    *,
    allowed_action_types: frozenset[str] | None = None,
) -> None:
    for ownership in state.property_ownership:
        if ownership.owner_id != player.id:
            continue

        property_data = _property_data(ownership.property_id)
        if (
            _is_management_action_enabled("BUY_HOUSE", allowed_action_types)
            and _mechanic_accepts(state, player.id, ownership.property_id, "BUY_HOUSE", buy_house)
        ):
            add(
                "BUY_HOUSE",
                {
                    "property_id": property_data.id,
                    "cost": _house_cost(property_data),
                },
                schema=_object_schema(
                    {
                        "property_id": _const_string_schema(property_data.id),
                        "cost": {"type": "integer", "const": _house_cost(property_data)},
                    },
                    required=("property_id",),
                ),
                description=f"Buy an improvement on {property_data.name}.",
            )
        if (
            _is_management_action_enabled("SELL_HOUSE", allowed_action_types)
            and _mechanic_accepts(state, player.id, ownership.property_id, "SELL_HOUSE", sell_house)
        ):
            add(
                "SELL_HOUSE",
                {
                    "property_id": property_data.id,
                    "proceeds": _house_cost(property_data) // 2,
                },
                schema=_object_schema(
                    {
                        "property_id": _const_string_schema(property_data.id),
                        "proceeds": {"type": "integer", "const": _house_cost(property_data) // 2},
                    },
                    required=("property_id",),
                ),
                description=f"Sell an improvement from {property_data.name}.",
            )
        if (
            _is_management_action_enabled("MORTGAGE_PROPERTY", allowed_action_types)
            and _mechanic_accepts(
                state,
                player.id,
                ownership.property_id,
                "MORTGAGE_PROPERTY",
                mortgage_property,
            )
        ):
            add(
                "MORTGAGE_PROPERTY",
                {
                    "property_id": property_data.id,
                    "proceeds": property_data.mortgage_value,
                },
                schema=_object_schema(
                    {
                        "property_id": _const_string_schema(property_data.id),
                        "proceeds": {"type": "integer", "const": property_data.mortgage_value},
                    },
                    required=("property_id",),
                ),
                description=f"Mortgage {property_data.name}.",
            )
        if (
            _is_management_action_enabled("UNMORTGAGE_PROPERTY", allowed_action_types)
            and _mechanic_accepts(
                state,
                player.id,
                ownership.property_id,
                "UNMORTGAGE_PROPERTY",
                unmortgage_property,
            )
        ):
            cost = property_data.mortgage_value + _mortgage_interest(property_data.mortgage_value)
            add(
                "UNMORTGAGE_PROPERTY",
                {
                    "property_id": property_data.id,
                    "cost": cost,
                },
                schema=_object_schema(
                    {
                        "property_id": _const_string_schema(property_data.id),
                        "cost": {"type": "integer", "const": cost},
                    },
                    required=("property_id",),
                ),
                description=f"Unmortgage {property_data.name}.",
            )


def _validate_payload_shape(action_type: str, payload: Mapping[str, object]) -> None:
    if action_type == "ROLL_DICE":
        _validate_allowed_fields(payload, ())
        return

    if action_type in {"BUY_PROPERTY", "START_AUCTION"}:
        _validate_allowed_fields(payload, ("property_id", "price"))
        _required_str(payload, "property_id")
        if "price" in payload:
            _required_int(payload, "price")
        return

    if action_type == "BID_AUCTION":
        _validate_allowed_fields(payload, ("property_id", "amount"))
        _required_int(payload, "amount")
        if "property_id" in payload:
            _required_str(payload, "property_id")
        return

    if action_type == "PASS_AUCTION":
        _validate_allowed_fields(payload, ("property_id",))
        if "property_id" in payload:
            _required_str(payload, "property_id")
        return

    if action_type == "PAY_JAIL_FINE":
        _validate_allowed_fields(payload, ("amount",))
        if "amount" in payload:
            _required_int(payload, "amount")
        return

    if action_type == "USE_GET_OUT_OF_JAIL_CARD":
        _validate_allowed_fields(payload, ("card_id",))
        _required_str(payload, "card_id")
        return

    if action_type == "BUY_HOUSE":
        _validate_allowed_fields(payload, ("property_id", "cost"))
        _required_str(payload, "property_id")
        if "cost" in payload:
            _required_int(payload, "cost")
        return

    if action_type == "SELL_HOUSE":
        _validate_allowed_fields(payload, ("property_id", "proceeds"))
        _required_str(payload, "property_id")
        if "proceeds" in payload:
            _required_int(payload, "proceeds")
        return

    if action_type == "MORTGAGE_PROPERTY":
        _validate_allowed_fields(payload, ("property_id", "proceeds"))
        _required_str(payload, "property_id")
        if "proceeds" in payload:
            _required_int(payload, "proceeds")
        return

    if action_type == "UNMORTGAGE_PROPERTY":
        _validate_allowed_fields(payload, ("property_id", "cost"))
        _required_str(payload, "property_id")
        if "cost" in payload:
            _required_int(payload, "cost")
        return

    if action_type == "DECLARE_BANKRUPTCY":
        _validate_allowed_fields(payload, ("creditor_id",))
        if "creditor_id" in payload:
            _optional_str_or_none(payload, "creditor_id")
        return

    if action_type == "SETTLE_DEBT":
        _validate_allowed_fields(payload, ("amount",))
        _required_int(payload, "amount")


def _validate_roll_timing(state: GameState, actor_id: str) -> None:
    if state.active_auction is not None:
        _raise_issue("mistimed_action", "players cannot roll dice during an active auction", "type")
    if actor_id != state.turn.current_player_id:
        _raise_issue("mistimed_action", f"{actor_id} is not the current turn player", "actor_id")


def _validate_purchase_action(
    state: GameState,
    actor: PlayerState,
    property_id: str,
    *,
    require_cash: bool,
) -> None:
    if state.active_auction is not None:
        _raise_issue("mistimed_action", "purchase decisions are not legal during an active auction", "type")
    if actor.id != state.turn.current_player_id:
        _raise_issue("mistimed_action", f"{actor.id} is not the current turn player", "actor_id")
    space = _space_for_position(actor.position)
    if space.property_id != property_id:
        _raise_issue("illegal_action", f"{actor.id} is not on {property_id}", "payload.property_id")
    ownership = _property_ownership(state, property_id)
    if ownership.owner_id is not None:
        _raise_issue("illegal_action", f"{property_id} is already owned", "payload.property_id")
    property_data = _property_data(property_id)
    if require_cash and actor.cash < property_data.price:
        _raise_issue("illegal_action", "insufficient cash to buy property", "payload.property_id")


def _validate_auction_bid_action(state: GameState, actor: PlayerState, payload: Mapping[str, object]) -> None:
    auction = state.active_auction
    if auction is None:
        _raise_issue("mistimed_action", "there is no active auction", "type")

    property_id = _optional_str(payload, "property_id")
    if property_id is not None and property_id != auction.property_id:
        _raise_issue("illegal_action", f"auction is for {auction.property_id}", "payload.property_id")

    if actor.id in auction.passed_player_ids:
        _raise_issue("illegal_action", f"{actor.id} has already passed this auction", "actor_id")
    if actor.id == auction.high_bidder_id:
        _raise_issue("illegal_action", f"{actor.id} cannot increase their own high bid", "actor_id")

    amount = _required_int(payload, "amount")
    minimum_bid = _minimum_auction_bid(state)
    if amount < minimum_bid:
        _raise_issue("illegal_action", f"auction bid must be at least {minimum_bid}", "payload.amount")
    if actor.cash < amount:
        _raise_issue("illegal_action", "insufficient cash for auction bid", "payload.amount")


def _validate_auction_pass_action(state: GameState, actor: PlayerState, payload: Mapping[str, object]) -> None:
    auction = state.active_auction
    if auction is None:
        _raise_issue("mistimed_action", "there is no active auction", "type")

    property_id = _optional_str(payload, "property_id")
    if property_id is not None and property_id != auction.property_id:
        _raise_issue("illegal_action", f"auction is for {auction.property_id}", "payload.property_id")
    if actor.id in auction.passed_player_ids:
        _raise_issue("illegal_action", f"{actor.id} has already passed this auction", "actor_id")
    if actor.id == auction.high_bidder_id:
        _raise_issue("illegal_action", f"{actor.id} cannot pass while holding the high bid", "actor_id")


def _validate_jail_fine_action(
    state: GameState,
    actor: PlayerState,
    payload: Mapping[str, object],
) -> None:
    if state.active_auction is not None:
        _raise_issue("mistimed_action", "jail fine is not legal during an active auction", "type")
    if actor.id != state.turn.current_player_id:
        _raise_issue("mistimed_action", f"{actor.id} is not the current turn player", "actor_id")
    if not actor.in_jail:
        _raise_issue("illegal_action", f"{actor.id} is not in jail", "actor_id")
    if actor.cash < JAIL_FINE:
        _raise_issue("illegal_action", "insufficient cash to pay jail fine", "payload.amount")
    if "amount" in payload and _required_int(payload, "amount") != JAIL_FINE:
        _raise_issue("illegal_action", f"jail fine amount must be {JAIL_FINE}", "payload.amount")


def _validate_jail_card_action(state: GameState, actor: PlayerState, card_id: str) -> None:
    if state.active_auction is not None:
        _raise_issue("mistimed_action", "jail card use is not legal during an active auction", "type")
    if actor.id != state.turn.current_player_id:
        _raise_issue("mistimed_action", f"{actor.id} is not the current turn player", "actor_id")
    if not actor.in_jail:
        _raise_issue("illegal_action", f"{actor.id} is not in jail", "actor_id")
    if card_id not in actor.get_out_of_jail_card_ids:
        _raise_issue("illegal_action", f"{actor.id} does not hold {card_id}", "payload.card_id")
    card = _card_data(card_id)
    if card.effect.get("type") != "get_out_of_jail":
        _raise_issue("illegal_action", f"{card_id} is not a get-out-of-jail card", "payload.card_id")


def _validate_management_action(
    state: GameState,
    actor_id: str,
    property_id: str,
    action_type: str,
    mechanic: Callable[[GameState, str, str, str], GameState],
) -> None:
    if state.active_auction is not None:
        _raise_issue("mistimed_action", "property management is not legal during an active auction", "type")
    if not _mechanic_accepts(state, actor_id, property_id, action_type, mechanic):
        _raise_issue("illegal_action", f"{action_type} is not legal for {property_id}", "payload.property_id")


def _validate_bankruptcy_action(
    state: GameState,
    actor: PlayerState,
    payload: Mapping[str, object],
) -> None:
    creditor_id = _bankruptcy_creditor_for_payload(state, actor.id, payload)
    if creditor_id == actor.id:
        _raise_issue("illegal_action", "bankrupt player cannot be their own creditor", "payload.creditor_id")
    if creditor_id is not None:
        creditor = _player_by_id(state, creditor_id)
        if creditor is None or creditor.is_bankrupt:
            _raise_issue("illegal_action", f"unknown active creditor {creditor_id}", "payload.creditor_id")
    if not _mechanic_accepts_bankruptcy(state, actor.id, creditor_id):
        _raise_issue("illegal_action", "bankruptcy is not legal in the current state", "type")


def _validate_settle_debt_action(
    state: GameState,
    actor: PlayerState,
    payload: Mapping[str, object],
) -> None:
    active_payment = state.active_payment
    if active_payment is None:
        _raise_issue("mistimed_action", "SETTLE_DEBT requires an active debt", "type")
    if actor.id != active_payment.debtor_id:
        _raise_issue("mistimed_action", "only the active debtor may settle debt", "actor_id")

    amount = _required_int(payload, "amount")
    if amount <= 0:
        _raise_issue("malformed_action", "payload field amount must be a positive integer", "payload.amount")

    outstanding = outstanding_debt_amount(state)
    if amount > actor.cash:
        _raise_issue("illegal_action", "settlement amount exceeds debtor cash", "payload.amount")
    if amount > outstanding:
        _raise_issue("illegal_action", "settlement amount exceeds outstanding debt", "payload.amount")


def _mechanic_accepts(
    state: GameState,
    actor_id: str,
    property_id: str,
    action_type: str,
    mechanic: Callable[[GameState, str, str, str], GameState],
) -> bool:
    try:
        mechanic(state, actor_id, property_id, _probe_prefix(state, action_type, property_id))
    except IllegalRuleActionError:
        return False
    return True


def _mechanic_accepts_bankruptcy(state: GameState, actor_id: str, creditor_id: str | None) -> bool:
    try:
        declare_bankruptcy(state, actor_id, creditor_id, _probe_prefix(state, "DECLARE_BANKRUPTCY", actor_id))
    except IllegalRuleActionError:
        return False
    return True


def _is_management_action_enabled(
    action_type: str,
    allowed_action_types: frozenset[str] | None,
) -> bool:
    return allowed_action_types is None or action_type in allowed_action_types


def _close_auction_if_resolved(state: GameState, event_id_prefix: str) -> GameState:
    auction = state.active_auction
    if auction is None:
        return state

    active_player_ids = {player.id for player in state.players if not player.is_bankrupt}
    unpassed_player_ids = active_player_ids - set(auction.passed_player_ids)
    if auction.high_bidder_id is None:
        if not unpassed_player_ids:
            return close_auction(state, event_id_prefix)
        return state

    unresolved_competitors = unpassed_player_ids - {auction.high_bidder_id}
    if not unresolved_competitors:
        return close_auction(state, event_id_prefix)
    return state


def _minimum_auction_bid(state: GameState) -> int:
    auction = state.active_auction
    if auction is None or auction.high_bid_amount is None:
        return 1
    return auction.high_bid_amount + 1


def _stale_issues(state: GameState, action: GameAction) -> tuple[ActionValidationIssue, ...]:
    issues: list[ActionValidationIssue] = []
    if action.expected_state_hash != state.state_hash():
        issues.append(
            ActionValidationIssue(
                code="stale_action",
                message="action expected_state_hash does not match current state",
                field="expected_state_hash",
            )
        )
    if action.expected_event_sequence != state.event_sequence:
        issues.append(
            ActionValidationIssue(
                code="stale_action",
                message="action expected_event_sequence does not match current state",
                field="expected_event_sequence",
            )
        )
    return tuple(issues)


def _payload_mapping(action: GameAction) -> Mapping[str, object]:
    if not isinstance(action.payload, Mapping):
        _raise_issue("malformed_action", "action payload must be an object", "payload")
    return action.payload


def _validate_allowed_fields(payload: Mapping[str, object], allowed_fields: tuple[str, ...]) -> None:
    allowed = set(allowed_fields)
    unknown_fields = sorted(str(field) for field in payload if field not in allowed)
    if unknown_fields:
        _raise_issue(
            "malformed_action",
            f"unsupported payload field {unknown_fields[0]}",
            f"payload.{unknown_fields[0]}",
        )


def _required_str(payload: Mapping[str, object], field_name: str) -> str:
    if field_name not in payload:
        _raise_issue("malformed_action", f"payload field {field_name} is required", f"payload.{field_name}")
    value = payload[field_name]
    if not isinstance(value, str) or not value:
        _raise_issue("malformed_action", f"payload field {field_name} must be a string", f"payload.{field_name}")
    return value


def _optional_str(payload: Mapping[str, object], field_name: str) -> str | None:
    if field_name not in payload:
        return None
    return _required_str(payload, field_name)


def _optional_str_or_none(payload: Mapping[str, object], field_name: str) -> str | None:
    if field_name not in payload:
        return None
    value = payload[field_name]
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        _raise_issue("malformed_action", f"payload field {field_name} must be a string or null", f"payload.{field_name}")
    return value


def _required_int(payload: Mapping[str, object], field_name: str) -> int:
    if field_name not in payload:
        _raise_issue("malformed_action", f"payload field {field_name} is required", f"payload.{field_name}")
    value = payload[field_name]
    if not isinstance(value, int) or isinstance(value, bool):
        _raise_issue("malformed_action", f"payload field {field_name} must be an integer", f"payload.{field_name}")
    return value


def _raise_issue(code: str, message: str, field: str | None = None) -> NoReturn:
    raise ActionValidationError((ActionValidationIssue(code=code, message=message, field=field),))


def _raise_timing_issue(issue: ActionTimingIssue) -> NoReturn:
    raise ActionValidationError(
        (
            ActionValidationIssue(
                code=issue.code,
                message=issue.message,
                field=issue.field,
            ),
        )
    )


def _player_by_id(state: GameState, player_id: str) -> PlayerState | None:
    for player in state.players:
        if player.id == player_id:
            return player
    return None


def _property_ownership(state: GameState, property_id: str) -> PropertyOwnershipState:
    for ownership in state.property_ownership:
        if ownership.property_id == property_id:
            return ownership
    _raise_issue("illegal_action", f"unknown property {property_id}", "payload.property_id")


def _property_data(property_id: str) -> PropertyData:
    for property_data in load_classic_monopoly_data().properties:
        if property_data.id == property_id:
            return property_data
    _raise_issue("illegal_action", f"unknown property {property_id}", "payload.property_id")


def _space_for_position(position: int) -> BoardSpace:
    for space in load_classic_monopoly_data().board:
        if space.position == position:
            return space
    raise RuntimeError(f"unknown board position {position}")


def _card_data(card_id: str) -> CardData:
    for card in (*load_classic_monopoly_data().decks.chance, *load_classic_monopoly_data().decks.community_chest):
        if card.id == card_id:
            return card
    _raise_issue("illegal_action", f"unknown card {card_id}", "payload.card_id")


def _house_cost(property_data: PropertyData) -> int:
    if property_data.house_cost is None:
        _raise_issue("illegal_action", f"{property_data.id} has no house cost", "payload.property_id")
    return property_data.house_cost


def _mortgage_interest(mortgage_value: int) -> int:
    return (mortgage_value + 9) // 10


def _bankruptcy_schema(state: GameState, actor_id: str) -> Mapping[str, object]:
    if state.active_payment is not None and state.active_payment.debtor_id == actor_id:
        active_creditor_id = state.active_payment.creditor_id
        return _object_schema(
            {
                "creditor_id": {
                    "type": ["string", "null"],
                    "enum": [active_creditor_id],
                }
            }
        )

    creditor_choices: list[object] = [
        None,
        *(player.id for player in state.players if player.id != actor_id and not player.is_bankrupt),
    ]
    return _object_schema(
        {
            "creditor_id": {
                "type": ["string", "null"],
                "enum": creditor_choices,
            }
        }
    )


def _bankruptcy_creditor_for_payload(
    state: GameState,
    actor_id: str,
    payload: Mapping[str, object],
) -> str | None:
    active_payment = state.active_payment
    if active_payment is not None and active_payment.debtor_id == actor_id:
        active_creditor_id = active_payment.creditor_id
        if "creditor_id" not in payload:
            return active_creditor_id
        creditor_id = _optional_str_or_none(payload, "creditor_id")
        if creditor_id != active_creditor_id:
            _raise_issue(
                "illegal_action",
                "bankruptcy creditor must match active debt creditor",
                "payload.creditor_id",
            )
        return creditor_id
    return _optional_str_or_none(payload, "creditor_id")


def _empty_payload_schema() -> Mapping[str, object]:
    return _object_schema({})


def _object_schema(
    properties: Mapping[str, object],
    *,
    required: tuple[str, ...] = (),
) -> Mapping[str, object]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _const_string_schema(value: str) -> Mapping[str, object]:
    return {"type": "string", "const": value}


def _probe_prefix(state: GameState, action_type: str, object_id: str) -> str:
    sanitized_object_id = object_id.replace("_", "-")
    return f"legal-{state.state_hash()[:12]}-{state.event_sequence}-{action_type.lower()}-{sanitized_object_id}"


def _freeze_mapping(mapping: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze_value(value) for key, value in mapping.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: object, *, mode: str) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(inner_value, mode=mode) for key, inner_value in value.items()}
    if isinstance(value, tuple):
        thawed_items = [_thaw_value(item, mode=mode) for item in value]
        return thawed_items if mode == "json" else tuple(thawed_items)
    return value


__all__ = [
    "ActionValidationError",
    "ActionValidationIssue",
    "ActionExecutionResult",
    "GameAction",
    "LegalAction",
    "SUPPORTED_ACTION_TYPES",
    "ValidatedAction",
    "apply_action",
    "execute_action",
    "list_legal_actions",
    "validate_action",
]
