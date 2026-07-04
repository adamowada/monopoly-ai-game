from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.rules.static_data import load_classic_monopoly_data


INSTRUMENT_PRIMITIVE_KINDS = (
    "immediate_cash_transfer",
    "immediate_property_transfer",
    "deferred_cash_payment",
    "installment_loan",
    "interest_bearing_debt",
    "collateralized_loan",
    "property_purchase_option",
    "rent_share",
    "insurance_payout",
    "conditional_obligation",
    "guarantee",
    "default_penalty",
)

InstrumentSettlementStatus = Literal["planned", "failed", "no_op"]


class InstrumentValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = "invalid_instrument"
    message: str
    field: str | None = None


class InstrumentPrimitive(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class InstrumentSettlement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    status: InstrumentSettlementStatus
    settlement_type: str
    spec: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None


def create_instrument(payload: Mapping[str, Any]) -> InstrumentPrimitive:
    canonical_source = _canonical_mapping(payload)
    raw_kind = canonical_source.get("kind")
    kind = raw_kind.strip() if isinstance(raw_kind, str) else ""
    fields = {
        key: _canonical_field_value(key, value)
        for key, value in sorted(canonical_source.items())
        if key != "kind"
    }
    canonical_payload = {"kind": kind if kind else raw_kind, **fields}
    return InstrumentPrimitive(kind=kind, payload=canonical_payload)


def validate_instrument(
    instrument: InstrumentPrimitive,
    *,
    player_ids: Sequence[str] = (),
    property_ids: Collection[str] | None = None,
    instrument_ids: Collection[str] | None = None,
    field: str = "instrument",
) -> list[InstrumentValidationError]:
    errors: list[InstrumentValidationError] = []
    kind = instrument.kind
    if kind not in INSTRUMENT_PRIMITIVE_KINDS:
        return [
            _validation_error(
                "kind must be one of the supported financial instrument primitives",
                f"{field}.kind",
            )
        ]

    player_id_set = _normalized_player_id_set(player_ids)
    property_id_set = set(property_ids) if property_ids is not None else _classic_property_ids()
    instrument_id_set = {str(item) for item in instrument_ids} if instrument_ids is not None else None

    errors.extend(_validate_optional_instrument_id(instrument, field))

    if kind == "immediate_cash_transfer":
        errors.extend(_validate_players(instrument, field, player_id_set, "from_player_id", "to_player_id"))
        errors.extend(_validate_distinct(instrument, field, "from_player_id", "to_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "amount"))
    elif kind == "immediate_property_transfer":
        errors.extend(_validate_players(instrument, field, player_id_set, "from_player_id", "to_player_id"))
        errors.extend(_validate_distinct(instrument, field, "from_player_id", "to_player_id"))
        errors.extend(_validate_property_field(instrument, field, property_id_set, "property_id"))
    elif kind == "deferred_cash_payment":
        errors.extend(_validate_players(instrument, field, player_id_set, "from_player_id", "to_player_id"))
        errors.extend(_validate_distinct(instrument, field, "from_player_id", "to_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "amount"))
        errors.extend(_validate_positive_int_fields(instrument, field, "due_turn"))
    elif kind == "installment_loan":
        errors.extend(
            _validate_players(instrument, field, player_id_set, "lender_player_id", "borrower_player_id")
        )
        errors.extend(_validate_distinct(instrument, field, "lender_player_id", "borrower_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "principal_amount"))
        errors.extend(_validate_schedule(instrument, field))
    elif kind == "interest_bearing_debt":
        errors.extend(
            _validate_players(instrument, field, player_id_set, "lender_player_id", "borrower_player_id")
        )
        errors.extend(_validate_distinct(instrument, field, "lender_player_id", "borrower_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "principal_amount", "due_turn"))
        errors.extend(_validate_percentage_field(instrument, field, "interest_rate_percent"))
    elif kind == "collateralized_loan":
        errors.extend(
            _validate_players(instrument, field, player_id_set, "lender_player_id", "borrower_player_id")
        )
        errors.extend(_validate_distinct(instrument, field, "lender_player_id", "borrower_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "principal_amount", "due_turn"))
        errors.extend(_validate_property_list(instrument, field, property_id_set, "collateral_property_ids"))
    elif kind == "property_purchase_option":
        errors.extend(
            _validate_players(instrument, field, player_id_set, "grantor_player_id", "holder_player_id")
        )
        errors.extend(_validate_distinct(instrument, field, "grantor_player_id", "holder_player_id"))
        errors.extend(_validate_property_field(instrument, field, property_id_set, "property_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "strike_price", "expiration_turn"))
    elif kind == "rent_share":
        errors.extend(_validate_players(instrument, field, player_id_set, "from_player_id", "to_player_id"))
        errors.extend(_validate_distinct(instrument, field, "from_player_id", "to_player_id"))
        errors.extend(_validate_property_field(instrument, field, property_id_set, "property_id"))
        errors.extend(_validate_percentage_field(instrument, field, "share_percent"))
        errors.extend(_validate_positive_int_fields(instrument, field, "duration_turns"))
    elif kind == "insurance_payout":
        errors.extend(
            _validate_players(instrument, field, player_id_set, "insurer_player_id", "insured_player_id")
        )
        errors.extend(_validate_distinct(instrument, field, "insurer_player_id", "insured_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "amount"))
        errors.extend(_validate_trigger(instrument, field, player_id_set, property_id_set))
    elif kind == "conditional_obligation":
        errors.extend(
            _validate_players(instrument, field, player_id_set, "obligor_player_id", "obligee_player_id")
        )
        errors.extend(_validate_distinct(instrument, field, "obligor_player_id", "obligee_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "amount"))
        errors.extend(_validate_trigger(instrument, field, player_id_set, property_id_set))
    elif kind == "guarantee":
        errors.extend(
            _validate_players(
                instrument,
                field,
                player_id_set,
                "guarantor_player_id",
                "guaranteed_player_id",
                "beneficiary_player_id",
            )
        )
        errors.extend(_validate_positive_int_fields(instrument, field, "amount"))
        errors.extend(_validate_instrument_reference(instrument, field, instrument_id_set))
    elif kind == "default_penalty":
        errors.extend(
            _validate_players(instrument, field, player_id_set, "liable_player_id", "beneficiary_player_id")
        )
        errors.extend(_validate_distinct(instrument, field, "liable_player_id", "beneficiary_player_id"))
        errors.extend(_validate_positive_int_fields(instrument, field, "amount"))
        errors.extend(_validate_instrument_reference(instrument, field, instrument_id_set))

    return errors


def settle_instrument(
    instrument: InstrumentPrimitive,
    *,
    player_ids: Sequence[str] = (),
    property_ids: Collection[str] | None = None,
    instrument_ids: Collection[str] | None = None,
) -> InstrumentSettlement:
    errors = validate_instrument(
        instrument,
        player_ids=player_ids,
        property_ids=property_ids,
        instrument_ids=instrument_ids,
    )
    if errors:
        return invalid_instrument(instrument, errors)

    payload = instrument.payload
    kind = instrument.kind
    if kind == "immediate_cash_transfer":
        return _settlement(
            kind,
            "immediate_transfer",
            {
                "transfers": [
                    {
                        "type": "cash",
                        "from_player_id": payload["from_player_id"],
                        "to_player_id": payload["to_player_id"],
                        "amount": payload["amount"],
                    }
                ]
            },
        )
    if kind == "immediate_property_transfer":
        return _settlement(
            kind,
            "immediate_transfer",
            {
                "transfers": [
                    {
                        "type": "property",
                        "from_player_id": payload["from_player_id"],
                        "to_player_id": payload["to_player_id"],
                        "property_id": payload["property_id"],
                    }
                ]
            },
        )
    if kind == "deferred_cash_payment":
        return _settlement(
            kind,
            "future_obligation",
            {
                "obligation": {
                    "type": "cash_payment",
                    "from_player_id": payload["from_player_id"],
                    "to_player_id": payload["to_player_id"],
                    "amount": payload["amount"],
                    "due_turn": payload["due_turn"],
                }
            },
        )
    if kind == "installment_loan":
        return _settlement(
            kind,
            "future_obligation",
            {
                "obligation": {
                    "type": "installment_loan",
                    "lender_player_id": payload["lender_player_id"],
                    "borrower_player_id": payload["borrower_player_id"],
                    "principal_amount": payload["principal_amount"],
                    "schedule": payload["schedule"],
                }
            },
        )
    if kind == "interest_bearing_debt":
        return _settlement(
            kind,
            "future_obligation",
            {
                "obligation": {
                    "type": "interest_bearing_debt",
                    "lender_player_id": payload["lender_player_id"],
                    "borrower_player_id": payload["borrower_player_id"],
                    "principal_amount": payload["principal_amount"],
                    "interest_rate_percent": payload["interest_rate_percent"],
                    "due_turn": payload["due_turn"],
                }
            },
        )
    if kind == "collateralized_loan":
        return _settlement(
            kind,
            "collateral_claim",
            {
                "obligation": {
                    "type": "collateralized_loan",
                    "lender_player_id": payload["lender_player_id"],
                    "borrower_player_id": payload["borrower_player_id"],
                    "principal_amount": payload["principal_amount"],
                    "due_turn": payload["due_turn"],
                },
                "collateral_claim": {
                    "property_ids": payload["collateral_property_ids"],
                    "trigger": {
                        "type": "default",
                        "instrument_id": payload.get("instrument_id"),
                    },
                },
            },
        )
    if kind == "property_purchase_option":
        return _settlement(
            kind,
            "option",
            {
                "option": {
                    "grantor_player_id": payload["grantor_player_id"],
                    "holder_player_id": payload["holder_player_id"],
                    "property_id": payload["property_id"],
                    "strike_price": payload["strike_price"],
                    "expiration_turn": payload["expiration_turn"],
                }
            },
        )
    if kind == "rent_share":
        return _settlement(
            kind,
            "trigger",
            {
                "trigger": {"type": "rent_collected", "property_id": payload["property_id"]},
                "rent_share": {
                    "from_player_id": payload["from_player_id"],
                    "to_player_id": payload["to_player_id"],
                    "share_percent": payload["share_percent"],
                    "duration_turns": payload["duration_turns"],
                },
            },
        )
    if kind == "insurance_payout":
        return _settlement(
            kind,
            "trigger",
            {
                "trigger": payload["trigger"],
                "payout": {
                    "from_player_id": payload["insurer_player_id"],
                    "to_player_id": payload["insured_player_id"],
                    "amount": payload["amount"],
                },
            },
        )
    if kind == "conditional_obligation":
        return _settlement(
            kind,
            "conditional_obligation",
            {
                "trigger": payload["trigger"],
                "obligation": {
                    "from_player_id": payload["obligor_player_id"],
                    "to_player_id": payload["obligee_player_id"],
                    "amount": payload["amount"],
                },
            },
        )
    if kind == "guarantee":
        return _settlement(
            kind,
            "guarantee_exposure",
            {
                "guarantee_exposure": {
                    "guarantor_player_id": payload["guarantor_player_id"],
                    "guaranteed_player_id": payload["guaranteed_player_id"],
                    "beneficiary_player_id": payload["beneficiary_player_id"],
                    "amount": payload["amount"],
                    "target_instrument_id": payload["target_instrument_id"],
                }
            },
        )
    if kind == "default_penalty":
        return _settlement(
            kind,
            "default_penalty",
            {
                "default_penalty": {
                    "liable_player_id": payload["liable_player_id"],
                    "beneficiary_player_id": payload["beneficiary_player_id"],
                    "amount": payload["amount"],
                    "target_instrument_id": payload["target_instrument_id"],
                }
            },
        )

    return invalid_instrument(
        instrument,
        [
            _validation_error(
                "kind must be one of the supported financial instrument primitives",
                "instrument.kind",
            )
        ],
    )


def combination_deal(
    payloads: Sequence[Mapping[str, Any]],
    *,
    player_ids: Sequence[str],
    property_ids: Collection[str] | None = None,
    field: str = "terms",
) -> tuple[list[InstrumentPrimitive], list[InstrumentValidationError]]:
    instruments = [create_instrument(payload) for payload in payloads]
    instrument_ids = _instrument_ids_for_combination(instruments)
    errors: list[InstrumentValidationError] = []
    errors.extend(_duplicate_instrument_id_errors(instruments, field))
    for index, instrument in enumerate(instruments):
        errors.extend(
            validate_instrument(
                instrument,
                player_ids=player_ids,
                property_ids=property_ids,
                instrument_ids=instrument_ids,
                field=f"{field}.{index}",
            )
        )
    return instruments, errors


def failure_reason(errors: Sequence[InstrumentValidationError | Mapping[str, Any]]) -> str | None:
    if not errors:
        return None
    messages: list[str] = []
    for error in errors:
        if isinstance(error, InstrumentValidationError):
            field = error.field
            message = error.message
        else:
            raw_field = error.get("field")
            field = raw_field if isinstance(raw_field, str) else None
            raw_message = error.get("message")
            message = raw_message if isinstance(raw_message, str) else "invalid instrument"
        messages.append(f"{field}: {message}" if field else message)
    return "; ".join(messages)


def invalid_instrument(
    instrument: InstrumentPrimitive | str,
    errors: Sequence[InstrumentValidationError | Mapping[str, Any]],
) -> InstrumentSettlement:
    kind = instrument.kind if isinstance(instrument, InstrumentPrimitive) else instrument
    return InstrumentSettlement(
        kind=kind or "invalid_instrument",
        status="failed",
        settlement_type="failure",
        spec={},
        failure_reason=failure_reason(errors) or "invalid instrument",
    )


def _settlement(kind: str, settlement_type: str, spec: Mapping[str, Any]) -> InstrumentSettlement:
    return InstrumentSettlement(
        kind=kind,
        status="planned",
        settlement_type=settlement_type,
        spec=_canonical_mapping(spec),
        failure_reason=None,
    )


def _validate_optional_instrument_id(
    instrument: InstrumentPrimitive,
    field: str,
) -> list[InstrumentValidationError]:
    if "instrument_id" not in instrument.payload:
        return []
    value = instrument.payload["instrument_id"]
    if isinstance(value, str) and value.strip():
        return []
    return [_validation_error("instrument_id must be a non-empty string", f"{field}.instrument_id")]


def _validate_players(
    instrument: InstrumentPrimitive,
    field: str,
    player_ids: set[str],
    *field_names: str,
) -> list[InstrumentValidationError]:
    return [
        error
        for field_name in field_names
        for error in _validate_player_field(instrument, field, player_ids, field_name)
    ]


def _validate_player_field(
    instrument: InstrumentPrimitive,
    field: str,
    player_ids: set[str],
    field_name: str,
) -> list[InstrumentValidationError]:
    value = instrument.payload.get(field_name)
    normalized = _normalize_uuid_string(value)
    if normalized is None:
        return [_validation_error(f"{field_name} must be a player UUID string", f"{field}.{field_name}")]
    if player_ids and normalized not in player_ids:
        return [_validation_error(f"{field_name} must reference a deal participant", f"{field}.{field_name}")]
    return []


def _validate_distinct(
    instrument: InstrumentPrimitive,
    field: str,
    left_field: str,
    right_field: str,
) -> list[InstrumentValidationError]:
    left = _normalize_uuid_string(instrument.payload.get(left_field))
    right = _normalize_uuid_string(instrument.payload.get(right_field))
    if left is not None and right is not None and left == right:
        return [
            _validation_error(
                f"{left_field} and {right_field} must reference different players",
                f"{field}.{right_field}",
            )
        ]
    return []


def _validate_positive_int_fields(
    instrument: InstrumentPrimitive,
    field: str,
    *field_names: str,
) -> list[InstrumentValidationError]:
    errors: list[InstrumentValidationError] = []
    for field_name in field_names:
        value = instrument.payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append(_validation_error(f"{field_name} must be a positive integer", f"{field}.{field_name}"))
    return errors


def _validate_percentage_field(
    instrument: InstrumentPrimitive,
    field: str,
    field_name: str,
) -> list[InstrumentValidationError]:
    value = instrument.payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0 or value > 100:
        return [_validation_error(f"{field_name} must be greater than 0 and at most 100", f"{field}.{field_name}")]
    return []


def _validate_property_field(
    instrument: InstrumentPrimitive,
    field: str,
    property_ids: set[str],
    field_name: str,
) -> list[InstrumentValidationError]:
    value = instrument.payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        return [_validation_error(f"{field_name} must be a property id", f"{field}.{field_name}")]
    if value not in property_ids:
        return [_validation_error(f"{field_name} must reference a known property", f"{field}.{field_name}")]
    return []


def _validate_property_list(
    instrument: InstrumentPrimitive,
    field: str,
    property_ids: set[str],
    field_name: str,
) -> list[InstrumentValidationError]:
    value = instrument.payload.get(field_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)) or not value:
        return [_validation_error(f"{field_name} must include at least one property id", f"{field}.{field_name}")]

    errors: list[InstrumentValidationError] = []
    seen: set[str] = set()
    for index, property_id in enumerate(value):
        item_field = f"{field}.{field_name}.{index}"
        if not isinstance(property_id, str) or not property_id.strip():
            errors.append(_validation_error("collateral property ids must be strings", item_field))
            continue
        if property_id in seen:
            errors.append(_validation_error("collateral property ids must be unique", item_field))
        if property_id not in property_ids:
            errors.append(_validation_error("collateral property id must reference a known property", item_field))
        seen.add(property_id)
    return errors


def _validate_schedule(
    instrument: InstrumentPrimitive,
    field: str,
) -> list[InstrumentValidationError]:
    value = instrument.payload.get("schedule")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)) or not value:
        return [_validation_error("schedule must include at least one payment", f"{field}.schedule")]

    errors: list[InstrumentValidationError] = []
    previous_due_turn = 0
    for index, item in enumerate(value):
        item_field = f"{field}.schedule.{index}"
        if not isinstance(item, Mapping):
            errors.append(_validation_error("schedule entries must be objects", item_field))
            continue
        due_turn = item.get("due_turn")
        amount = item.get("amount")
        if isinstance(due_turn, bool) or not isinstance(due_turn, int) or due_turn <= 0:
            errors.append(_validation_error("schedule due_turn must be a positive integer", f"{item_field}.due_turn"))
        elif due_turn <= previous_due_turn:
            errors.append(_validation_error("schedule due_turn values must increase", f"{item_field}.due_turn"))
        else:
            previous_due_turn = due_turn
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            errors.append(_validation_error("schedule amount must be a positive integer", f"{item_field}.amount"))
    return errors


def _validate_trigger(
    instrument: InstrumentPrimitive,
    field: str,
    player_ids: set[str],
    property_ids: set[str],
) -> list[InstrumentValidationError]:
    trigger = instrument.payload.get("trigger")
    if not isinstance(trigger, Mapping):
        return [_validation_error("trigger must be an object", f"{field}.trigger")]

    trigger_type = trigger.get("type")
    if not isinstance(trigger_type, str) or not trigger_type.strip():
        return [_validation_error("trigger.type must be a non-empty string", f"{field}.trigger.type")]

    trigger_type = trigger_type.strip()
    if trigger_type in {"property_landed", "rent_collected"}:
        property_id = trigger.get("property_id")
        if not isinstance(property_id, str) or not property_id.strip():
            return [
                _validation_error(
                    f"{trigger_type} trigger must include property_id",
                    f"{field}.trigger.property_id",
                )
            ]
        if property_id not in property_ids:
            return [
                _validation_error(
                    "trigger.property_id must reference a known property",
                    f"{field}.trigger.property_id",
                )
            ]
        return []

    if trigger_type in {"turn_start", "turn_end"}:
        turn = trigger.get("turn")
        if isinstance(turn, bool) or not isinstance(turn, int) or turn <= 0:
            return [_validation_error(f"{trigger_type} trigger must include a positive turn", f"{field}.trigger.turn")]
        return []

    if trigger_type == "bankruptcy":
        player_id = trigger.get("player_id")
        if player_id is None:
            return []
        normalized = _normalize_uuid_string(player_id)
        if normalized is None or (player_ids and normalized not in player_ids):
            return [
                _validation_error(
                    "bankruptcy trigger player_id must reference a deal participant",
                    f"{field}.trigger.player_id",
                )
            ]
        return []

    if trigger_type == "default":
        target_id = trigger.get("instrument_id")
        if not isinstance(target_id, str) or not target_id.strip():
            return [
                _validation_error(
                    "default trigger must include instrument_id",
                    f"{field}.trigger.instrument_id",
                )
            ]
        return []

    if trigger_type == "custom":
        name = trigger.get("name")
        if not isinstance(name, str) or not name.strip():
            return [_validation_error("custom trigger must include name", f"{field}.trigger.name")]
        return []

    return [
        _validation_error(
            "trigger.type must be property_landed, rent_collected, turn_start, turn_end, bankruptcy, default, or custom",
            f"{field}.trigger.type",
        )
    ]


def _validate_instrument_reference(
    instrument: InstrumentPrimitive,
    field: str,
    instrument_ids: set[str] | None,
) -> list[InstrumentValidationError]:
    target_id = instrument.payload.get("target_instrument_id")
    if not isinstance(target_id, str) or not target_id.strip():
        return [
            _validation_error(
                "target_instrument_id must reference another instrument",
                f"{field}.target_instrument_id",
            )
        ]
    instrument_id = instrument.payload.get("instrument_id")
    if isinstance(instrument_id, str) and instrument_id == target_id:
        return [
            _validation_error(
                "target_instrument_id cannot reference the same instrument",
                f"{field}.target_instrument_id",
            )
        ]
    if instrument_ids is not None and target_id not in instrument_ids:
        return [
            _validation_error(
                "target_instrument_id must reference an instrument in the same deal",
                f"{field}.target_instrument_id",
            )
        ]
    return []


def _instrument_ids_for_combination(instruments: Sequence[InstrumentPrimitive]) -> set[str]:
    instrument_ids: set[str] = set()
    for instrument in instruments:
        instrument_id = instrument.payload.get("instrument_id")
        if isinstance(instrument_id, str) and instrument_id.strip():
            instrument_ids.add(instrument_id)
    return instrument_ids


def _duplicate_instrument_id_errors(
    instruments: Sequence[InstrumentPrimitive],
    field: str,
) -> list[InstrumentValidationError]:
    first_index_by_id: dict[str, int] = {}
    errors: list[InstrumentValidationError] = []
    for index, instrument in enumerate(instruments):
        instrument_id = instrument.payload.get("instrument_id")
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            continue
        if instrument_id in first_index_by_id:
            errors.append(
                _validation_error(
                    "instrument_id must be unique within a structured deal",
                    f"{field}.{index}.instrument_id",
                )
            )
        else:
            first_index_by_id[instrument_id] = index
    return errors


def _validation_error(message: str, field: str) -> InstrumentValidationError:
    return InstrumentValidationError(message=message, field=field)


def _canonical_field_value(key: str, value: object) -> Any:
    if key.endswith("_player_id") or key in {
        "from_player_id",
        "to_player_id",
        "grantor_player_id",
        "holder_player_id",
        "liable_player_id",
    }:
        return _normalize_uuid_string(value) or _canonical_json(value)
    if key.endswith("_player_ids"):
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
            return [_normalize_uuid_string(item) or _canonical_json(item) for item in value]
        return _canonical_json(value)
    if key in {"instrument_id", "target_instrument_id"} and isinstance(value, str):
        return value.strip()
    if key.endswith("_property_ids") and isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, Mapping)
    ):
        return sorted(str(item).strip() for item in value)
    if key == "trigger" and isinstance(value, Mapping):
        return {
            str(item_key): _canonical_field_value(str(item_key), item_value)
            for item_key, item_value in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if key == "schedule" and isinstance(value, Sequence) and not isinstance(value, (str, bytes, Mapping)):
        return [_canonical_json(item) for item in value]
    return _canonical_json(value)


def _canonical_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _canonical_json(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}


def _canonical_json(value: object) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return _canonical_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_canonical_json(item) for item in value]
    return value


def _normalized_player_id_set(player_ids: Sequence[str]) -> set[str]:
    return {normalized for item in player_ids if (normalized := _normalize_uuid_string(item)) is not None}


def _normalize_uuid_string(value: object) -> str | None:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _classic_property_ids() -> set[str]:
    return {property_data.id for property_data in load_classic_monopoly_data().properties}
