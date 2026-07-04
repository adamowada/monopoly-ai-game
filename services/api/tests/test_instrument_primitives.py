from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.metadata import deals, game_events, games, metadata
from app.main import create_app
from app.rules.financial_instruments import (
    InstrumentPrimitive,
    combination_deal,
    create_instrument,
    failure_reason,
    settle_instrument,
    validate_instrument,
)


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)

PLAYER_IDS = [
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
]
PROPERTY_IDS = [
    "property_mediterranean_avenue",
    "property_baltic_avenue",
    "property_reading_railroad",
]


VALID_PRIMITIVE_PAYLOADS: dict[str, dict[str, Any]] = {
    "immediate_cash_transfer": {
        "kind": "immediate_cash_transfer",
        "instrument_id": "cash-now",
        "from_player_id": PLAYER_IDS[0],
        "to_player_id": PLAYER_IDS[1],
        "amount": 50,
    },
    "immediate_property_transfer": {
        "kind": "immediate_property_transfer",
        "instrument_id": "property-now",
        "from_player_id": PLAYER_IDS[1],
        "to_player_id": PLAYER_IDS[0],
        "property_id": PROPERTY_IDS[0],
    },
    "deferred_cash_payment": {
        "kind": "deferred_cash_payment",
        "instrument_id": "cash-later",
        "from_player_id": PLAYER_IDS[0],
        "to_player_id": PLAYER_IDS[1],
        "amount": 80,
        "due_turn": 4,
    },
    "installment_loan": {
        "kind": "installment_loan",
        "instrument_id": "installment-loan-1",
        "lender_player_id": PLAYER_IDS[1],
        "borrower_player_id": PLAYER_IDS[0],
        "principal_amount": 120,
        "schedule": [
            {"due_turn": 2, "amount": 40},
            {"due_turn": 4, "amount": 40},
            {"due_turn": 6, "amount": 40},
        ],
    },
    "interest_bearing_debt": {
        "kind": "interest_bearing_debt",
        "instrument_id": "interest-debt-1",
        "lender_player_id": PLAYER_IDS[1],
        "borrower_player_id": PLAYER_IDS[0],
        "principal_amount": 200,
        "interest_rate_percent": 10,
        "due_turn": 8,
    },
    "collateralized_loan": {
        "kind": "collateralized_loan",
        "instrument_id": "collateral-loan-1",
        "lender_player_id": PLAYER_IDS[1],
        "borrower_player_id": PLAYER_IDS[0],
        "principal_amount": 150,
        "due_turn": 7,
        "collateral_property_ids": [PROPERTY_IDS[1]],
    },
    "property_purchase_option": {
        "kind": "property_purchase_option",
        "instrument_id": "option-1",
        "grantor_player_id": PLAYER_IDS[1],
        "holder_player_id": PLAYER_IDS[0],
        "property_id": PROPERTY_IDS[1],
        "strike_price": 220,
        "expiration_turn": 10,
    },
    "rent_share": {
        "kind": "rent_share",
        "instrument_id": "rent-share-1",
        "from_player_id": PLAYER_IDS[1],
        "to_player_id": PLAYER_IDS[0],
        "property_id": PROPERTY_IDS[0],
        "share_percent": 25,
        "duration_turns": 5,
    },
    "insurance_payout": {
        "kind": "insurance_payout",
        "instrument_id": "insurance-1",
        "insurer_player_id": PLAYER_IDS[1],
        "insured_player_id": PLAYER_IDS[0],
        "amount": 100,
        "trigger": {"type": "property_landed", "property_id": PROPERTY_IDS[0]},
    },
    "conditional_obligation": {
        "kind": "conditional_obligation",
        "instrument_id": "conditional-1",
        "obligor_player_id": PLAYER_IDS[0],
        "obligee_player_id": PLAYER_IDS[1],
        "amount": 60,
        "trigger": {"type": "turn_start", "turn": 3},
    },
    "guarantee": {
        "kind": "guarantee",
        "instrument_id": "guarantee-1",
        "guarantor_player_id": PLAYER_IDS[2],
        "guaranteed_player_id": PLAYER_IDS[0],
        "beneficiary_player_id": PLAYER_IDS[1],
        "amount": 75,
        "target_instrument_id": "interest-debt-1",
    },
    "default_penalty": {
        "kind": "default_penalty",
        "instrument_id": "default-penalty-1",
        "liable_player_id": PLAYER_IDS[0],
        "beneficiary_player_id": PLAYER_IDS[1],
        "amount": 30,
        "target_instrument_id": "interest-debt-1",
    },
}

INVALID_PRIMITIVE_PAYLOADS: dict[str, tuple[dict[str, Any], str]] = {
    "immediate_cash_transfer": (
        {**VALID_PRIMITIVE_PAYLOADS["immediate_cash_transfer"], "amount": 0},
        "amount",
    ),
    "immediate_property_transfer": (
        {**VALID_PRIMITIVE_PAYLOADS["immediate_property_transfer"], "property_id": "property_missing"},
        "property_id",
    ),
    "deferred_cash_payment": (
        {**VALID_PRIMITIVE_PAYLOADS["deferred_cash_payment"], "due_turn": 0},
        "due_turn",
    ),
    "installment_loan": (
        {
            **VALID_PRIMITIVE_PAYLOADS["installment_loan"],
            "schedule": [{"due_turn": 3, "amount": 40}, {"due_turn": 2, "amount": 40}],
        },
        "schedule.1.due_turn",
    ),
    "interest_bearing_debt": (
        {**VALID_PRIMITIVE_PAYLOADS["interest_bearing_debt"], "interest_rate_percent": 101},
        "interest_rate_percent",
    ),
    "collateralized_loan": (
        {**VALID_PRIMITIVE_PAYLOADS["collateralized_loan"], "collateral_property_ids": []},
        "collateral_property_ids",
    ),
    "property_purchase_option": (
        {**VALID_PRIMITIVE_PAYLOADS["property_purchase_option"], "expiration_turn": 0},
        "expiration_turn",
    ),
    "rent_share": (
        {**VALID_PRIMITIVE_PAYLOADS["rent_share"], "share_percent": 101},
        "share_percent",
    ),
    "insurance_payout": (
        {**VALID_PRIMITIVE_PAYLOADS["insurance_payout"], "trigger": {"type": "property_landed"}},
        "trigger.property_id",
    ),
    "conditional_obligation": (
        {**VALID_PRIMITIVE_PAYLOADS["conditional_obligation"], "trigger": {"turn": 3}},
        "trigger.type",
    ),
    "guarantee": (
        {**VALID_PRIMITIVE_PAYLOADS["guarantee"], "target_instrument_id": "missing-debt"},
        "target_instrument_id",
    ),
    "default_penalty": (
        {**VALID_PRIMITIVE_PAYLOADS["default_penalty"], "target_instrument_id": "missing-debt"},
        "target_instrument_id",
    ),
}


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await connection.run_sync(metadata.create_all)

    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        settings=Settings(
            api_env="test",
            database_url=TEST_DATABASE_URL,
            cors_origins="http://localhost:3000",
        )
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            yield client
    finally:
        await app.state.database_engine.dispose()


def _known_instrument_ids() -> set[str]:
    return {
        payload["instrument_id"]
        for payload in VALID_PRIMITIVE_PAYLOADS.values()
        if isinstance(payload.get("instrument_id"), str)
    }


def test_primitive_creation_validation_and_settlement_for_every_required_kind() -> None:
    # creation, validation, settlement
    for kind, payload in VALID_PRIMITIVE_PAYLOADS.items():
        instrument = create_instrument(payload)
        assert isinstance(instrument, InstrumentPrimitive)
        assert instrument.kind == kind
        assert instrument.payload == create_instrument(dict(reversed(payload.items()))).payload

        errors = validate_instrument(
            instrument,
            player_ids=PLAYER_IDS,
            property_ids=PROPERTY_IDS,
            instrument_ids=_known_instrument_ids(),
        )
        assert errors == []

        settlement = settle_instrument(
            instrument,
            player_ids=PLAYER_IDS,
            property_ids=PROPERTY_IDS,
            instrument_ids=_known_instrument_ids(),
        )
        repeated = settle_instrument(
            instrument,
            player_ids=PLAYER_IDS,
            property_ids=PROPERTY_IDS,
            instrument_ids=_known_instrument_ids(),
        )
        assert settlement.status == "planned"
        assert settlement.failure_reason is None
        assert settlement.model_dump(mode="json") == repeated.model_dump(mode="json")


@pytest.mark.parametrize(("kind", "case"), INVALID_PRIMITIVE_PAYLOADS.items())
def test_primitive_validation_failure_cases_return_clear_reasons(
    kind: str,
    case: tuple[dict[str, Any], str],
) -> None:
    # failure cases
    payload, field_suffix = case
    instrument = create_instrument(payload)

    errors = validate_instrument(
        instrument,
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        instrument_ids=_known_instrument_ids(),
        field=f"terms.{kind}",
    )

    assert errors
    assert all(error.code == "invalid_instrument" for error in errors)
    assert any(error.field and error.field.endswith(field_suffix) for error in errors)
    assert failure_reason(errors)

    settlement = settle_instrument(
        instrument,
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        instrument_ids=_known_instrument_ids(),
    )
    assert settlement.status == "failed"
    assert settlement.failure_reason == failure_reason(
        validate_instrument(
            instrument,
            player_ids=PLAYER_IDS,
            property_ids=PROPERTY_IDS,
            instrument_ids=_known_instrument_ids(),
        )
    )


def test_one_deal_represents_combinations_of_primitives() -> None:
    # One deal represents combinations of primitives
    payloads = [
        VALID_PRIMITIVE_PAYLOADS["immediate_cash_transfer"],
        VALID_PRIMITIVE_PAYLOADS["interest_bearing_debt"],
        VALID_PRIMITIVE_PAYLOADS["guarantee"],
        VALID_PRIMITIVE_PAYLOADS["default_penalty"],
    ]

    instruments, errors = combination_deal(
        payloads,
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        field="terms",
    )

    assert errors == []
    assert [instrument.kind for instrument in instruments] == [
        "immediate_cash_transfer",
        "interest_bearing_debt",
        "guarantee",
        "default_penalty",
    ]
    assert instruments[0].payload["amount"] == 50


def test_invalid_instruments_are_rejected_with_clear_reasons() -> None:
    # Invalid instruments are rejected with clear reasons
    payloads = [
        {**VALID_PRIMITIVE_PAYLOADS["immediate_cash_transfer"], "amount": -5},
        {**VALID_PRIMITIVE_PAYLOADS["rent_share"], "property_id": "property_missing"},
    ]

    instruments, errors = combination_deal(
        payloads,
        player_ids=PLAYER_IDS,
        property_ids=PROPERTY_IDS,
        field="terms",
    )

    assert len(instruments) == 2
    assert len(errors) == 2
    assert {error.code for error in errors} == {"invalid_instrument"}
    assert {error.field for error in errors} == {"terms.0.amount", "terms.1.property_id"}
    assert all(error.message for error in errors)


async def _create_game(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "phase-6-stage-6-4-instrument-primitives",
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "human"},
                {"name": "Linus", "kind": "ai"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _delete_game(session_factory: async_sessionmaker, game_id: str | UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(str(game_id))))


async def _create_negotiation(
    client: httpx.AsyncClient,
    game_id: str,
    player_ids: list[str],
) -> dict[str, Any]:
    response = await client.post(
        f"/games/{game_id}/negotiations",
        json={
            "opened_by_player_id": player_ids[0],
            "participant_player_ids": player_ids,
            "context": {"topic": "stage 6.4 primitive integration"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _table_count(
    session_factory: async_sessionmaker,
    table: sa.Table,
    game_id: str | UUID,
) -> int:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(sa.func.count()).select_from(table).where(table.c.game_id == UUID(str(game_id)))
        )
        return int(result.scalar_one())


@pytest.mark.asyncio
async def test_structured_deal_validation_rejects_invalid_instruments_with_clear_reasons(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    created = await _create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        negotiation = await _create_negotiation(client, game_id, player_ids)
        before_events = await _table_count(session_factory, game_events, game_id)

        response = await client.post(
            f"/games/{game_id}/deals",
            json={
                "negotiation_id": negotiation["id"],
                "proposed_by_player_id": player_ids[0],
                "terms": {
                    "kind": "structured_deal",
                    "deal_schema_version": 1,
                    "participants": player_ids,
                    "terms": [
                        {
                            "kind": "immediate_cash_transfer",
                            "from_player_id": player_ids[0],
                            "to_player_id": player_ids[1],
                            "amount": 0,
                        },
                        {
                            "kind": "rent_share",
                            "from_player_id": player_ids[1],
                            "to_player_id": player_ids[0],
                            "property_id": "property_missing",
                            "share_percent": 125,
                            "duration_turns": 2,
                        },
                    ],
                },
            },
        )

        assert response.status_code == 422, response.text
        body = response.json()
        assert body["reason_code"] == "invalid_structured_deal"
        assert len(body["validation_errors"]) >= 3
        assert all(error["code"] == "invalid_instrument" for error in body["validation_errors"])
        assert {error["field"] for error in body["validation_errors"]} >= {
            "terms.0.amount",
            "terms.1.property_id",
            "terms.1.share_percent",
        }
        assert await _table_count(session_factory, game_events, game_id) == before_events

        async with session_factory() as session:
            result = await session.execute(
                sa.select(deals.c.status, deals.c.validation_errors)
                .where(deals.c.game_id == UUID(str(game_id)))
                .order_by(deals.c.version)
            )
            rejected = result.mappings().one()
        assert rejected["status"] == "rejected"
        assert rejected["validation_errors"] == body["validation_errors"]
    finally:
        await _delete_game(session_factory, game_id)
