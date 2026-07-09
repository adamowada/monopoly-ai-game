from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_SMOKE_PATH = REPO_ROOT / "scripts" / "product_smoke.py"
LIVE_SMOKE_PATH = REPO_ROOT / "services" / "api" / "scripts" / "live_codex_ai_smoke.py"
LIVE_STRATEGY_SMOKE_PATH = (
    REPO_ROOT / "services" / "api" / "scripts" / "live_codex_ai_strategy_smoke.py"
)
API_DOCKERFILE_PATH = REPO_ROOT / "services" / "api" / "Dockerfile"


def test_product_smoke_script_declares_required_tier_labels() -> None:
    module = _load_product_smoke_module()

    assert set(module.SMOKE_TIERS) >= {
        "docker stack",
        "database migration",
        "API health",
        "game creation",
        "scripted turn",
        "fake AI",
    }
    assert module.LIVE_CODEX_ENV_VAR == "RUN_LIVE_CODEX_AI"


def test_root_smoke_scripts_preserve_scaffold_checks_and_add_product_smoke() -> None:
    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]

    assert "scripts/phase0_check.py smoke" in scripts["test:smoke"]
    assert "scripts/scaffold_check.py smoke" in scripts["test:smoke"]
    assert "scripts/product_smoke.py" in scripts["test:smoke"]
    assert "RUN_LIVE_CODEX_AI" in scripts["test:smoke:live"]
    assert "live_codex_ai_smoke.py" in scripts["test:smoke:live"]
    assert "RUN_LIVE_CODEX_AI" in scripts["test:smoke:live:strategy"]
    assert "live_codex_ai_strategy_smoke.py" in scripts["test:smoke:live:strategy"]


def test_api_container_runs_migrations_before_uvicorn() -> None:
    source = API_DOCKERFILE_PATH.read_text(encoding="utf-8")

    migration = "alembic -c alembic.ini upgrade head"
    server = "uvicorn app.main:app"
    assert migration in source
    assert server in source
    assert source.index(migration) < source.index(server)


def test_live_codex_smoke_stays_gated_and_uses_gpt_5_4_mini_light_exec_json() -> None:
    source = LIVE_SMOKE_PATH.read_text(encoding="utf-8")

    assert "RUN_LIVE_CODEX_AI" in source
    assert "codex exec" in source
    assert "gpt-5.4-mini" in source
    assert "model_reasoning_effort" in source
    assert "low" in source
    assert "light" not in source
    assert "--json" in source
    assert "--output-schema" in source
    assert "--disable" in source
    assert "plugin_hooks" in source
    assert "shell_snapshot" in source
    assert "robinhood-trading" in source
    assert "if process.returncode != 0:" in source
    assert "treating as pass" not in source


def test_live_codex_strategy_smoke_checks_monopoly_development_and_negotiation() -> None:
    source = LIVE_STRATEGY_SMOKE_PATH.read_text(encoding="utf-8")

    assert "RUN_LIVE_CODEX_AI" in source
    assert "codex exec" in source
    assert "gpt-5.4-mini" in source
    assert "model_reasoning_effort" in source
    assert "low" in source
    assert "railroad_purchase_with_healthy_cash" in source
    assert "boardwalk_purchase_with_healthy_cash" in source
    assert "railroad_purchase_completes_set_with_thin_cash" in source
    assert "utility_purchase_completes_set_with_thin_cash" in source
    assert "purchase_completes_color_group_with_thin_cash" in source
    assert "purchase_blocks_opponent_color_group_with_thin_cash" in source
    assert "purchase_blocks_opponent_utility_group_with_thin_cash" in source
    assert "healthy_cash_avoids_mortgage" in source
    assert "active_debt_uses_mortgage" in source
    assert "active_debt_settles_cash" in source
    assert "active_debt_sells_house_before_mortgage" in source
    assert "healthy_cash_unmortgages_rent_property" in source
    assert "jail_card_used_before_fine_or_roll" in source
    assert "auction_bid_within_valuation" in source
    assert "auction_pass_above_valuation" in source
    assert "auction_pass_to_preserve_cash_reserve" in source
    assert "auction_bid_to_complete_color_group" in source
    assert "auction_bid_to_complete_railroad_group" in source
    assert "auction_bid_to_complete_utility_group" in source
    assert "auction_bid_to_block_opponent_color_group" in source
    assert "auction_bid_to_block_opponent_railroad_group" in source
    assert "auction_bid_to_block_opponent_utility_group" in source
    assert "dark_blue_monopoly_development" in source
    assert "orange_monopoly_development" in source
    assert "multiple_monopolies_prioritizes_orange_development" in source
    assert "low_cash_defers_monopoly_development" in source
    assert "dark_blue_near_monopoly_negotiation" in source
    assert "orange_near_monopoly_negotiation" in source
    assert "railroad_near_set_negotiation" in source
    assert "utility_near_set_negotiation" in source
    assert "multiple_near_monopolies_prioritizes_orange_negotiation" in source
    assert "block_opponent_orange_near_monopoly_negotiation" in source
    assert "block_opponent_railroad_near_set_negotiation" in source
    assert "block_opponent_utility_near_set_negotiation" in source
    assert "orange_near_monopoly_deal_proposal" in source
    assert "orange_cash_limited_deal_proposal" in source
    assert "dark_blue_near_monopoly_deal_proposal" in source
    assert "railroad_near_set_deal_proposal" in source
    assert "utility_near_set_deal_proposal" in source
    assert "railroad_good_deal_acceptance" in source
    assert "utility_good_deal_acceptance" in source
    assert "railroad_bad_deal_rejection" in source
    assert "utility_bad_deal_rejection" in source
    assert "orange_bad_deal_rejection" in source
    assert "orange_good_deal_acceptance" in source
    assert "orange_cash_return_deal_acceptance" in source
    assert "orange_overpriced_deal_rejection" in source
    assert "orange_overpriced_deal_counteroffer" in source
    assert "railroad_overpriced_deal_counteroffer" in source
    assert "utility_overpriced_deal_counteroffer" in source
    assert "orange_boardwalk_swap_deal_counteroffer" in source
    assert "orange_cash_draining_deal_rejection" in source
    assert "orange_cash_draining_deal_counteroffer" in source
    assert "orange_mutual_completion_deal_rejection" in source
    assert "orange_cash_round_trip_deal_rejection" in source
    assert "orange_monopoly_breakup_deal_rejection" in source
    assert "orange_monopoly_breakup_property_swap_rejection" in source
    assert "FOURTH_PLAYER_ID" in source
    assert 'PlayerSetup(id=str(FOURTH_PLAYER_ID), name="Marie", kind="ai")' in source
    assert "BUY_HOUSE" in source
    assert "BUY_PROPERTY" in source
    assert "ROLL_DICE" in source
    assert "SETTLE_DEBT" in source
    assert "SELL_HOUSE" in source
    assert "MORTGAGE_PROPERTY" in source
    assert "UNMORTGAGE_PROPERTY" in source
    assert "USE_GET_OUT_OF_JAIL_CARD" in source
    assert "PAY_JAIL_FINE" in source
    assert "DECLARE_BANKRUPTCY" in source
    assert "property_b_and_o_railroad" in source
    assert "restore rent" in source
    assert "card_community_get_out_of_jail" in source
    assert "START_AUCTION" in source
    assert "BID_AUCTION" in source
    assert "PASS_AUCTION" in source
    assert "property_virginia_avenue" in source
    assert "above the valuation ceiling" in source
    assert "cash reserve floor" in source
    assert "buy_property_to_complete_group" in source
    assert "buy_property_to_block_opponent_group_completion" in source
    assert "property_group_completion_premium" in source
    assert "block_opponent_group_completion_premium" in source
    assert "development_priority_score" in source
    assert "marginal_rent_gain" in source
    assert "cash reserve floor" in source
    assert "property_reading_railroad" in source
    assert "property_boardwalk" in source
    assert "property_park_place" in source
    assert "property_short_line_railroad" in source
    assert "property_pennsylvania_railroad" in source
    assert "property_electric_company" in source
    assert "property_water_works" in source
    assert "open_negotiation" in source
    assert "deal_proposal" in source
    assert "immediate_cash_transfer" in source
    assert "immediate_property_transfer" in source
    assert "property_tennessee_avenue" in source
    assert "property_connecticut_avenue" in source
    assert "Linus owns property_tennessee_avenue" in source
    assert "participant_player_ids" in source
    assert "accept_reject" in source
    assert "expected reject" in source
    assert "expected accept" in source
    assert "strategic value ceiling" in source
    assert "group completion cash floor" in source
    assert "breakup cash value floor" in source
    assert "treating as pass" not in source


def test_live_codex_strategy_smoke_debt_cases_have_targeted_legal_actions() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    healthy_case = cases["healthy_cash_avoids_mortgage"]
    healthy_state = healthy_case.state_factory(healthy_case.game_id)
    healthy_pack = module.build_ai_context_pack(
        healthy_state,
        player_id=str(healthy_case.actor_player_id),
        decision_type=healthy_case.decision_type,
    )
    assert healthy_pack["action_selection_guidance"]["recommended_turn_flow_action"] == {
        "type": "ROLL_DICE",
        "payload": {},
        "reason_code": "roll_when_no_higher_priority_action",
    }

    settle_case = cases["active_debt_settles_cash"]
    settle_state = settle_case.state_factory(settle_case.game_id)
    settle_pack = module.build_ai_context_pack(
        settle_state,
        player_id=str(settle_case.actor_player_id),
        decision_type=settle_case.decision_type,
    )
    settle_action_types = {action["type"] for action in settle_pack["legal_actions"]}
    assert {"SETTLE_DEBT", "MORTGAGE_PROPERTY", "DECLARE_BANKRUPTCY"}.issubset(settle_action_types)
    assert settle_pack["action_selection_guidance"]["recommended_action_types"] == ["SETTLE_DEBT"]
    assert settle_pack["action_selection_guidance"]["debt_resolution_guidance"][
        "recommendation"
    ] == ("settle_cash_debt")
    settle_recommendation = settle_pack["action_selection_guidance"][
        "debt_resolution_guidance"
    ]["recommended_debt_action"]
    assert settle_recommendation["type"] == "SETTLE_DEBT"
    assert settle_recommendation["payload"]["amount"] == 75
    assert settle_recommendation["payload"]["creditor_player_id"] == str(module.OTHER_PLAYER_ID)
    assert settle_recommendation["payload"]["debt_id"].startswith(
        f"active-debt:{settle_case.game_id}:"
    )
    assert settle_recommendation["reason_code"] == "settle_cash_debt"

    sell_case = cases["active_debt_sells_house_before_mortgage"]
    sell_state = sell_case.state_factory(sell_case.game_id)
    sell_pack = module.build_ai_context_pack(
        sell_state,
        player_id=str(sell_case.actor_player_id),
        decision_type=sell_case.decision_type,
    )
    sell_house_actions = [
        action for action in sell_pack["legal_actions"] if action["type"] == "SELL_HOUSE"
    ]
    mortgage_actions = [
        action for action in sell_pack["legal_actions"] if action["type"] == "MORTGAGE_PROPERTY"
    ]
    assert sell_house_actions == [
        {
            "actor_id": str(module.AI_PLAYER_ID),
            "type": "SELL_HOUSE",
            "payload": {"property_id": "property_oriental_avenue", "proceeds": 25},
            "description": "Sell an improvement from Oriental Avenue.",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "property_id": {"type": "string", "const": "property_oriental_avenue"},
                    "proceeds": {"type": "integer", "const": 25},
                },
                "required": ["property_id"],
            },
            "expected_state_hash": sell_state.state_hash(),
            "expected_event_sequence": sell_state.event_sequence,
        }
    ]
    assert any(
        action["payload"]["property_id"] == "property_b_and_o_railroad"
        for action in mortgage_actions
    )
    assert sell_pack["action_selection_guidance"]["recommended_action_types"] == ["SELL_HOUSE"]
    assert sell_pack["action_selection_guidance"]["debt_resolution_guidance"]["recommendation"] == (
        "sell_improvements_before_mortgage"
    )
    assert sell_pack["action_selection_guidance"]["debt_resolution_guidance"][
        "recommended_debt_action"
    ] == {
        "type": "SELL_HOUSE",
        "payload": {"property_id": "property_oriental_avenue", "proceeds": 25},
        "reason_code": "sell_improvements_before_mortgage",
    }


def test_live_codex_strategy_smoke_utility_actions_have_targeted_legal_actions() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    unmortgage_case = cases["healthy_cash_unmortgages_rent_property"]
    unmortgage_state = unmortgage_case.state_factory(unmortgage_case.game_id)
    unmortgage_pack = module.build_ai_context_pack(
        unmortgage_state,
        player_id=str(unmortgage_case.actor_player_id),
        decision_type=unmortgage_case.decision_type,
    )
    unmortgage_guidance = unmortgage_pack["action_selection_guidance"][
        "unmortgage_guidance"
    ]
    assert unmortgage_guidance["recommended_unmortgage_action"] == {
        "type": "UNMORTGAGE_PROPERTY",
        "payload": {
            "property_id": "property_b_and_o_railroad",
            "cost": 110,
        },
        "reason_code": "restore_rent_when_cash_healthy",
    }

    jail_case = cases["jail_card_used_before_fine_or_roll"]
    jail_state = jail_case.state_factory(jail_case.game_id)
    jail_pack = module.build_ai_context_pack(
        jail_state,
        player_id=str(jail_case.actor_player_id),
        decision_type=jail_case.decision_type,
    )
    jail_guidance = jail_pack["action_selection_guidance"]["jail_guidance"]
    assert jail_guidance["recommended_jail_action"] == {
        "type": "USE_GET_OUT_OF_JAIL_CARD",
        "payload": {"card_id": "card_community_get_out_of_jail"},
        "reason_code": "use_card_before_paying_or_rolling",
    }


def test_live_codex_strategy_smoke_prioritizes_stronger_development_group() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["multiple_monopolies_prioritizes_orange_development"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    opportunities = pack["action_selection_guidance"]["development_opportunities"]
    assert [opportunity["group"] for opportunity in opportunities[:3]] == [
        "orange",
        "orange",
        "orange",
    ]
    assert opportunities[0]["property_id"] == "property_new_york_avenue"
    assert (
        opportunities[0]["development_priority_score"]
        > opportunities[-1]["development_priority_score"]
    )
    assert opportunities[0]["marginal_rent_gain"] == 64
    assert pack["action_selection_guidance"]["recommended_development_action"] == {
        "type": "BUY_HOUSE",
        "payload": {
            "property_id": "property_new_york_avenue",
            "cost": 100,
        },
        "reason_code": "highest_priority_even_monopoly_development",
        "property_id": "property_new_york_avenue",
        "property_name": "New York Avenue",
        "group": "orange",
        "group_name": "Orange",
        "development_priority_score": opportunities[0]["development_priority_score"],
        "marginal_rent_gain": 64,
    }
    assert "development_priority_score" in pack["action_selection_guidance"]["turn_guidance"][0]


def test_live_codex_strategy_smoke_defers_low_cash_development() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["low_cash_defers_monopoly_development"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]
    assert "BUY_HOUSE" in {action["type"] for action in pack["legal_actions"]}
    assert "BUY_HOUSE" not in guidance["recommended_action_types_before_roll"]
    assert guidance["recommended_development_action"] is None
    assert "BUY_HOUSE" in guidance["lower_priority_action_types"]
    assert guidance["recommended_development_opportunities"] == []
    assert len(guidance["deferred_development_opportunities"]) == 3
    assert guidance["deferred_development_opportunities"][0]["cash_after_cost"] == 250
    assert "cash reserve floor" in " ".join(guidance["turn_guidance"])


def test_live_codex_strategy_smoke_prioritizes_dark_blue_development() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["dark_blue_monopoly_development"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]
    opportunities = guidance["development_opportunities"]
    assert guidance["recommended_action_types_before_roll"] == ["BUY_HOUSE"]
    assert opportunities[0]["group"] == "dark_blue"
    assert opportunities[0]["property_id"] == "property_boardwalk"
    assert opportunities[0]["marginal_rent_gain"] == 150
    assert opportunities[0]["development_priority_score"] == 2100
    assert guidance["recommended_development_action"] == {
        "type": "BUY_HOUSE",
        "payload": {
            "property_id": "property_boardwalk",
            "cost": 200,
        },
        "reason_code": "highest_priority_even_monopoly_development",
        "property_id": "property_boardwalk",
        "property_name": "Boardwalk",
        "group": "dark_blue",
        "group_name": "Dark Blue",
        "development_priority_score": 2100,
        "marginal_rent_gain": 150,
    }


def test_live_codex_strategy_smoke_auction_blocks_opponent_group_completion() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["auction_bid_to_block_opponent_color_group"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["auction_guidance"]
    assert guidance["property_id"] == "property_virginia_avenue"
    assert guidance["valuation_basis"] == "block_opponent_group_completion_premium"
    assert guidance["strategic_valuation_ceiling"] == 240
    assert guidance["recommended_auction_action"] == {
        "type": "BID_AUCTION",
        "payload": {
            "property_id": "property_virginia_avenue",
            "amount": 161,
        },
        "reason_code": "bid_deliberate_amount_at_or_below_valuation",
    }
    assert guidance["opponent_group_completion_threats"] == [
        {
            "opponent_player_id": str(module.OTHER_PLAYER_ID),
            "opponent_owned_property_ids": [
                "property_st_charles_place",
                "property_states_avenue",
            ],
        }
    ]


def test_live_codex_strategy_smoke_auction_completes_railroad_group() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["auction_bid_to_complete_railroad_group"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["auction_guidance"]
    assert guidance["property_id"] == "property_short_line_railroad"
    assert guidance["property_group"] == "railroad"
    assert guidance["valuation_basis"] == "property_group_completion_premium"
    assert guidance["completes_property_group"] is True
    assert guidance["strategic_valuation_ceiling"] == 300
    assert guidance["valuation_ceiling"] == 300
    assert guidance["recommended_bid_amount"] == 201
    assert guidance["recommended_auction_action"] == {
        "type": "BID_AUCTION",
        "payload": {
            "property_id": "property_short_line_railroad",
            "amount": 201,
        },
        "reason_code": "bid_deliberate_amount_at_or_below_valuation",
    }
    assert guidance["same_group_owned_property_ids"] == [
        "property_reading_railroad",
        "property_pennsylvania_railroad",
        "property_b_and_o_railroad",
    ]


def test_live_codex_strategy_smoke_auction_completes_utility_group() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["auction_bid_to_complete_utility_group"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["auction_guidance"]
    assert guidance["property_id"] == "property_water_works"
    assert guidance["property_group"] == "utility"
    assert guidance["valuation_basis"] == "property_group_completion_premium"
    assert guidance["completes_property_group"] is True
    assert guidance["strategic_valuation_ceiling"] == 225
    assert guidance["valuation_ceiling"] == 225
    assert guidance["recommended_bid_amount"] == 151
    assert guidance["recommended_auction_action"] == {
        "type": "BID_AUCTION",
        "payload": {
            "property_id": "property_water_works",
            "amount": 151,
        },
        "reason_code": "bid_deliberate_amount_at_or_below_valuation",
    }
    assert guidance["same_group_owned_property_ids"] == ["property_electric_company"]


def test_live_codex_strategy_smoke_auction_blocks_opponent_railroad_group() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["auction_bid_to_block_opponent_railroad_group"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["auction_guidance"]
    assert guidance["property_id"] == "property_short_line_railroad"
    assert guidance["property_group"] == "railroad"
    assert guidance["valuation_basis"] == "block_opponent_group_completion_premium"
    assert guidance["completes_property_group"] is False
    assert guidance["strategic_valuation_ceiling"] == 300
    assert guidance["valuation_ceiling"] == 300
    assert guidance["recommended_bid_amount"] == 201
    assert guidance["recommended_auction_action"] == {
        "type": "BID_AUCTION",
        "payload": {
            "property_id": "property_short_line_railroad",
            "amount": 201,
        },
        "reason_code": "bid_deliberate_amount_at_or_below_valuation",
    }
    assert guidance["opponent_group_completion_threats"] == [
        {
            "opponent_player_id": str(module.OTHER_PLAYER_ID),
            "opponent_owned_property_ids": [
                "property_reading_railroad",
                "property_pennsylvania_railroad",
                "property_b_and_o_railroad",
            ],
        }
    ]


def test_live_codex_strategy_smoke_auction_blocks_opponent_utility_group() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["auction_bid_to_block_opponent_utility_group"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["auction_guidance"]
    assert guidance["property_id"] == "property_water_works"
    assert guidance["property_group"] == "utility"
    assert guidance["valuation_basis"] == "block_opponent_group_completion_premium"
    assert guidance["completes_property_group"] is False
    assert guidance["strategic_valuation_ceiling"] == 225
    assert guidance["valuation_ceiling"] == 225
    assert guidance["recommended_bid_amount"] == 151
    assert guidance["recommended_auction_action"] == {
        "type": "BID_AUCTION",
        "payload": {
            "property_id": "property_water_works",
            "amount": 151,
        },
        "reason_code": "bid_deliberate_amount_at_or_below_valuation",
    }
    assert guidance["opponent_group_completion_threats"] == [
        {
            "opponent_player_id": str(module.OTHER_PLAYER_ID),
            "opponent_owned_property_ids": ["property_electric_company"],
        }
    ]


def test_live_codex_strategy_smoke_purchase_blocks_opponent_group_completion() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["purchase_blocks_opponent_color_group_with_thin_cash"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["purchase_guidance"]
    assert guidance["property_id"] == "property_virginia_avenue"
    assert guidance["recommendation"] == "buy_property_to_block_opponent_group_completion"
    assert guidance["cash_after_price"] == 240
    assert guidance["recommended_purchase_action"] == {
        "type": "BUY_PROPERTY",
        "payload": {
            "property_id": "property_virginia_avenue",
            "price": 160,
        },
        "reason_code": "buy_property_to_block_opponent_group_completion",
    }
    assert guidance["opponent_group_completion_threats"] == [
        {
            "opponent_player_id": str(module.OTHER_PLAYER_ID),
            "opponent_owned_property_ids": [
                "property_st_charles_place",
                "property_states_avenue",
            ],
        }
    ]


def test_live_codex_strategy_smoke_purchase_blocks_opponent_utility_group_completion() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["purchase_blocks_opponent_utility_group_with_thin_cash"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["purchase_guidance"]
    assert guidance["property_id"] == "property_water_works"
    assert guidance["property_kind"] == "utility"
    assert guidance["recommendation"] == "buy_property_to_block_opponent_group_completion"
    assert guidance["cash_after_price"] == 150
    assert guidance["recommended_purchase_action"] == {
        "type": "BUY_PROPERTY",
        "payload": {
            "property_id": "property_water_works",
            "price": 150,
        },
        "reason_code": "buy_property_to_block_opponent_group_completion",
    }
    assert guidance["opponent_group_completion_threats"] == [
        {
            "opponent_player_id": str(module.OTHER_PLAYER_ID),
            "opponent_owned_property_ids": ["property_electric_company"],
        }
    ]


def test_live_codex_strategy_smoke_buys_boardwalk_with_healthy_cash() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["boardwalk_purchase_with_healthy_cash"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]
    assert guidance["recommended_action_types"] == ["BUY_PROPERTY"]
    assert "START_AUCTION" in guidance["lower_priority_action_types"]
    assert guidance["purchase_guidance"]["property_id"] == "property_boardwalk"
    assert guidance["purchase_guidance"]["property_name"] == "Boardwalk"
    assert guidance["purchase_guidance"]["recommendation"] == "buy_property_at_list_price"
    assert guidance["purchase_guidance"]["cash_after_price"] == 900
    assert guidance["purchase_guidance"]["recommended_purchase_action"] == {
        "type": "BUY_PROPERTY",
        "payload": {
            "property_id": "property_boardwalk",
            "price": 400,
        },
        "reason_code": "buy_property_at_list_price",
    }


def test_live_codex_strategy_smoke_purchase_completes_railroad_set() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["railroad_purchase_completes_set_with_thin_cash"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["purchase_guidance"]
    assert guidance["property_id"] == "property_short_line_railroad"
    assert guidance["property_kind"] == "railroad"
    assert guidance["recommendation"] == "buy_property_to_complete_group"
    assert guidance["cash_after_price"] == 200
    assert guidance["completes_property_group"] is True
    assert guidance["recommended_purchase_action"] == {
        "type": "BUY_PROPERTY",
        "payload": {
            "property_id": "property_short_line_railroad",
            "price": 200,
        },
        "reason_code": "buy_property_to_complete_group",
    }
    assert guidance["same_group_owned_property_ids"] == [
        "property_b_and_o_railroad",
        "property_pennsylvania_railroad",
        "property_reading_railroad",
    ]


def test_live_codex_strategy_smoke_purchase_completes_utility_set() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["utility_purchase_completes_set_with_thin_cash"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["action_selection_guidance"]["purchase_guidance"]
    assert guidance["property_id"] == "property_water_works"
    assert guidance["property_kind"] == "utility"
    assert guidance["recommendation"] == "buy_property_to_complete_group"
    assert guidance["cash_after_price"] == 150
    assert guidance["completes_property_group"] is True
    assert guidance["recommended_purchase_action"] == {
        "type": "BUY_PROPERTY",
        "payload": {
            "property_id": "property_water_works",
            "price": 150,
        },
        "reason_code": "buy_property_to_complete_group",
    }
    assert guidance["same_group_owned_property_ids"] == ["property_electric_company"]


def test_live_codex_strategy_smoke_blocks_opponent_near_monopoly() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["block_opponent_orange_near_monopoly_negotiation"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["negotiation_strategy_guidance"]
    assert guidance["recommended_decision_types"] == ["open_negotiation"]
    assert guidance["open_negotiation_payload_template"]["participant_player_ids"] == [
        str(module.AI_PLAYER_ID),
        str(module.THIRD_PLAYER_ID),
    ]
    context = guidance["open_negotiation_payload_template"]["context"]
    assert context["target_property_id"] == "property_tennessee_avenue"
    assert context["target_owner_id"] == str(module.THIRD_PLAYER_ID)
    assert context["opponent_player_id"] == str(module.OTHER_PLAYER_ID)
    assert guidance["trade_opportunities"][0]["kind"] == "block_opponent_street_group"


def test_live_codex_strategy_smoke_blocks_opponent_railroad_set() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["block_opponent_railroad_near_set_negotiation"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["negotiation_strategy_guidance"]
    assert guidance["recommended_decision_types"] == ["open_negotiation"]
    assert guidance["open_negotiation_payload_template"]["participant_player_ids"] == [
        str(module.AI_PLAYER_ID),
        str(module.THIRD_PLAYER_ID),
    ]
    context = guidance["open_negotiation_payload_template"]["context"]
    assert context["topic"] == "Trade for Short Line Railroad to block Ada's Railroads monopoly"
    assert context["target_property_id"] == "property_short_line_railroad"
    assert context["target_owner_id"] == str(module.THIRD_PLAYER_ID)
    assert context["opponent_player_id"] == str(module.OTHER_PLAYER_ID)
    assert context["suggested_offer"] == {
        "cash_budget_floor": 200,
        "cash_budget_ceiling": 300,
        "do_not_trade_target_to_opponent_player_id": str(module.OTHER_PLAYER_ID),
    }
    assert guidance["trade_opportunities"][0]["kind"] == "block_opponent_railroad_group"
    assert guidance["trade_opportunities"][0]["property_group_kind"] == "railroad"


def test_live_codex_strategy_smoke_blocks_opponent_utility_set() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["block_opponent_utility_near_set_negotiation"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["negotiation_strategy_guidance"]
    assert guidance["recommended_decision_types"] == ["open_negotiation"]
    assert guidance["open_negotiation_payload_template"]["participant_player_ids"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    context = guidance["open_negotiation_payload_template"]["context"]
    assert context["topic"] == "Trade for Electric Company to block Linus's Utilities monopoly"
    assert context["target_property_id"] == "property_electric_company"
    assert context["target_owner_id"] == str(module.OTHER_PLAYER_ID)
    assert context["opponent_player_id"] == str(module.THIRD_PLAYER_ID)
    assert context["suggested_offer"] == {
        "cash_budget_floor": 150,
        "cash_budget_ceiling": 225,
        "do_not_trade_target_to_opponent_player_id": str(module.THIRD_PLAYER_ID),
    }
    assert guidance["trade_opportunities"][0]["kind"] == "block_opponent_utility_group"
    assert guidance["trade_opportunities"][0]["property_group_kind"] == "utility"


def test_live_codex_strategy_smoke_negotiates_for_railroad_set_completion() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["railroad_near_set_negotiation"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["negotiation_strategy_guidance"]
    assert guidance["recommended_decision_types"] == ["open_negotiation"]
    assert guidance["open_negotiation_payload_template"]["participant_player_ids"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    context = guidance["open_negotiation_payload_template"]["context"]
    assert context["target_property_id"] == "property_short_line_railroad"
    assert context["target_owner_id"] == str(module.OTHER_PLAYER_ID)
    assert guidance["trade_opportunities"][0]["kind"] == "complete_railroad_group"
    assert guidance["trade_opportunities"][0]["property_group_kind"] == "railroad"


def test_live_codex_strategy_smoke_negotiates_for_utility_set_completion() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["utility_near_set_negotiation"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["negotiation_strategy_guidance"]
    assert guidance["recommended_decision_types"] == ["open_negotiation"]
    assert guidance["open_negotiation_payload_template"]["participant_player_ids"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    context = guidance["open_negotiation_payload_template"]["context"]
    assert context["topic"] == "Trade for Water Works to complete Utilities"
    assert context["target_property_id"] == "property_water_works"
    assert context["target_owner_id"] == str(module.OTHER_PLAYER_ID)
    assert context["suggested_offer"] == {
        "cash_budget_floor": 150,
        "cash_budget_ceiling": 225,
        "avoid_trading_away_group_property_ids": ["property_electric_company"],
    }
    assert guidance["trade_opportunities"][0]["kind"] == "complete_utility_group"
    assert guidance["trade_opportunities"][0]["property_group_kind"] == "utility"


def test_live_codex_strategy_smoke_negotiates_for_dark_blue_completion() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["dark_blue_near_monopoly_negotiation"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["negotiation_strategy_guidance"]
    assert guidance["recommended_decision_types"] == ["open_negotiation"]
    assert guidance["open_negotiation_payload_template"]["participant_player_ids"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    context = guidance["open_negotiation_payload_template"]["context"]
    assert context["topic"] == "Trade for Park Place to complete Dark Blue"
    assert context["target_property_id"] == "property_park_place"
    assert context["target_owner_id"] == str(module.OTHER_PLAYER_ID)
    assert context["suggested_offer"] == {
        "cash_budget_floor": 350,
        "cash_budget_ceiling": 525,
        "avoid_trading_away_group_property_ids": ["property_boardwalk"],
    }
    assert guidance["trade_opportunities"][0]["kind"] == "complete_street_group"
    assert guidance["trade_opportunities"][0]["group"] == "dark_blue"


def test_live_codex_strategy_smoke_bad_deal_has_context_pack_rejection_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_bad_deal_rejection"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {str(module.BAD_DEAL_ID): "reject"}
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"] == {
        "deal_id": str(module.BAD_DEAL_ID),
        "decision": "reject",
        "message": "I reject because this offer undervalues set leverage.",
    }
    assert guidance["deal_evaluations"][0]["risk"]["property_id"] == "property_tennessee_avenue"
    assert guidance["deal_evaluations"][0]["risk"]["minimum_cash_value_floor"] == 270
    assert guidance["deal_evaluations"][0]["risk"]["cash_value_gap"] == 269


def test_live_codex_strategy_smoke_non_street_lowball_deals_have_rejection_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    railroad_case = cases["railroad_bad_deal_rejection"]
    railroad_state = railroad_case.state_factory(railroad_case.game_id)
    railroad_pack = module.build_ai_context_pack(
        railroad_state,
        player_id=str(railroad_case.actor_player_id),
        decision_type=railroad_case.decision_type,
        negotiations=module._negotiations(railroad_case),
        negotiation_messages=module._negotiation_messages(railroad_case),
        deals=module._deals(railroad_case),
        rule_snippets=module._strategy_rule_snippets(railroad_case),
    )

    railroad_guidance = railroad_pack["deal_evaluation_guidance"]
    assert railroad_guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.LOWBALL_RAILROAD_DEAL_ID): "reject"
    }
    railroad_evaluation = railroad_guidance["deal_evaluations"][0]
    assert railroad_evaluation["reason_code"] == (
        "transfers_property_that_completes_opponent_railroad_group_below_floor"
    )
    railroad_risk = railroad_evaluation["risk"]
    assert railroad_risk["kind"] == "opponent_railroad_group_completion"
    assert railroad_risk["property_group_kind"] == "railroad"
    assert railroad_risk["property_id"] == "property_short_line_railroad"
    assert railroad_risk["minimum_cash_value_floor"] == 300
    assert railroad_risk["cash_value_gap"] == 299

    utility_case = cases["utility_bad_deal_rejection"]
    utility_state = utility_case.state_factory(utility_case.game_id)
    utility_pack = module.build_ai_context_pack(
        utility_state,
        player_id=str(utility_case.actor_player_id),
        decision_type=utility_case.decision_type,
        negotiations=module._negotiations(utility_case),
        negotiation_messages=module._negotiation_messages(utility_case),
        deals=module._deals(utility_case),
        rule_snippets=module._strategy_rule_snippets(utility_case),
    )

    utility_guidance = utility_pack["deal_evaluation_guidance"]
    assert utility_guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.LOWBALL_UTILITY_DEAL_ID): "reject"
    }
    utility_evaluation = utility_guidance["deal_evaluations"][0]
    assert utility_evaluation["reason_code"] == (
        "transfers_property_that_completes_opponent_utility_group_below_floor"
    )
    utility_risk = utility_evaluation["risk"]
    assert utility_risk["kind"] == "opponent_utility_group_completion"
    assert utility_risk["property_group_kind"] == "utility"
    assert utility_risk["property_id"] == "property_water_works"
    assert utility_risk["minimum_cash_value_floor"] == 225
    assert utility_risk["cash_value_gap"] == 224


def test_live_codex_strategy_smoke_deal_proposal_uses_context_pack_template() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_near_monopoly_deal_proposal"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_proposal_guidance"]
    assert guidance["recommended_decision_types"] == ["deal_proposal"]
    template = guidance["proposal_templates"][0]
    assert template["target_property_id"] == "property_tennessee_avenue"
    assert template["recommended_cash_offer"] == 225
    deal_payload = template["deal_payload_template"]
    assert deal_payload["recipient_player_ids"] == [str(module.OTHER_PLAYER_ID)]
    assert deal_payload["terms"]["participants"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    assert [
        term["kind"] for term in deal_payload["terms"]["terms"]
    ] == [
        "immediate_cash_transfer",
        "immediate_property_transfer",
    ]


def test_live_codex_strategy_smoke_cash_limited_deal_proposal_clamps_offer() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_cash_limited_deal_proposal"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_proposal_guidance"]
    assert guidance["recommended_decision_types"] == ["deal_proposal"]
    template = guidance["proposal_templates"][0]
    assert template["target_property_id"] == "property_tennessee_avenue"
    assert template["cash_available"] == 500
    assert template["cash_budget_ceiling"] == 270
    assert template["current_cash_budget_ceiling"] == 200
    assert template["recommended_cash_offer"] == 190
    deal_payload = template["deal_payload_template"]
    assert deal_payload["message"] == "I can offer $190 for Tennessee Avenue to complete Orange."
    assert deal_payload["terms"]["participants"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    assert deal_payload["terms"]["terms"] == [
        {
            "kind": "immediate_cash_transfer",
            "from_player_id": str(module.AI_PLAYER_ID),
            "to_player_id": str(module.OTHER_PLAYER_ID),
            "amount": 190,
        },
        {
            "kind": "immediate_property_transfer",
            "from_player_id": str(module.OTHER_PLAYER_ID),
            "to_player_id": str(module.AI_PLAYER_ID),
            "property_id": "property_tennessee_avenue",
        },
    ]


def test_live_codex_strategy_smoke_cash_starved_deal_proposal_defers_offer() -> None:
    module = _load_live_strategy_smoke_module()

    state = module._cash_starved_orange_near_monopoly_state(  # noqa: SLF001
        module.UUID("00000000-0000-0000-0000-00000000b246")
    )
    case = next(
        strategy_case
        for strategy_case in module._strategy_cases()
        if strategy_case.name == "orange_near_monopoly_deal_proposal"
    )
    pack = module.build_ai_context_pack(
        state,
        player_id=str(module.AI_PLAYER_ID),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_proposal_guidance"]
    assert guidance["recommended_decision_types"] == []
    assert guidance["proposal_templates"] == []
    assert guidance["deferred_proposal_opportunities"] == [
        {
            "negotiation_id": str(module.NEGOTIATION_ID),
            "target_property_id": "property_tennessee_avenue",
            "target_property_name": "Tennessee Avenue",
            "target_owner_id": str(module.OTHER_PLAYER_ID),
            "cash_budget_floor": 180,
            "cash_budget_ceiling": 270,
            "current_cash_budget_ceiling": 150,
            "cash_available": 450,
            "healthy_cash_floor": 300,
            "reason": "Current cash above reserve cannot support the saved offer floor.",
        }
    ]
    assert "Wait on deal_proposal" in guidance["guidance"][0]


def test_live_codex_strategy_smoke_dark_blue_deal_proposal_uses_context_pack_template() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["dark_blue_near_monopoly_deal_proposal"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_proposal_guidance"]
    assert guidance["recommended_decision_types"] == ["deal_proposal"]
    template = guidance["proposal_templates"][0]
    assert template["target_property_id"] == "property_park_place"
    assert template["target_property_name"] == "Park Place"
    assert template["cash_budget_floor"] == 350
    assert template["cash_budget_ceiling"] == 525
    assert template["recommended_cash_offer"] == 437
    assert template["avoid_trading_away_group_property_ids"] == ["property_boardwalk"]
    deal_payload = template["deal_payload_template"]
    assert deal_payload["recipient_player_ids"] == [str(module.OTHER_PLAYER_ID)]
    assert deal_payload["terms"]["participants"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    assert deal_payload["terms"]["terms"] == [
        {
            "kind": "immediate_cash_transfer",
            "from_player_id": str(module.AI_PLAYER_ID),
            "to_player_id": str(module.OTHER_PLAYER_ID),
            "amount": 437,
        },
        {
            "kind": "immediate_property_transfer",
            "from_player_id": str(module.OTHER_PLAYER_ID),
            "to_player_id": str(module.AI_PLAYER_ID),
            "property_id": "property_park_place",
        },
    ]


def test_live_codex_strategy_smoke_utility_deal_proposal_uses_context_pack_template() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["utility_near_set_deal_proposal"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_proposal_guidance"]
    assert guidance["recommended_decision_types"] == ["deal_proposal"]
    template = guidance["proposal_templates"][0]
    assert template["target_property_id"] == "property_water_works"
    assert template["target_property_name"] == "Water Works"
    assert template["cash_budget_floor"] == 150
    assert template["cash_budget_ceiling"] == 225
    assert template["recommended_cash_offer"] == 187
    assert template["avoid_trading_away_group_property_ids"] == [
        "property_electric_company"
    ]
    deal_payload = template["deal_payload_template"]
    assert deal_payload["recipient_player_ids"] == [str(module.OTHER_PLAYER_ID)]
    assert deal_payload["terms"]["participants"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    assert deal_payload["terms"]["terms"] == [
        {
            "kind": "immediate_cash_transfer",
            "from_player_id": str(module.AI_PLAYER_ID),
            "to_player_id": str(module.OTHER_PLAYER_ID),
            "amount": 187,
        },
        {
            "kind": "immediate_property_transfer",
            "from_player_id": str(module.OTHER_PLAYER_ID),
            "to_player_id": str(module.AI_PLAYER_ID),
            "property_id": "property_water_works",
        },
    ]


def test_live_codex_strategy_smoke_railroad_deal_proposal_uses_context_pack_template() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["railroad_near_set_deal_proposal"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_proposal_guidance"]
    assert guidance["recommended_decision_types"] == ["deal_proposal"]
    template = guidance["proposal_templates"][0]
    assert template["target_property_id"] == "property_short_line_railroad"
    assert template["target_property_name"] == "Short Line Railroad"
    assert template["cash_budget_floor"] == 200
    assert template["cash_budget_ceiling"] == 300
    assert template["recommended_cash_offer"] == 250
    assert template["avoid_trading_away_group_property_ids"] == [
        "property_reading_railroad",
        "property_pennsylvania_railroad",
        "property_b_and_o_railroad",
    ]
    deal_payload = template["deal_payload_template"]
    assert deal_payload["recipient_player_ids"] == [str(module.OTHER_PLAYER_ID)]
    assert deal_payload["terms"]["participants"] == [
        str(module.AI_PLAYER_ID),
        str(module.OTHER_PLAYER_ID),
    ]
    assert deal_payload["terms"]["terms"] == [
        {
            "kind": "immediate_cash_transfer",
            "from_player_id": str(module.AI_PLAYER_ID),
            "to_player_id": str(module.OTHER_PLAYER_ID),
            "amount": 250,
        },
        {
            "kind": "immediate_property_transfer",
            "from_player_id": str(module.OTHER_PLAYER_ID),
            "to_player_id": str(module.AI_PLAYER_ID),
            "property_id": "property_short_line_railroad",
        },
    ]


def test_live_codex_strategy_smoke_good_deal_has_context_pack_acceptance_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_good_deal_acceptance"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {str(module.GOOD_DEAL_ID): "accept"}
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"] == {
        "deal_id": str(module.GOOD_DEAL_ID),
        "decision": "accept",
        "message": "I accept because this deal completes my set within value and liquidity limits.",
    }
    assert guidance["deal_evaluations"][0]["opportunity"]["property_id"] == (
        "property_tennessee_avenue"
    )
    assert guidance["deal_evaluations"][0]["opportunity"]["maximum_cash_value_ceiling"] == 270
    assert guidance["deal_evaluations"][0]["opportunity"]["cash_after_payment"] == 1280


def test_live_codex_strategy_smoke_cash_return_deal_accepts_fair_net_price() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_cash_return_deal_acceptance"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.CASH_RETURN_DEAL_ID): "accept"
    }
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"] == {
        "deal_id": str(module.CASH_RETURN_DEAL_ID),
        "decision": "accept",
        "message": "I accept because this deal completes my set within value and liquidity limits.",
    }
    evaluation = guidance["deal_evaluations"][0]
    assert evaluation["recommendation"] == "accept"
    assert evaluation["reason_code"] == (
        "receives_property_that_completes_actor_street_group_with_affordable_cash"
    )
    assert evaluation["actor_receives_cash_total"] == 200
    assert evaluation["actor_pays_cash_total"] == 400
    opportunity = evaluation["opportunity"]
    assert opportunity["property_id"] == "property_tennessee_avenue"
    assert opportunity["maximum_cash_value_ceiling"] == 270
    assert opportunity["net_cash_payment"] == 200
    assert opportunity["cash_after_net_payment"] == 300
    assert opportunity["cash_after_payment"] == 100


def test_live_codex_strategy_smoke_fair_railroad_deal_has_acceptance_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["railroad_good_deal_acceptance"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.FAIR_RAILROAD_DEAL_ID): "accept"
    }
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"] == {
        "deal_id": str(module.FAIR_RAILROAD_DEAL_ID),
        "decision": "accept",
        "message": "I accept because this deal completes my set within value and liquidity limits.",
    }
    evaluation = guidance["deal_evaluations"][0]
    assert evaluation["reason_code"] == (
        "receives_property_that_completes_actor_railroad_group_with_affordable_cash"
    )
    opportunity = evaluation["opportunity"]
    assert opportunity["kind"] == "actor_railroad_group_completion"
    assert opportunity["property_group_kind"] == "railroad"
    assert opportunity["property_id"] == "property_short_line_railroad"
    assert opportunity["maximum_cash_value_ceiling"] == 300
    assert opportunity["cash_after_payment"] == 1250


def test_live_codex_strategy_smoke_fair_utility_deal_has_acceptance_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["utility_good_deal_acceptance"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.FAIR_UTILITY_DEAL_ID): "accept"
    }
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"] == {
        "deal_id": str(module.FAIR_UTILITY_DEAL_ID),
        "decision": "accept",
        "message": "I accept because this deal completes my set within value and liquidity limits.",
    }
    evaluation = guidance["deal_evaluations"][0]
    assert evaluation["reason_code"] == (
        "receives_property_that_completes_actor_utility_group_with_affordable_cash"
    )
    opportunity = evaluation["opportunity"]
    assert opportunity["kind"] == "actor_utility_group_completion"
    assert opportunity["property_group_kind"] == "utility"
    assert opportunity["property_id"] == "property_water_works"
    assert opportunity["maximum_cash_value_ceiling"] == 225
    assert opportunity["cash_after_payment"] == 1313


def test_live_codex_strategy_smoke_cash_round_trip_deal_rejects_low_net_price() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_cash_round_trip_deal_rejection"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.CASH_ROUND_TRIP_DEAL_ID): "reject"
    }
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"] == {
        "deal_id": str(module.CASH_ROUND_TRIP_DEAL_ID),
        "decision": "reject",
        "message": "I reject because this offer undervalues set leverage.",
    }
    evaluation = guidance["deal_evaluations"][0]
    assert evaluation["recommendation"] == "reject"
    assert evaluation["reason_code"] == (
        "transfers_property_that_completes_opponent_street_group_below_floor"
    )
    assert evaluation["actor_receives_cash_total"] == 320
    assert evaluation["actor_pays_cash_total"] == 200
    risk = evaluation["risk"]
    assert risk["property_id"] == "property_tennessee_avenue"
    assert risk["minimum_cash_value_floor"] == 270
    assert risk["total_compensation_value"] == 320
    assert risk["net_compensation_value"] == 120
    assert risk["net_compensation_value_gap"] == 150


def test_live_codex_strategy_smoke_bad_completion_deals_have_rejection_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    overpriced_case = cases["orange_overpriced_deal_rejection"]
    overpriced_state = overpriced_case.state_factory(overpriced_case.game_id)
    overpriced_pack = module.build_ai_context_pack(
        overpriced_state,
        player_id=str(overpriced_case.actor_player_id),
        decision_type=overpriced_case.decision_type,
        negotiations=module._negotiations(overpriced_case),
        negotiation_messages=module._negotiation_messages(overpriced_case),
        deals=module._deals(overpriced_case),
        rule_snippets=module._strategy_rule_snippets(overpriced_case),
    )
    overpriced_guidance = overpriced_pack["deal_evaluation_guidance"]
    assert overpriced_guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.OVERPRICED_DEAL_ID): "reject"
    }
    assert overpriced_guidance["recommended_accept_reject_actions"][0][
        "accept_reject_payload_template"
    ]["decision"] == "reject"
    assert overpriced_guidance["deal_evaluations"][0]["reason_code"] == (
        "receives_property_that_completes_actor_street_group_above_value_ceiling"
    )
    assert (
        overpriced_guidance["deal_evaluations"][0]["opportunity"]["cash_over_value_ceiling"] == 130
    )

    draining_case = cases["orange_cash_draining_deal_rejection"]
    draining_state = draining_case.state_factory(draining_case.game_id)
    draining_pack = module.build_ai_context_pack(
        draining_state,
        player_id=str(draining_case.actor_player_id),
        decision_type=draining_case.decision_type,
        negotiations=module._negotiations(draining_case),
        negotiation_messages=module._negotiation_messages(draining_case),
        deals=module._deals(draining_case),
        rule_snippets=module._strategy_rule_snippets(draining_case),
    )
    draining_guidance = draining_pack["deal_evaluation_guidance"]
    assert draining_guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.CASH_DRAINING_DEAL_ID): "reject"
    }
    assert draining_guidance["recommended_accept_reject_actions"][0][
        "accept_reject_payload_template"
    ]["decision"] == "reject"
    assert draining_guidance["deal_evaluations"][0]["reason_code"] == (
        "receives_property_that_completes_actor_street_group_below_cash_floor"
    )
    assert draining_guidance["deal_evaluations"][0]["opportunity"]["cash_floor_gap"] == 20


def test_live_codex_strategy_smoke_counteroffer_has_context_pack_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_overpriced_deal_counteroffer"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["counteroffer_guidance"]
    assert guidance["recommended_decision_types"] == ["counteroffer"]
    template = guidance["counteroffer_templates"][0]
    assert template["responds_to_deal_id"] == str(module.OVERPRICED_DEAL_ID)
    assert template["negotiation_id"] == str(module.NEGOTIATION_ID)
    assert template["target_property_id"] == "property_tennessee_avenue"
    assert template["recommended_cash_amount"] == 270
    counteroffer_payload = template["counteroffer_payload_template"]
    assert counteroffer_payload["responds_to_deal_id"] == str(module.OVERPRICED_DEAL_ID)
    assert [
        term["kind"] for term in counteroffer_payload["terms"]["terms"]
    ] == [
        "immediate_cash_transfer",
        "immediate_property_transfer",
    ]

    cash_draining_case = cases["orange_cash_draining_deal_counteroffer"]
    cash_draining_state = cash_draining_case.state_factory(cash_draining_case.game_id)
    cash_draining_pack = module.build_ai_context_pack(
        cash_draining_state,
        player_id=str(cash_draining_case.actor_player_id),
        decision_type=cash_draining_case.decision_type,
        negotiations=module._negotiations(cash_draining_case),
        negotiation_messages=module._negotiation_messages(cash_draining_case),
        deals=module._deals(cash_draining_case),
        rule_snippets=module._strategy_rule_snippets(cash_draining_case),
    )

    cash_draining_guidance = cash_draining_pack["counteroffer_guidance"]
    assert cash_draining_guidance["recommended_decision_types"] == ["counteroffer"]
    cash_draining_template = cash_draining_guidance["counteroffer_templates"][0]
    assert cash_draining_template["responds_to_deal_id"] == str(module.CASH_DRAINING_DEAL_ID)
    assert cash_draining_template["negotiation_id"] == str(module.NEGOTIATION_ID)
    assert cash_draining_template["target_property_id"] == "property_tennessee_avenue"
    assert cash_draining_template["recommended_cash_amount"] == 200


def test_live_codex_strategy_smoke_non_street_counteroffers_have_context_pack_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    railroad_case = cases["railroad_overpriced_deal_counteroffer"]
    railroad_state = railroad_case.state_factory(railroad_case.game_id)
    railroad_pack = module.build_ai_context_pack(
        railroad_state,
        player_id=str(railroad_case.actor_player_id),
        decision_type=railroad_case.decision_type,
        negotiations=module._negotiations(railroad_case),
        negotiation_messages=module._negotiation_messages(railroad_case),
        deals=module._deals(railroad_case),
        rule_snippets=module._strategy_rule_snippets(railroad_case),
    )

    railroad_guidance = railroad_pack["counteroffer_guidance"]
    assert railroad_guidance["recommended_decision_types"] == ["counteroffer"]
    railroad_template = railroad_guidance["counteroffer_templates"][0]
    railroad_payload = railroad_template["counteroffer_payload_template"]
    assert railroad_template["responds_to_deal_id"] == str(
        module.OVERPRICED_RAILROAD_DEAL_ID
    )
    assert railroad_template["reason_code"] == (
        "receives_property_that_completes_actor_railroad_group_above_value_ceiling"
    )
    assert railroad_template["target_property_id"] == "property_short_line_railroad"
    assert railroad_template["recommended_cash_amount"] == 300
    assert railroad_template["maximum_cash_value_ceiling"] == 300
    assert railroad_payload["terms"]["terms"] == [
        {
            "kind": "immediate_cash_transfer",
            "from_player_id": str(module.AI_PLAYER_ID),
            "to_player_id": str(module.OTHER_PLAYER_ID),
            "amount": 300,
        },
        {
            "kind": "immediate_property_transfer",
            "from_player_id": str(module.OTHER_PLAYER_ID),
            "to_player_id": str(module.AI_PLAYER_ID),
            "property_id": "property_short_line_railroad",
        },
    ]

    utility_case = cases["utility_overpriced_deal_counteroffer"]
    utility_state = utility_case.state_factory(utility_case.game_id)
    utility_pack = module.build_ai_context_pack(
        utility_state,
        player_id=str(utility_case.actor_player_id),
        decision_type=utility_case.decision_type,
        negotiations=module._negotiations(utility_case),
        negotiation_messages=module._negotiation_messages(utility_case),
        deals=module._deals(utility_case),
        rule_snippets=module._strategy_rule_snippets(utility_case),
    )

    utility_guidance = utility_pack["counteroffer_guidance"]
    assert utility_guidance["recommended_decision_types"] == ["counteroffer"]
    utility_template = utility_guidance["counteroffer_templates"][0]
    utility_payload = utility_template["counteroffer_payload_template"]
    assert utility_template["responds_to_deal_id"] == str(
        module.OVERPRICED_UTILITY_DEAL_ID
    )
    assert utility_template["reason_code"] == (
        "receives_property_that_completes_actor_utility_group_above_value_ceiling"
    )
    assert utility_template["target_property_id"] == "property_water_works"
    assert utility_template["recommended_cash_amount"] == 225
    assert utility_template["maximum_cash_value_ceiling"] == 225
    assert utility_payload["terms"]["terms"] == [
        {
            "kind": "immediate_cash_transfer",
            "from_player_id": str(module.AI_PLAYER_ID),
            "to_player_id": str(module.OTHER_PLAYER_ID),
            "amount": 225,
        },
        {
            "kind": "immediate_property_transfer",
            "from_player_id": str(module.OTHER_PLAYER_ID),
            "to_player_id": str(module.AI_PLAYER_ID),
            "property_id": "property_water_works",
        },
    ]


def test_live_codex_strategy_smoke_property_swap_counteroffer_keeps_premium_property() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_boardwalk_swap_deal_counteroffer"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["counteroffer_guidance"]
    assert guidance["recommended_decision_types"] == ["counteroffer"]
    template = guidance["counteroffer_templates"][0]
    payload = template["counteroffer_payload_template"]
    assert template["responds_to_deal_id"] == str(module.BOARDWALK_SWAP_DEAL_ID)
    assert template["reason_code"] == (
        "receives_property_that_completes_actor_street_group_above_value_ceiling"
    )
    assert template["target_property_id"] == "property_tennessee_avenue"
    assert template["current_payment_value"] == 400
    assert template["current_cash_amount"] == 0
    assert template["recommended_cash_amount"] == 270
    assert payload["terms"]["terms"] == [
        {
            "kind": "immediate_cash_transfer",
            "from_player_id": str(module.AI_PLAYER_ID),
            "to_player_id": str(module.OTHER_PLAYER_ID),
            "amount": 270,
        },
        {
            "kind": "immediate_property_transfer",
            "from_player_id": str(module.OTHER_PLAYER_ID),
            "to_player_id": str(module.AI_PLAYER_ID),
            "property_id": "property_tennessee_avenue",
        },
    ]
    assert "property_boardwalk" not in json.dumps(payload["terms"])


def test_live_codex_strategy_smoke_monopoly_breakup_deal_has_rejection_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_monopoly_breakup_deal_rejection"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.BREAKUP_DEAL_ID): "reject"
    }
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"][
        "decision"
    ] == "reject"
    assert guidance["deal_evaluations"][0]["reason_code"] == (
        "transfers_property_that_breaks_actor_complete_street_group_below_floor"
    )
    assert guidance["deal_evaluations"][0]["risk"]["kind"] == "actor_street_group_breakup"
    assert guidance["deal_evaluations"][0]["risk"]["minimum_cash_value_floor"] == 540
    assert guidance["deal_evaluations"][0]["risk"]["cash_value_gap"] == 240


def test_live_codex_strategy_smoke_monopoly_breakup_property_swap_has_rejection_guidance() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_monopoly_breakup_property_swap_rejection"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.BREAKUP_PROPERTY_SWAP_DEAL_ID): "reject"
    }
    assert guidance["recommended_accept_reject_actions"][0]["accept_reject_payload_template"] == {
        "deal_id": str(module.BREAKUP_PROPERTY_SWAP_DEAL_ID),
        "decision": "reject",
        "message": "I reject because this would break up my complete set below fair value.",
    }
    evaluation = guidance["deal_evaluations"][0]
    assert evaluation["reason_code"] == (
        "transfers_property_that_breaks_actor_complete_street_group_below_floor"
    )
    risk = evaluation["risk"]
    assert risk["kind"] == "actor_street_group_breakup"
    assert risk["property_id"] == "property_tennessee_avenue"
    assert risk["minimum_cash_value_floor"] == 540
    assert risk["actor_receives_property_value_total"] == 60
    assert risk["total_compensation_value"] == 60
    assert risk["compensation_value_gap"] == 480


def test_live_codex_strategy_smoke_mutual_completion_deal_rejects_stronger_opponent_set() -> None:
    module = _load_live_strategy_smoke_module()
    cases = {case.name: case for case in module._strategy_cases()}

    case = cases["orange_mutual_completion_deal_rejection"]
    state = case.state_factory(case.game_id)
    pack = module.build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        negotiations=module._negotiations(case),
        negotiation_messages=module._negotiation_messages(case),
        deals=module._deals(case),
        rule_snippets=module._strategy_rule_snippets(case),
    )

    guidance = pack["deal_evaluation_guidance"]
    assert guidance["recommended_accept_reject_by_deal_id"] == {
        str(module.MUTUAL_COMPLETION_DEAL_ID): "reject"
    }
    action = guidance["recommended_accept_reject_actions"][0]
    assert action["accept_reject_payload_template"] == {
        "deal_id": str(module.MUTUAL_COMPLETION_DEAL_ID),
        "decision": "reject",
        "message": "I reject because this trade gives you the stronger completed set.",
    }
    evaluation = guidance["deal_evaluations"][0]
    assert evaluation["reason_code"] == (
        "transfers_property_that_completes_opponent_street_group_for_weaker_actor_completion"
    )
    risk = evaluation["risk"]
    assert risk["property_id"] == "property_tennessee_avenue"
    assert risk["group"] == "orange"
    assert risk["actor_completion_group"] == "light_blue"
    assert risk["opponent_completion_priority_score"] == 1400
    assert risk["actor_completion_priority_score"] == 690
    assert risk["completion_priority_gap"] == 710


def test_several_turn_scripted_smoke_rejects_actions_without_player_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_product_smoke_module()
    fake_api = _FakeScriptedSmokeApi(rotates_turns=False)
    monkeypatch.setattr(module, "create_game", fake_api.create_game)
    monkeypatch.setattr(module, "http_json", fake_api.http_json)

    with pytest.raises(module.SmokeFailure) as exc_info:
        module.several_turn_scripted_smoke(object())

    assert exc_info.value.tier == "scripted turn"
    assert "current_player_id" in str(exc_info.value)


def test_several_turn_scripted_smoke_accepts_full_cycle_with_transition_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_product_smoke_module()
    fake_api = _FakeScriptedSmokeApi(rotates_turns=True)
    monkeypatch.setattr(module, "create_game", fake_api.create_game)
    monkeypatch.setattr(module, "http_json", fake_api.http_json)

    module.several_turn_scripted_smoke(object())

    assert fake_api.sequence >= 4
    assert {event["actor_player_id"] for event in fake_api.events} == {"player-1", "player-2"}
    assert any(event["event_type"] == "TURN_STATE_SET" for event in fake_api.events)


class _FakeScriptedSmokeApi:
    def __init__(self, *, rotates_turns: bool) -> None:
        self.rotates_turns = rotates_turns
        self.player_ids = ("player-1", "player-2")
        self.current_player_index = 0
        self.turn_number = 1
        self.phase = "START_TURN"
        self.sequence = 0
        self.events: list[dict[str, object]] = []

    def create_game(self, *args: object, **kwargs: object) -> dict[str, object]:
        return {
            "id": "game-1",
            "players": [
                {"id": player_id, "controller_type": "human"} for player_id in self.player_ids
            ],
        }

    def http_json(
        self,
        _context: object,
        method: str,
        path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        if method == "GET" and path == "/games/game-1/state":
            return self._state_response()
        if method == "GET" and path.startswith("/games/game-1/legal-actions"):
            return {"legal_actions": [self._legal_action()]}
        if method == "GET" and path == "/games/game-1/events":
            return {"events": list(self.events)}
        if method == "POST" and path == "/games/game-1/actions":
            payload = kwargs["payload"]
            assert isinstance(payload, dict)
            return self._accept_action(payload)
        raise AssertionError(f"unexpected fake HTTP call: {method} {path}")

    def _state_response(self) -> dict[str, object]:
        state_hash = f"hash-{self.sequence}-{self.current_player_id}"
        return {
            "event_sequence": self.sequence,
            "state_hash": state_hash,
            "state": {
                "turn": {
                    "turn_number": self.turn_number,
                    "current_player_index": self.current_player_index,
                    "current_player_id": self.current_player_id,
                    "phase": self.phase,
                }
            },
        }

    def _legal_action(self) -> dict[str, object]:
        state = self._state_response()
        return {
            "actor_id": self.current_player_id,
            "type": "END_TURN"
            if self.rotates_turns and self.phase != "START_TURN"
            else "ROLL_DICE",
            "payload": {},
            "expected_state_hash": state["state_hash"],
            "expected_event_sequence": state["event_sequence"],
        }

    def _accept_action(self, action: dict[str, object]) -> dict[str, object]:
        actor_id = str(action["actor_id"])
        action_type = str(action["type"])
        self.sequence += 1
        if action_type == "END_TURN" and self.rotates_turns and self.phase != "START_TURN":
            self.current_player_index = (self.current_player_index + 1) % len(self.player_ids)
            self.turn_number += 1
            self.phase = "START_TURN"
            event_type = "TURN_STATE_SET"
            event_payload: dict[str, object] = {
                "turn_number": self.turn_number,
                "current_player_index": self.current_player_index,
                "current_player_id": self.current_player_id,
                "phase": self.phase,
                "consecutive_doubles": 0,
            }
        else:
            if action_type == "ROLL_DICE" and self.rotates_turns:
                self.phase = "POST_ROLL_MANAGEMENT"
            event_type = "DICE_ROLLED"
            event_payload = {"player_id": actor_id, "total": 7}
        event = {
            "id": f"event-{self.sequence}",
            "game_id": "game-1",
            "sequence": self.sequence,
            "actor_player_id": actor_id,
            "event_type": event_type,
            "payload": event_payload,
            "state_hash": f"hash-{self.sequence}-{self.current_player_id}",
            "created_at": "2026-07-06T00:00:00Z",
        }
        self.events.append(event)
        state = self._state_response()
        return {
            "status": "accepted",
            "accepted_events": [event],
            "state": state["state"],
            "state_hash": state["state_hash"],
            "event_sequence": state["event_sequence"],
        }

    @property
    def current_player_id(self) -> str:
        return self.player_ids[self.current_player_index]


def _load_product_smoke_module():
    spec = importlib.util.spec_from_file_location("product_smoke", PRODUCT_SMOKE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_live_strategy_smoke_module():
    spec = importlib.util.spec_from_file_location(
        "live_codex_ai_strategy_smoke", LIVE_STRATEGY_SMOKE_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
