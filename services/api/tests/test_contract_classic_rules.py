from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.contracts.classic_rules import (
    DEFAULT_CONTRACT_CLASSIC_RULE_POLICY,
    bankruptcy_resolution_plan,
    impossible_state_prevention_check,
    resolve_contract_classic_rule_interaction,
)
from app.core.config import Settings
from app.db.metadata import games, metadata
from app.db.persistence import AcceptedEventTemplate
from app.main import create_app
from app.rules.events import (
    ActiveAuctionSetPayload,
    ActiveBankruptcySetPayload,
    ActivePaymentSetPayload,
    GameEvent,
    PlayerCashDeltaPayload,
    PropertyImprovementsSetPayload,
    PropertyMortgageSetPayload,
    PropertyOwnerSetPayload,
)
from app.rules.reducer import apply_event
from app.rules.state import GameState, PlayerSetup, create_initial_game_state


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)

GAME_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CONTRACT_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
DEAL_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
P1 = "11111111-1111-4111-8111-111111111111"
P2 = "22222222-2222-4222-8222-222222222222"
P3 = "33333333-3333-4333-8333-333333333333"


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


def _state() -> GameState:
    return create_initial_game_state(
        seed="phase-6-stage-6-6",
        game_id=str(GAME_ID),
        players=(
            PlayerSetup(id=P1, name="Ada", kind="human"),
            PlayerSetup(id=P2, name="Grace", kind="human"),
            PlayerSetup(id=P3, name="Linus", kind="ai"),
        ),
    )


def _apply_setup_event(state: GameState, event_type: str, payload: object) -> GameState:
    return apply_event(
        state,
        GameEvent(
            event_id=f"setup-{state.event_sequence + 1}",
            sequence=state.event_sequence + 1,
            type=event_type,  # type: ignore[arg-type]
            payload=payload,  # type: ignore[arg-type]
        ),
    )


def _set_cash(state: GameState, player_id: str, cash: int) -> GameState:
    current = next(player for player in state.players if player.id == player_id)
    return _apply_setup_event(
        state,
        "PLAYER_CASH_DELTA",
        PlayerCashDeltaPayload(player_id=player_id, amount=cash - current.cash),
    )


def _own(state: GameState, property_id: str, owner_id: str | None) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_OWNER_SET",
        PropertyOwnerSetPayload(property_id=property_id, owner_id=owner_id),
    )


def _mortgage(state: GameState, property_id: str) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_MORTGAGE_SET",
        PropertyMortgageSetPayload(property_id=property_id, mortgaged=True),
    )


def _improve(state: GameState, property_id: str, houses: int) -> GameState:
    return _apply_setup_event(
        state,
        "PROPERTY_IMPROVEMENTS_SET",
        PropertyImprovementsSetPayload(property_id=property_id, houses=houses, hotel=False),
    )


def _active_auction(state: GameState) -> GameState:
    return _apply_setup_event(
        state,
        "ACTIVE_AUCTION_SET",
        ActiveAuctionSetPayload(active=True, property_id="property_reading_railroad"),
    )


def _active_bankruptcy(state: GameState) -> GameState:
    return _apply_setup_event(state, "ACTIVE_BANKRUPTCY_SET", ActiveBankruptcySetPayload(active=True))


def _active_payment(state: GameState) -> GameState:
    return _apply_setup_event(
        state,
        "ACTIVE_PAYMENT_SET",
        ActivePaymentSetPayload(
            active=True,
            debtor_id=P1,
            creditor_id=P2,
            amount_owed=200,
            amount_paid=0,
            reason="rent",
            negotiation_allowed=True,
        ),
    )


def _obligation(
    *,
    obligation_id: str = "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    obligation_type: str = "cash_payment",
    owed_by: str = P1,
    owed_to: str = P2,
    terms: Mapping[str, Any] | None = None,
    schedule: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": UUID(obligation_id),
        "game_id": GAME_ID,
        "contract_id": CONTRACT_ID,
        "owed_by_player_id": UUID(owed_by),
        "owed_to_player_id": UUID(owed_to),
        "settled_event_id": None,
        "status": "pending",
        "obligation_type": obligation_type,
        "schedule": {"trigger": {"type": "round", "round": 1}} if schedule is None else dict(schedule),
        "terms": {
            "settlement_action": "cash_transfer",
            "from_player_id": owed_by,
            "to_player_id": owed_to,
            "amount": 50,
            **dict(terms or {}),
        },
        "due_at": None,
    }


def test_impossible_state_prevention_rejects_negative_cash_and_owner_mismatches() -> None:
    # Contract obligations do not leave money/property in impossible states
    state = _set_cash(_own(_state(), "property_reading_railroad", P2), P1, 25)
    cash_obligation = _obligation(terms={"amount": 75})

    cash_decision = resolve_contract_classic_rule_interaction(
        cash_obligation,
        state=state,
        trigger_context={"type": "round", "round": 1},
    )

    assert DEFAULT_CONTRACT_CLASSIC_RULE_POLICY["impossible_state_prevention"] == "strict"
    assert cash_decision.status == "default"
    assert cash_decision.policy_key == "impossible_state_prevention"
    assert cash_decision.reason_code == "insufficient_cash"

    unsafe_events = [
        AcceptedEventTemplate(event_type="PLAYER_CASH_DELTA", payload={"player_id": P1, "amount": -75}),
        AcceptedEventTemplate(
            event_type="PROPERTY_OWNER_SET",
            payload={"property_id": "property_reading_railroad", "owner_id": P1},
        ),
    ]
    issues = impossible_state_prevention_check(
        state=state,
        obligation_row=_obligation(
            obligation_type="property_transfer",
            terms={
                "settlement_action": "property_transfer",
                "property_id": "property_reading_railroad",
                "from_player_id": P1,
                "to_player_id": P3,
            },
        ),
        event_templates=unsafe_events,
    )

    assert {issue["code"] for issue in issues} >= {"negative_cash", "property_owner_mismatch"}


def test_bankruptcy_combines_classic_debts_and_contract_obligations_deterministically() -> None:
    # Bankruptcy resolves both classic debts and custom contract obligations deterministically
    state = _active_payment(_own(_set_cash(_state(), P1, 100), "property_baltic_avenue", P1))
    cash_obligation = _obligation(
        obligation_id="10000000-0000-4000-8000-000000000001",
        obligation_type="cash_payment",
        owed_to=P3,
        terms={"amount": 50},
    )
    collateral_obligation = _obligation(
        obligation_id="10000000-0000-4000-8000-000000000002",
        obligation_type="collateralized_loan",
        owed_to=P2,
        terms={
            "amount": 125,
            "collateral_property_ids": ["property_baltic_avenue"],
            "settlement_action": "cash_transfer",
        },
    )

    first = bankruptcy_resolution_plan(
        state=state,
        bankrupt_player_id=P1,
        classic_creditor_id=P2,
        obligations=[collateral_obligation, cash_obligation],
    )
    second = bankruptcy_resolution_plan(
        state=state,
        bankrupt_player_id=P1,
        classic_creditor_id=P2,
        obligations=[cash_obligation, collateral_obligation],
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.policy["contract_obligations_affect_bankruptcy"] == "after_classic_debts"
    assert [decision["kind"] for decision in first.decisions] == [
        "classic_debt",
        "contract_obligation",
        "contract_obligation",
    ]
    assert first.decisions[0]["amount_paid"] == 100
    assert first.decisions[1]["decision"] == "collateral_seizure"
    assert first.decisions[2]["decision"] == "default_after_bankruptcy_estate_exhausted"
    assert first.resulting_cash_by_player_id[P1] == 0
    assert first.resulting_property_owner_by_property_id["property_baltic_avenue"] == P2


def test_collateral_seizure_prevents_duplicate_or_unavailable_property_states() -> None:
    # collateral seizure
    state = _own(_own(_set_cash(_state(), P1, 10), "property_baltic_avenue", P1), "property_reading_railroad", P3)
    valid = _obligation(
        obligation_type="collateralized_loan",
        terms={
            "amount": 125,
            "collateral_property_ids": ["property_baltic_avenue"],
            "settlement_action": "cash_transfer",
        },
    )
    invalid = _obligation(
        obligation_type="collateralized_loan",
        terms={
            "amount": 125,
            "collateral_property_ids": [
                "property_baltic_avenue",
                "property_baltic_avenue",
                "property_reading_railroad",
            ],
            "settlement_action": "cash_transfer",
        },
    )

    valid_decision = resolve_contract_classic_rule_interaction(
        valid,
        state=state,
        trigger_context={"type": "default", "instrument_id": "loan-1"},
    )
    invalid_decision = resolve_contract_classic_rule_interaction(
        invalid,
        state=state,
        trigger_context={"type": "default", "instrument_id": "loan-1"},
    )

    assert valid_decision.status == "settle"
    assert valid_decision.policy_key == "collateral_seizure"
    assert [event.event_type for event in valid_decision.event_templates] == ["PROPERTY_OWNER_SET"]
    assert valid_decision.event_templates[0].payload == {
        "property_id": "property_baltic_avenue",
        "owner_id": P2,
    }
    assert invalid_decision.status == "default"
    assert invalid_decision.reason_code == "collateral_unavailable"
    assert invalid_decision.event_templates == ()
    assert "duplicate collateral" in invalid_decision.explanation_text
    assert "unavailable collateral" in invalid_decision.explanation_text


def test_options_on_mortgaged_or_improved_properties_are_explained_deterministically() -> None:
    # options on mortgaged or improved properties
    mortgaged_state = _mortgage(_own(_state(), "property_baltic_avenue", P2), "property_baltic_avenue")
    improved_state = _improve(_own(_state(), "property_oriental_avenue", P2), "property_oriental_avenue", 1)
    mortgaged_option = _obligation(
        obligation_type="property_option",
        owed_by=P2,
        owed_to=P1,
        terms={
            "settlement_action": "record_option_expiration",
            "property_id": "property_baltic_avenue",
            "strike_price": 120,
        },
    )
    improved_option = _obligation(
        obligation_type="property_option",
        owed_by=P2,
        owed_to=P1,
        terms={
            "settlement_action": "record_option_expiration",
            "property_id": "property_oriental_avenue",
            "strike_price": 160,
        },
    )

    mortgaged_decision = resolve_contract_classic_rule_interaction(
        mortgaged_option,
        state=mortgaged_state,
        trigger_context={"type": "round", "round": 4},
    )
    improved_decision = resolve_contract_classic_rule_interaction(
        improved_option,
        state=improved_state,
        trigger_context={"type": "round", "round": 4},
    )

    assert mortgaged_decision.status == "reject"
    assert mortgaged_decision.policy_key == "mortgaged_option_policy"
    assert "mortgaged" in mortgaged_decision.explanation_text
    assert improved_decision.status == "defer"
    assert improved_decision.policy_key == "improved_property_option_policy"
    assert "improved" in improved_decision.explanation_text


def test_rent_share_settlement_uses_reduced_waived_and_unpaid_rent_state() -> None:
    # rent sharing when rent is reduced, waived, or unpaid
    state = _state()
    rent_share = _obligation(
        obligation_type="rent_share",
        terms={
            "settlement_action": "rent_share_cash_payment",
            "amount": None,
            "share_percent": 50,
            "property_id": "property_baltic_avenue",
        },
    )

    reduced = resolve_contract_classic_rule_interaction(
        rent_share,
        state=state,
        trigger_context={
            "type": "rent_collected",
            "property_id": "property_baltic_avenue",
            "rent_owed_amount": 100,
            "rent_paid_amount": 60,
            "rent_status": "reduced",
        },
    )
    waived = resolve_contract_classic_rule_interaction(
        rent_share,
        state=state,
        trigger_context={
            "type": "rent_collected",
            "property_id": "property_baltic_avenue",
            "rent_owed_amount": 100,
            "rent_paid_amount": 0,
            "rent_status": "waived",
        },
    )
    unpaid = resolve_contract_classic_rule_interaction(
        rent_share,
        state=state,
        trigger_context={
            "type": "rent_collected",
            "property_id": "property_baltic_avenue",
            "rent_owed_amount": 100,
            "rent_paid_amount": 0,
            "rent_status": "unpaid",
        },
    )

    assert reduced.status == "settle"
    assert reduced.policy_key == "rent_share_reduced_rent"
    assert reduced.cash_amount == 30
    assert [event.payload for event in reduced.event_templates] == [
        {"player_id": P1, "amount": -30},
        {"player_id": P2, "amount": 30},
    ]
    assert waived.status == "settle"
    assert waived.policy_key == "rent_share_waived_rent"
    assert waived.event_templates == ()
    assert unpaid.status == "defer"
    assert unpaid.policy_key == "rent_share_unpaid_rent"


def test_timing_sensitive_jail_auction_and_bankruptcy_policies_are_deterministic() -> None:
    # obligations during jail, auction, and bankruptcy
    jailed_state = GameState.model_validate(
        {
            **_state().model_dump(mode="python"),
            "players": [
                {
                    **player.model_dump(mode="python"),
                    "in_jail": player.id == P1,
                    "jail_turns": 1 if player.id == P1 else 0,
                }
                for player in _state().players
            ],
        }
    )
    auction_state = _active_auction(_own(_state(), "property_baltic_avenue", P1))
    bankruptcy_state = _active_bankruptcy(_state())

    cash_obligation = _obligation(terms={"amount": 40})
    property_transfer = _obligation(
        obligation_type="property_transfer",
        terms={
            "settlement_action": "property_transfer",
            "property_id": "property_baltic_avenue",
            "from_player_id": P1,
            "to_player_id": P2,
        },
    )

    jail_decision = resolve_contract_classic_rule_interaction(
        cash_obligation,
        state=jailed_state,
        trigger_context={"type": "turn_start", "turn": 2},
    )
    auction_decision = resolve_contract_classic_rule_interaction(
        property_transfer,
        state=auction_state,
        trigger_context={"type": "property_transfer", "property_id": "property_baltic_avenue"},
    )
    bankruptcy_decision = resolve_contract_classic_rule_interaction(
        cash_obligation,
        state=bankruptcy_state,
        trigger_context={"type": "bankruptcy", "player_id": P1},
    )

    assert jail_decision.status == "settle"
    assert jail_decision.policy_key == "jail_obligation_policy"
    assert auction_decision.status == "defer"
    assert auction_decision.policy_key == "auction_obligation_policy"
    assert bankruptcy_decision.status == "settle"
    assert bankruptcy_decision.policy_key == "bankruptcy_obligation_policy"


async def _create_game(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": "phase-6-stage-6-6-contract-classic-rule-interactions",
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


async def _accepted_structured_deal(
    client: httpx.AsyncClient,
    game_id: str,
    player_ids: list[str],
) -> dict[str, Any]:
    negotiation = await client.post(
        f"/games/{game_id}/negotiations",
        json={
            "opened_by_player_id": player_ids[0],
            "participant_player_ids": player_ids,
            "context": {"topic": "stage 6.6 explanation"},
        },
    )
    assert negotiation.status_code == 201, negotiation.text
    proposal = await client.post(
        f"/games/{game_id}/deals",
        json={
            "negotiation_id": negotiation.json()["id"],
            "proposed_by_player_id": player_ids[0],
            "terms": {
                "kind": "structured_deal",
                "deal_schema_version": 1,
                "participants": player_ids,
                "terms": [
                    {
                        "kind": "deferred_cash_payment",
                        "from_player_id": player_ids[0],
                        "to_player_id": player_ids[1],
                        "amount": 45,
                        "due_turn": 1,
                    }
                ],
            },
        },
    )
    assert proposal.status_code == 201, proposal.text
    deal: dict[str, Any] = {}
    for player_id in player_ids:
        accepted = await client.post(
            f"/games/{game_id}/deals/{proposal.json()['id']}/accept",
            json={"player_id": player_id},
        )
        assert accepted.status_code == 200, accepted.text
        deal = accepted.json()
    return deal


@pytest.mark.asyncio
async def test_api_explanation_endpoint_returns_readable_structured_outcomes(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # UI and API can explain contract outcomes
    created = await _create_game(client)
    game_id = created["id"]
    player_ids = [player["id"] for player in created["players"]]
    try:
        deal = await _accepted_structured_deal(client, game_id, player_ids)
        created_contract = await client.post(
            f"/games/{game_id}/contracts/from-deal",
            json={"deal_id": deal["id"]},
        )
        assert created_contract.status_code == 201, created_contract.text
        contract = created_contract.json()["contract"]
        obligation = created_contract.json()["obligations"][0]
        settlement = await client.post(
            f"/games/{game_id}/contracts/{contract['id']}/settle",
            json={"obligation_id": obligation["id"], "trigger_context": {"type": "round", "round": 1}},
        )
        assert settlement.status_code == 200, settlement.text

        outcomes = await client.get(f"/games/{game_id}/contracts/outcomes")
        explanation = await client.get(f"/games/{game_id}/contracts/{contract['id']}/explain")

        assert outcomes.status_code == 200, outcomes.text
        assert explanation.status_code == 200, explanation.text
        outcome = outcomes.json()["outcomes"][0]
        explained = explanation.json()["outcomes"][0]
        assert outcome == explained
        assert outcome["source_deal_id"] == deal["id"]
        assert outcome["contract_id"] == contract["id"]
        assert outcome["obligation_id"] == obligation["id"]
        assert outcome["trigger"]["type"] == "round"
        assert outcome["classic_rule_interaction"]["policy"]["impossible_state_prevention"] == "strict"
        assert outcome["decision"]["status"] == "settled"
        assert outcome["resulting_state_effect"]["cash_transfers"][0]["amount"] == -45
        assert "Contract outcome explanation" in outcome["explanation_text"]
    finally:
        await _delete_game(session_factory, game_id)
