"""Gated live strategy smoke for real `codex exec --json`.

Set RUN_LIVE_CODEX_AI=1 to run real Codex AI strategy probes. The command uses
`codex exec --json`, `--model gpt-5.4-mini`, `--output-schema`, and
model_reasoning_effort="low", then validates that the live model makes
strategically useful Monopoly decisions in four-AI debug-style game states.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.ai.context_pack import build_ai_context_pack  # noqa: E402
from app.ai.decision_schema import validate_ai_decision_output  # noqa: E402
from app.ai.orchestrator import (  # noqa: E402
    DEFAULT_AI_SANDBOX_DIR,
    CodexExecAIDecisionRequest,
    CodexExecTimeoutError,
    CodexSubprocessRunner,
    build_codex_exec_command,
    build_prompt,
    parse_codex_jsonl_events,
    write_ai_output_schema_file,
)
from app.rules.phases import TurnPhase  # noqa: E402
from app.rules.state import GameState, PlayerSetup, create_initial_game_state  # noqa: E402


LIVE_CODEX_ENV_VAR = "RUN_LIVE_CODEX_AI"
AI_PLAYER_ID = UUID("00000000-0000-0000-0000-00000000b102")
OTHER_PLAYER_ID = UUID("00000000-0000-0000-0000-00000000b103")
THIRD_PLAYER_ID = UUID("00000000-0000-0000-0000-00000000b104")
FOURTH_PLAYER_ID = UUID("00000000-0000-0000-0000-00000000b105")
NEGOTIATION_ID = UUID("00000000-0000-0000-0000-00000000b301")
ORANGE_PROPERTY_IDS = {
    "property_st_james_place",
    "property_tennessee_avenue",
    "property_new_york_avenue",
}
BROWN_PROPERTY_IDS = {
    "property_mediterranean_avenue",
    "property_baltic_avenue",
}
DARK_BLUE_PROPERTY_IDS = {
    "property_park_place",
    "property_boardwalk",
}


@dataclass(frozen=True, slots=True)
class StrategySmokeCase:
    name: str
    game_id: UUID
    decision_type: str
    state_factory: Callable[[UUID], GameState]
    verifier: Callable[[dict[str, Any]], None]
    actor_player_id: UUID = AI_PLAYER_ID


def main() -> int:
    if os.getenv(LIVE_CODEX_ENV_VAR) != "1":
        print(f"live Codex AI strategy smoke skipped; set {LIVE_CODEX_ENV_VAR}=1 to enable")
        return 0

    DEFAULT_AI_SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for case in _strategy_cases():
        try:
            parsed = _run_strategy_case(case)
            case.verifier(parsed)
        except (AssertionError, CodexExecTimeoutError, ValueError) as exc:
            print(
                json.dumps(
                    {"case": case.name, "status": "failed", "message": str(exc)}, sort_keys=True
                )
            )
            return 1
        results.append(_case_summary(case, parsed))

    print(json.dumps({"status": "ok", "cases": results}, sort_keys=True))
    return 0


def _strategy_cases() -> tuple[StrategySmokeCase, ...]:
    return (
        StrategySmokeCase(
            name="railroad_purchase_with_healthy_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b205"),
            decision_type="action_decision",
            state_factory=_railroad_purchase_state,
            verifier=_verify_railroad_purchase_with_healthy_cash,
        ),
        StrategySmokeCase(
            name="boardwalk_purchase_with_healthy_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b22b"),
            decision_type="action_decision",
            state_factory=_boardwalk_purchase_state,
            verifier=_verify_boardwalk_purchase_with_healthy_cash,
        ),
        StrategySmokeCase(
            name="railroad_purchase_completes_set_with_thin_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b227"),
            decision_type="action_decision",
            state_factory=_railroad_purchase_completes_set_state,
            verifier=_verify_railroad_purchase_completes_set_with_thin_cash,
        ),
        StrategySmokeCase(
            name="utility_purchase_completes_set_with_thin_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b231"),
            decision_type="action_decision",
            state_factory=_utility_purchase_completes_set_state,
            verifier=_verify_utility_purchase_completes_set_with_thin_cash,
        ),
        StrategySmokeCase(
            name="purchase_completes_color_group_with_thin_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b215"),
            decision_type="action_decision",
            state_factory=_purchase_completes_color_group_state,
            verifier=_verify_purchase_completes_color_group_with_thin_cash,
        ),
        StrategySmokeCase(
            name="purchase_blocks_opponent_color_group_with_thin_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b226"),
            decision_type="action_decision",
            state_factory=_purchase_blocks_opponent_color_group_state,
            verifier=_verify_purchase_blocks_opponent_color_group_with_thin_cash,
        ),
        StrategySmokeCase(
            name="purchase_blocks_opponent_utility_group_with_thin_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b232"),
            decision_type="action_decision",
            state_factory=_purchase_blocks_opponent_utility_group_state,
            verifier=_verify_purchase_blocks_opponent_utility_group_with_thin_cash,
        ),
        StrategySmokeCase(
            name="healthy_cash_avoids_mortgage",
            game_id=UUID("00000000-0000-0000-0000-00000000b208"),
            decision_type="action_decision",
            state_factory=_healthy_cash_mortgage_state,
            verifier=_verify_healthy_cash_avoids_mortgage,
        ),
        StrategySmokeCase(
            name="active_debt_uses_mortgage",
            game_id=UUID("00000000-0000-0000-0000-00000000b209"),
            decision_type="action_decision",
            state_factory=_active_debt_mortgage_state,
            verifier=_verify_active_debt_uses_mortgage,
        ),
        StrategySmokeCase(
            name="active_debt_settles_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b213"),
            decision_type="action_decision",
            state_factory=_active_debt_settlement_state,
            verifier=_verify_active_debt_settles_cash,
        ),
        StrategySmokeCase(
            name="active_debt_sells_house_before_mortgage",
            game_id=UUID("00000000-0000-0000-0000-00000000b214"),
            decision_type="action_decision",
            state_factory=_active_debt_sell_house_state,
            verifier=_verify_active_debt_sells_house_before_mortgage,
        ),
        StrategySmokeCase(
            name="healthy_cash_unmortgages_rent_property",
            game_id=UUID("00000000-0000-0000-0000-00000000b211"),
            decision_type="action_decision",
            state_factory=_healthy_cash_unmortgage_state,
            verifier=_verify_healthy_cash_unmortgages_rent_property,
        ),
        StrategySmokeCase(
            name="jail_card_used_before_fine_or_roll",
            game_id=UUID("00000000-0000-0000-0000-00000000b212"),
            decision_type="action_decision",
            state_factory=_jail_card_state,
            verifier=_verify_jail_card_used_before_fine_or_roll,
        ),
        StrategySmokeCase(
            name="auction_bid_within_valuation",
            game_id=UUID("00000000-0000-0000-0000-00000000b206"),
            decision_type="action_decision",
            state_factory=_auction_bid_state,
            verifier=_verify_auction_bid_within_valuation,
        ),
        StrategySmokeCase(
            name="auction_pass_above_valuation",
            game_id=UUID("00000000-0000-0000-0000-00000000b207"),
            decision_type="action_decision",
            state_factory=_auction_pass_state,
            verifier=_verify_auction_pass_above_valuation,
        ),
        StrategySmokeCase(
            name="auction_pass_to_preserve_cash_reserve",
            game_id=UUID("00000000-0000-0000-0000-00000000b217"),
            decision_type="action_decision",
            state_factory=_auction_cash_reserve_pass_state,
            verifier=_verify_auction_pass_above_valuation,
        ),
        StrategySmokeCase(
            name="auction_bid_to_complete_color_group",
            game_id=UUID("00000000-0000-0000-0000-00000000b210"),
            decision_type="action_decision",
            state_factory=_auction_color_group_completion_state,
            verifier=_verify_auction_bid_to_complete_color_group,
        ),
        StrategySmokeCase(
            name="auction_bid_to_complete_railroad_group",
            game_id=UUID("00000000-0000-0000-0000-00000000b235"),
            decision_type="action_decision",
            state_factory=_auction_railroad_group_completion_state,
            verifier=_verify_auction_bid_to_complete_railroad_group,
        ),
        StrategySmokeCase(
            name="auction_bid_to_complete_utility_group",
            game_id=UUID("00000000-0000-0000-0000-00000000b233"),
            decision_type="action_decision",
            state_factory=_auction_utility_group_completion_state,
            verifier=_verify_auction_bid_to_complete_utility_group,
        ),
        StrategySmokeCase(
            name="auction_bid_to_block_opponent_color_group",
            game_id=UUID("00000000-0000-0000-0000-00000000b225"),
            decision_type="action_decision",
            state_factory=_auction_block_opponent_color_group_state,
            verifier=_verify_auction_bid_to_block_opponent_color_group,
        ),
        StrategySmokeCase(
            name="auction_bid_to_block_opponent_railroad_group",
            game_id=UUID("00000000-0000-0000-0000-00000000b236"),
            decision_type="action_decision",
            state_factory=_auction_block_opponent_railroad_group_state,
            verifier=_verify_auction_bid_to_block_opponent_railroad_group,
        ),
        StrategySmokeCase(
            name="auction_bid_to_block_opponent_utility_group",
            game_id=UUID("00000000-0000-0000-0000-00000000b234"),
            decision_type="action_decision",
            state_factory=_auction_block_opponent_utility_group_state,
            verifier=_verify_auction_bid_to_block_opponent_utility_group,
        ),
        StrategySmokeCase(
            name="orange_monopoly_development",
            game_id=UUID("00000000-0000-0000-0000-00000000b201"),
            decision_type="action_decision",
            state_factory=_orange_monopoly_state,
            verifier=_verify_orange_monopoly_development,
        ),
        StrategySmokeCase(
            name="dark_blue_monopoly_development",
            game_id=UUID("00000000-0000-0000-0000-00000000b22c"),
            decision_type="action_decision",
            state_factory=_dark_blue_monopoly_state,
            verifier=_verify_dark_blue_monopoly_development,
        ),
        StrategySmokeCase(
            name="multiple_monopolies_prioritizes_orange_development",
            game_id=UUID("00000000-0000-0000-0000-00000000b218"),
            decision_type="action_decision",
            state_factory=_brown_and_orange_monopolies_state,
            verifier=_verify_orange_monopoly_development,
        ),
        StrategySmokeCase(
            name="low_cash_defers_monopoly_development",
            game_id=UUID("00000000-0000-0000-0000-00000000b219"),
            decision_type="action_decision",
            state_factory=_low_cash_orange_monopoly_state,
            verifier=_verify_low_cash_defers_monopoly_development,
        ),
        StrategySmokeCase(
            name="orange_near_monopoly_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b202"),
            decision_type="open_negotiation",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_near_monopoly_negotiation,
        ),
        StrategySmokeCase(
            name="dark_blue_near_monopoly_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b22d"),
            decision_type="open_negotiation",
            state_factory=_dark_blue_near_monopoly_state,
            verifier=_verify_dark_blue_near_monopoly_negotiation,
        ),
        StrategySmokeCase(
            name="railroad_near_set_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b228"),
            decision_type="open_negotiation",
            state_factory=_railroad_near_set_state,
            verifier=_verify_railroad_near_set_negotiation,
        ),
        StrategySmokeCase(
            name="utility_near_set_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b22f"),
            decision_type="open_negotiation",
            state_factory=_utility_near_set_state,
            verifier=_verify_utility_near_set_negotiation,
        ),
        StrategySmokeCase(
            name="multiple_near_monopolies_prioritizes_orange_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b216"),
            decision_type="open_negotiation",
            state_factory=_multiple_near_monopolies_state,
            verifier=_verify_orange_near_monopoly_negotiation,
        ),
        StrategySmokeCase(
            name="block_opponent_orange_near_monopoly_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b224"),
            decision_type="open_negotiation",
            state_factory=_opponent_orange_near_monopoly_state,
            verifier=_verify_block_opponent_orange_near_monopoly_negotiation,
        ),
        StrategySmokeCase(
            name="block_opponent_railroad_near_set_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b237"),
            decision_type="open_negotiation",
            state_factory=_opponent_railroad_near_set_state,
            verifier=_verify_block_opponent_railroad_near_set_negotiation,
        ),
        StrategySmokeCase(
            name="block_opponent_utility_near_set_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b238"),
            decision_type="open_negotiation",
            state_factory=_opponent_utility_near_set_state,
            verifier=_verify_block_opponent_utility_near_set_negotiation,
        ),
        StrategySmokeCase(
            name="orange_near_monopoly_deal_proposal",
            game_id=UUID("00000000-0000-0000-0000-00000000b203"),
            decision_type="deal_proposal",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_near_monopoly_deal_proposal,
        ),
        StrategySmokeCase(
            name="orange_cash_limited_deal_proposal",
            game_id=UUID("00000000-0000-0000-0000-00000000b245"),
            decision_type="deal_proposal",
            state_factory=_cash_limited_orange_near_monopoly_state,
            verifier=_verify_orange_cash_limited_deal_proposal,
        ),
        StrategySmokeCase(
            name="dark_blue_near_monopoly_deal_proposal",
            game_id=UUID("00000000-0000-0000-0000-00000000b22e"),
            decision_type="deal_proposal",
            state_factory=_dark_blue_near_monopoly_state,
            verifier=_verify_dark_blue_near_monopoly_deal_proposal,
        ),
        StrategySmokeCase(
            name="railroad_near_set_deal_proposal",
            game_id=UUID("00000000-0000-0000-0000-00000000b239"),
            decision_type="deal_proposal",
            state_factory=_railroad_near_set_state,
            verifier=_verify_railroad_near_set_deal_proposal,
        ),
        StrategySmokeCase(
            name="utility_near_set_deal_proposal",
            game_id=UUID("00000000-0000-0000-0000-00000000b230"),
            decision_type="deal_proposal",
            state_factory=_utility_near_set_state,
            verifier=_verify_utility_near_set_deal_proposal,
        ),
        StrategySmokeCase(
            name="railroad_good_deal_acceptance",
            game_id=UUID("00000000-0000-0000-0000-00000000b23a"),
            decision_type="accept_reject",
            state_factory=_railroad_near_set_state,
            verifier=_verify_railroad_good_deal_acceptance,
        ),
        StrategySmokeCase(
            name="utility_good_deal_acceptance",
            game_id=UUID("00000000-0000-0000-0000-00000000b23b"),
            decision_type="accept_reject",
            state_factory=_utility_near_set_state,
            verifier=_verify_utility_good_deal_acceptance,
        ),
        StrategySmokeCase(
            name="railroad_bad_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b23c"),
            decision_type="accept_reject",
            state_factory=_railroad_near_set_state,
            verifier=_verify_railroad_bad_deal_rejection,
            actor_player_id=OTHER_PLAYER_ID,
        ),
        StrategySmokeCase(
            name="utility_bad_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b23d"),
            decision_type="accept_reject",
            state_factory=_utility_near_set_state,
            verifier=_verify_utility_bad_deal_rejection,
            actor_player_id=OTHER_PLAYER_ID,
        ),
        StrategySmokeCase(
            name="railroad_overpriced_deal_counteroffer",
            game_id=UUID("00000000-0000-0000-0000-00000000b23e"),
            decision_type="counteroffer",
            state_factory=_railroad_near_set_state,
            verifier=_verify_railroad_overpriced_deal_counteroffer,
        ),
        StrategySmokeCase(
            name="utility_overpriced_deal_counteroffer",
            game_id=UUID("00000000-0000-0000-0000-00000000b23f"),
            decision_type="counteroffer",
            state_factory=_utility_near_set_state,
            verifier=_verify_utility_overpriced_deal_counteroffer,
        ),
        StrategySmokeCase(
            name="orange_boardwalk_swap_deal_counteroffer",
            game_id=UUID("00000000-0000-0000-0000-00000000b240"),
            decision_type="counteroffer",
            state_factory=_orange_near_monopoly_with_boardwalk_state,
            verifier=_verify_orange_boardwalk_swap_deal_counteroffer,
        ),
        StrategySmokeCase(
            name="orange_bad_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b204"),
            decision_type="accept_reject",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_bad_deal_rejection,
            actor_player_id=OTHER_PLAYER_ID,
        ),
        StrategySmokeCase(
            name="orange_good_deal_acceptance",
            game_id=UUID("00000000-0000-0000-0000-00000000b220"),
            decision_type="accept_reject",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_good_deal_acceptance,
        ),
        StrategySmokeCase(
            name="orange_cash_return_deal_acceptance",
            game_id=UUID("00000000-0000-0000-0000-00000000b243"),
            decision_type="accept_reject",
            state_factory=_cash_return_orange_near_monopoly_state,
            verifier=_verify_orange_cash_return_deal_acceptance,
        ),
        StrategySmokeCase(
            name="orange_overpriced_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b221"),
            decision_type="accept_reject",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_overpriced_deal_rejection,
        ),
        StrategySmokeCase(
            name="orange_overpriced_deal_counteroffer",
            game_id=UUID("00000000-0000-0000-0000-00000000b229"),
            decision_type="counteroffer",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_overpriced_deal_counteroffer,
        ),
        StrategySmokeCase(
            name="orange_cash_draining_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b222"),
            decision_type="accept_reject",
            state_factory=_low_cash_orange_near_monopoly_state,
            verifier=_verify_orange_cash_draining_deal_rejection,
        ),
        StrategySmokeCase(
            name="orange_cash_draining_deal_counteroffer",
            game_id=UUID("00000000-0000-0000-0000-00000000b22a"),
            decision_type="counteroffer",
            state_factory=_low_cash_orange_near_monopoly_state,
            verifier=_verify_orange_cash_draining_deal_counteroffer,
        ),
        StrategySmokeCase(
            name="orange_mutual_completion_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b241"),
            decision_type="accept_reject",
            state_factory=_light_blue_near_set_and_opponent_orange_near_set_state,
            verifier=_verify_orange_mutual_completion_deal_rejection,
        ),
        StrategySmokeCase(
            name="orange_cash_round_trip_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b244"),
            decision_type="accept_reject",
            state_factory=_light_blue_near_set_and_opponent_orange_near_set_state,
            verifier=_verify_orange_cash_round_trip_deal_rejection,
        ),
        StrategySmokeCase(
            name="orange_monopoly_breakup_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b223"),
            decision_type="accept_reject",
            state_factory=_orange_monopoly_state,
            verifier=_verify_orange_monopoly_breakup_deal_rejection,
        ),
        StrategySmokeCase(
            name="orange_monopoly_breakup_property_swap_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b242"),
            decision_type="accept_reject",
            state_factory=_orange_monopoly_and_opponent_baltic_state,
            verifier=_verify_orange_monopoly_breakup_property_swap_rejection,
        ),
    )


def _run_strategy_case(case: StrategySmokeCase) -> dict[str, Any]:
    state = case.state_factory(case.game_id)
    pack = build_ai_context_pack(
        state,
        player_id=str(case.actor_player_id),
        decision_type=case.decision_type,
        caller_request_context=_caller_request_context(case),
        negotiations=_negotiations(case),
        negotiation_messages=_negotiation_messages(case),
        deals=_deals(case),
        rule_snippets=_strategy_rule_snippets(case),
    )
    request = CodexExecAIDecisionRequest(
        game_id=case.game_id,
        player_id=case.actor_player_id,
        decision_type=case.decision_type,
        negotiation_id=NEGOTIATION_ID
        if case.decision_type in {"deal_proposal", "counteroffer", "accept_reject"}
        else None,
        phase=state.turn.phase.value,
        state_hash=state.state_hash(),
        prompt_context=pack,
        timeout_seconds=300,
    )

    with tempfile.TemporaryDirectory(prefix=f"monopoly-live-strategy-{case.name}-") as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = write_ai_output_schema_file(
            temp_path / "agent-decision.schema.json",
            decision_type=case.decision_type,
        )
        output_last_message_path = temp_path / "last-message.json"
        command = build_codex_exec_command(
            codex_executable="codex.cmd" if os.name == "nt" else "codex",
            schema_file=schema_path,
            sandbox_dir=DEFAULT_AI_SANDBOX_DIR,
            output_last_message_path=output_last_message_path,
        )
        process = CodexSubprocessRunner().run(
            command,
            stdin=build_prompt(request),
            timeout_seconds=request.timeout_seconds,
            output_last_message_path=output_last_message_path,
        )

        if process.returncode != 0:
            raise ValueError(f"codex exec returned {process.returncode}: {process.stderr[-1000:]}")

        parsed_events = parse_codex_jsonl_events(process.stdout)
        final_output = (
            _read_last_message(output_last_message_path) or parsed_events.final_assistant_output
        )
        if final_output is None:
            raise ValueError("codex exec did not produce a final assistant output")
        return validate_ai_decision_output(final_output).root.model_dump(mode="json")


def _verify_railroad_purchase_with_healthy_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_reading_railroad"
    if "price" in payload:
        assert payload.get("price") == 200


def _verify_boardwalk_purchase_with_healthy_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_boardwalk"
    if "price" in payload:
        assert payload.get("price") == 400


def _verify_railroad_purchase_completes_set_with_thin_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_short_line_railroad"
    if "price" in payload:
        assert payload.get("price") == 200


def _verify_utility_purchase_completes_set_with_thin_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_water_works"
    if "price" in payload:
        assert payload.get("price") == 150


def _verify_purchase_completes_color_group_with_thin_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_tennessee_avenue"
    if "price" in payload:
        assert payload.get("price") == 180


def _verify_purchase_blocks_opponent_color_group_with_thin_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_virginia_avenue"
    if "price" in payload:
        assert payload.get("price") == 160


def _verify_purchase_blocks_opponent_utility_group_with_thin_cash(
    parsed: dict[str, Any],
) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_water_works"
    if "price" in payload:
        assert payload.get("price") == 150


def _verify_healthy_cash_avoids_mortgage(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "ROLL_DICE", f"expected ROLL_DICE, got {action.get('type')}"


def _verify_active_debt_uses_mortgage(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "MORTGAGE_PROPERTY", (
        f"expected MORTGAGE_PROPERTY, got {action.get('type')}"
    )
    assert payload.get("property_id") == "property_b_and_o_railroad"


def _verify_active_debt_settles_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "SETTLE_DEBT", f"expected SETTLE_DEBT, got {action.get('type')}"
    assert payload.get("amount") == 75


def _verify_active_debt_sells_house_before_mortgage(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "SELL_HOUSE", f"expected SELL_HOUSE, got {action.get('type')}"
    assert payload.get("property_id") == "property_oriental_avenue"
    if "proceeds" in payload:
        assert payload.get("proceeds") == 25


def _verify_healthy_cash_unmortgages_rent_property(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "UNMORTGAGE_PROPERTY", (
        f"expected UNMORTGAGE_PROPERTY, got {action.get('type')}"
    )
    assert payload.get("property_id") == "property_b_and_o_railroad"
    if "cost" in payload:
        assert payload.get("cost") == 110


def _verify_jail_card_used_before_fine_or_roll(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "USE_GET_OUT_OF_JAIL_CARD", (
        f"expected USE_GET_OUT_OF_JAIL_CARD, got {action.get('type')}"
    )
    assert payload.get("card_id") == "card_community_get_out_of_jail"


def _verify_auction_bid_within_valuation(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    amount = int(payload.get("amount", 0))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BID_AUCTION", f"expected BID_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_virginia_avenue"
    assert 52 <= amount <= 160, (
        f"expected a deliberate bid above floor and within valuation, got {amount}"
    )


def _verify_auction_pass_above_valuation(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "PASS_AUCTION", f"expected PASS_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_virginia_avenue"


def _verify_auction_bid_to_complete_color_group(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    amount = int(payload.get("amount", 0))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BID_AUCTION", f"expected BID_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_tennessee_avenue"
    assert 181 <= amount <= 270, f"expected a bid within group-completion valuation, got {amount}"


def _verify_auction_bid_to_complete_railroad_group(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    amount = int(payload.get("amount", 0))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BID_AUCTION", f"expected BID_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_short_line_railroad"
    assert 201 <= amount <= 300, (
        f"expected a bid within railroad-completion valuation, got {amount}"
    )


def _verify_auction_bid_to_complete_utility_group(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    amount = int(payload.get("amount", 0))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BID_AUCTION", f"expected BID_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_water_works"
    assert 151 <= amount <= 225, (
        f"expected a bid within utility-completion valuation, got {amount}"
    )


def _verify_auction_bid_to_block_opponent_color_group(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    amount = int(payload.get("amount", 0))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BID_AUCTION", f"expected BID_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_virginia_avenue"
    assert 161 <= amount <= 240, f"expected a blocking premium bid within valuation, got {amount}"


def _verify_auction_bid_to_block_opponent_railroad_group(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    amount = int(payload.get("amount", 0))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BID_AUCTION", f"expected BID_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_short_line_railroad"
    assert 201 <= amount <= 300, (
        f"expected a railroad blocking premium bid within valuation, got {amount}"
    )


def _verify_auction_bid_to_block_opponent_utility_group(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    amount = int(payload.get("amount", 0))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BID_AUCTION", f"expected BID_AUCTION, got {action.get('type')}"
    assert payload.get("property_id") == "property_water_works"
    assert 151 <= amount <= 225, (
        f"expected a utility blocking premium bid within valuation, got {amount}"
    )


def _verify_orange_monopoly_development(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_HOUSE", f"expected BUY_HOUSE, got {action.get('type')}"
    assert payload.get("property_id") == "property_new_york_avenue"
    assert payload.get("cost") == 100


def _verify_dark_blue_monopoly_development(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_HOUSE", f"expected BUY_HOUSE, got {action.get('type')}"
    assert payload.get("property_id") == "property_boardwalk"
    assert payload.get("cost") == 200


def _verify_low_cash_defers_monopoly_development(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "ROLL_DICE", f"expected ROLL_DICE, got {action.get('type')}"


def _verify_orange_near_monopoly_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [
        str(player_id) for player_id in negotiation.get("participant_player_ids", [])
    ]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert context.get("target_property_id") == "property_tennessee_avenue"
    assert context.get("target_owner_id") == str(OTHER_PLAYER_ID)


def _verify_dark_blue_near_monopoly_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [
        str(player_id) for player_id in negotiation.get("participant_player_ids", [])
    ]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert context.get("target_property_id") == "property_park_place"
    assert context.get("target_owner_id") == str(OTHER_PLAYER_ID)


def _verify_railroad_near_set_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [
        str(player_id) for player_id in negotiation.get("participant_player_ids", [])
    ]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert context.get("target_property_id") == "property_short_line_railroad"
    assert context.get("target_owner_id") == str(OTHER_PLAYER_ID)


def _verify_utility_near_set_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [
        str(player_id) for player_id in negotiation.get("participant_player_ids", [])
    ]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert context.get("target_property_id") == "property_water_works"
    assert context.get("target_owner_id") == str(OTHER_PLAYER_ID)


def _verify_block_opponent_orange_near_monopoly_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [
        str(player_id) for player_id in negotiation.get("participant_player_ids", [])
    ]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(THIRD_PLAYER_ID)]
    assert context.get("target_property_id") == "property_tennessee_avenue"
    assert context.get("target_owner_id") == str(THIRD_PLAYER_ID)
    assert context.get("opponent_player_id") == str(OTHER_PLAYER_ID)


def _verify_block_opponent_railroad_near_set_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [
        str(player_id) for player_id in negotiation.get("participant_player_ids", [])
    ]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(THIRD_PLAYER_ID)]
    assert context.get("target_property_id") == "property_short_line_railroad"
    assert context.get("target_owner_id") == str(THIRD_PLAYER_ID)
    assert context.get("opponent_player_id") == str(OTHER_PLAYER_ID)


def _verify_block_opponent_utility_near_set_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [
        str(player_id) for player_id in negotiation.get("participant_player_ids", [])
    ]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert context.get("target_property_id") == "property_electric_company"
    assert context.get("target_owner_id") == str(OTHER_PLAYER_ID)
    assert context.get("opponent_player_id") == str(THIRD_PLAYER_ID)


def _verify_orange_near_monopoly_deal_proposal(parsed: dict[str, Any]) -> None:
    deal = _dict(parsed.get("deal"))
    terms = _dict(deal.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "deal_proposal"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert deal.get("recipient_player_ids") == [str(OTHER_PLAYER_ID)]
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert cash_terms, "expected an immediate_cash_transfer in the proposal"
    assert property_terms, "expected an immediate_property_transfer in the proposal"
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and 180 <= int(term.get("amount", 0)) <= 270
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_tennessee_avenue"
        for term in property_terms
    )


def _verify_orange_cash_limited_deal_proposal(parsed: dict[str, Any]) -> None:
    deal = _dict(parsed.get("deal"))
    terms = _dict(deal.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "deal_proposal"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert deal.get("recipient_player_ids") == [str(OTHER_PLAYER_ID)]
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and 180 <= int(term.get("amount", 0)) <= 200
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_tennessee_avenue"
        for term in property_terms
    )


def _verify_dark_blue_near_monopoly_deal_proposal(parsed: dict[str, Any]) -> None:
    deal = _dict(parsed.get("deal"))
    terms = _dict(deal.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "deal_proposal"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert deal.get("recipient_player_ids") == [str(OTHER_PLAYER_ID)]
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and 350 <= int(term.get("amount", 0)) <= 525
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_park_place"
        for term in property_terms
    )


def _verify_railroad_near_set_deal_proposal(parsed: dict[str, Any]) -> None:
    deal = _dict(parsed.get("deal"))
    terms = _dict(deal.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "deal_proposal"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert deal.get("recipient_player_ids") == [str(OTHER_PLAYER_ID)]
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and 200 <= int(term.get("amount", 0)) <= 300
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_short_line_railroad"
        for term in property_terms
    )


def _verify_utility_near_set_deal_proposal(parsed: dict[str, Any]) -> None:
    deal = _dict(parsed.get("deal"))
    terms = _dict(deal.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "deal_proposal"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert deal.get("recipient_player_ids") == [str(OTHER_PLAYER_ID)]
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and 150 <= int(term.get("amount", 0)) <= 225
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_water_works"
        for term in property_terms
    )


def _verify_orange_bad_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(BAD_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_orange_good_deal_acceptance(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(GOOD_DEAL_ID)
    assert accept_reject.get("decision") == "accept", (
        f"expected accept, got {accept_reject.get('decision')}"
    )


def _verify_orange_cash_return_deal_acceptance(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(CASH_RETURN_DEAL_ID)
    assert accept_reject.get("decision") == "accept", (
        f"expected accept, got {accept_reject.get('decision')}"
    )


def _verify_railroad_good_deal_acceptance(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(FAIR_RAILROAD_DEAL_ID)
    assert accept_reject.get("decision") == "accept", (
        f"expected accept, got {accept_reject.get('decision')}"
    )


def _verify_utility_good_deal_acceptance(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(FAIR_UTILITY_DEAL_ID)
    assert accept_reject.get("decision") == "accept", (
        f"expected accept, got {accept_reject.get('decision')}"
    )


def _verify_railroad_bad_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(LOWBALL_RAILROAD_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_utility_bad_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(LOWBALL_UTILITY_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_orange_overpriced_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(OVERPRICED_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_orange_cash_round_trip_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(CASH_ROUND_TRIP_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_orange_overpriced_deal_counteroffer(parsed: dict[str, Any]) -> None:
    counteroffer = _dict(parsed.get("counteroffer"))
    terms = _dict(counteroffer.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "counteroffer"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert counteroffer.get("responds_to_deal_id") == str(OVERPRICED_DEAL_ID)
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and 180 <= int(term.get("amount", 0)) <= 270
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_tennessee_avenue"
        for term in property_terms
    )


def _verify_railroad_overpriced_deal_counteroffer(parsed: dict[str, Any]) -> None:
    counteroffer = _dict(parsed.get("counteroffer"))
    terms = _dict(counteroffer.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "counteroffer"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert counteroffer.get("responds_to_deal_id") == str(OVERPRICED_RAILROAD_DEAL_ID)
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and int(term.get("amount", 0)) == 300
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_short_line_railroad"
        for term in property_terms
    )


def _verify_utility_overpriced_deal_counteroffer(parsed: dict[str, Any]) -> None:
    counteroffer = _dict(parsed.get("counteroffer"))
    terms = _dict(counteroffer.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "counteroffer"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert counteroffer.get("responds_to_deal_id") == str(OVERPRICED_UTILITY_DEAL_ID)
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and int(term.get("amount", 0)) == 225
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_water_works"
        for term in property_terms
    )


def _verify_orange_boardwalk_swap_deal_counteroffer(parsed: dict[str, Any]) -> None:
    counteroffer = _dict(parsed.get("counteroffer"))
    terms = _dict(counteroffer.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "counteroffer"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert counteroffer.get("responds_to_deal_id") == str(BOARDWALK_SWAP_DEAL_ID)
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert all(term.get("property_id") != "property_boardwalk" for term in property_terms)
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and int(term.get("amount", 0)) == 270
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_tennessee_avenue"
        for term in property_terms
    )


def _verify_orange_cash_draining_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(CASH_DRAINING_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_orange_cash_draining_deal_counteroffer(parsed: dict[str, Any]) -> None:
    counteroffer = _dict(parsed.get("counteroffer"))
    terms = _dict(counteroffer.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [
        term for term in instruments if term.get("kind") == "immediate_property_transfer"
    ]

    assert parsed.get("decision_type") == "counteroffer"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert counteroffer.get("responds_to_deal_id") == str(CASH_DRAINING_DEAL_ID)
    assert terms.get("kind") == "structured_deal"
    assert terms.get("deal_schema_version") == 1
    assert terms.get("participants") == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert any(
        term.get("from_player_id") == str(AI_PLAYER_ID)
        and term.get("to_player_id") == str(OTHER_PLAYER_ID)
        and int(term.get("amount", 0)) == 200
        for term in cash_terms
    )
    assert any(
        term.get("from_player_id") == str(OTHER_PLAYER_ID)
        and term.get("to_player_id") == str(AI_PLAYER_ID)
        and term.get("property_id") == "property_tennessee_avenue"
        for term in property_terms
    )


def _verify_orange_mutual_completion_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(MUTUAL_COMPLETION_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_orange_monopoly_breakup_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(BREAKUP_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _verify_orange_monopoly_breakup_property_swap_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(BREAKUP_PROPERTY_SWAP_DEAL_ID)
    assert accept_reject.get("decision") == "reject", (
        f"expected reject, got {accept_reject.get('decision')}"
    )


def _case_summary(case: StrategySmokeCase, parsed: dict[str, Any]) -> dict[str, Any]:
    if case.decision_type == "action_decision":
        action = _dict(parsed.get("action"))
        payload = _dict(action.get("payload"))
        return {
            "case": case.name,
            "status": "ok",
            "action_type": action.get("type"),
            "property_id": payload.get("property_id"),
        }
    if case.decision_type == "deal_proposal":
        terms = _dict(_dict(parsed.get("deal")).get("terms"))
        return {
            "case": case.name,
            "status": "ok",
            "decision_type": parsed.get("decision_type"),
            "term_kinds": [
                term.get("kind") for term in terms.get("terms", []) if isinstance(term, dict)
            ],
        }
    if case.decision_type == "counteroffer":
        terms = _dict(_dict(parsed.get("counteroffer")).get("terms"))
        return {
            "case": case.name,
            "status": "ok",
            "decision_type": parsed.get("decision_type"),
            "responds_to_deal_id": _dict(parsed.get("counteroffer")).get(
                "responds_to_deal_id"
            ),
            "term_kinds": [
                term.get("kind") for term in terms.get("terms", []) if isinstance(term, dict)
            ],
        }
    if case.decision_type == "accept_reject":
        accept_reject = _dict(parsed.get("accept_reject"))
        return {
            "case": case.name,
            "status": "ok",
            "decision_type": parsed.get("decision_type"),
            "decision": accept_reject.get("decision"),
        }
    negotiation = _dict(parsed.get("negotiation"))
    context = _dict(negotiation.get("context"))
    return {
        "case": case.name,
        "status": "ok",
        "decision_type": parsed.get("decision_type"),
        "target_property_id": context.get("target_property_id"),
    }


def _caller_request_context(case: StrategySmokeCase) -> dict[str, Any]:
    if case.decision_type == "accept_reject":
        if case.name == "orange_cash_return_deal_acceptance":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(CASH_RETURN_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Ada asks Grace to pay $400 for "
                    "Tennessee Avenue but returns $200 cash in the same deal, making "
                    "the net price $200."
                ),
                "strategic_position": (
                    "Tennessee Avenue completes Grace's Orange set and the net price "
                    "preserves liquidity."
                ),
            }
        if case.name == "railroad_good_deal_acceptance":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(FAIR_RAILROAD_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Ada offers Short Line Railroad "
                    "for $250, which completes Grace's railroad set."
                ),
                "strategic_position": (
                    "Grace owns the other three railroads and can afford this fair "
                    "completion price while preserving liquidity."
                ),
            }
        if case.name == "utility_good_deal_acceptance":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(FAIR_UTILITY_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Ada offers Water Works for $187, "
                    "which completes Grace's utility set."
                ),
                "strategic_position": (
                    "Grace owns Electric Company and can afford this fair completion "
                    "price while preserving liquidity."
                ),
            }
        if case.name == "railroad_bad_deal_rejection":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(LOWBALL_RAILROAD_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Grace offers Ada $1 for Short Line "
                    "Railroad, which would complete Grace's railroad set."
                ),
                "strategic_position": (
                    "Ada owns Short Line Railroad and should protect railroad-set leverage "
                    "unless paid a fair premium."
                ),
            }
        if case.name == "utility_bad_deal_rejection":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(LOWBALL_UTILITY_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Grace offers Ada $1 for Water Works, "
                    "which would complete Grace's utility set."
                ),
                "strategic_position": (
                    "Ada owns Water Works and should protect utility-set leverage unless "
                    "paid a fair premium."
                ),
            }
        if case.name == "orange_mutual_completion_deal_rejection":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(MUTUAL_COMPLETION_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Ada offers Connecticut Avenue and "
                    "$150 for Tennessee Avenue, completing Light Blue for Grace but "
                    "Orange for Ada."
                ),
                "strategic_position": (
                    "Grace should reject because Orange has stronger completion pressure "
                    "than Light Blue."
                ),
            }
        if case.name == "orange_cash_round_trip_deal_rejection":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(CASH_ROUND_TRIP_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Ada offers Grace $320 for "
                    "Tennessee Avenue but asks Grace to return $200 cash in the "
                    "same deal, so Ada's net payment is only $120."
                ),
                "strategic_position": (
                    "Grace should reject because Tennessee Avenue completes Ada's "
                    "Orange set and the net compensation is below the strategic floor."
                ),
            }
        if case.name == "orange_monopoly_breakup_property_swap_rejection":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(BREAKUP_PROPERTY_SWAP_DEAL_ID),
                "requested_decision": (
                    "Respond to the current offer. Ada offers Baltic Avenue for "
                    "Tennessee Avenue from Grace's complete Orange monopoly."
                ),
                "strategic_position": (
                    "Grace should reject because Baltic Avenue is far below the value "
                    "of breaking a complete Orange set."
                ),
            }
        return {
            "mode": "live_strategy_smoke",
            "negotiation_id": str(NEGOTIATION_ID),
            "deal_id": str(BAD_DEAL_ID),
            "requested_decision": (
                "Respond to the current offer. Grace offers Ada $1 for Tennessee Avenue, "
                "which would complete Grace's Orange monopoly."
            ),
            "strategic_position": (
                "Ada owns Tennessee Avenue and should protect monopoly-blocking leverage unless paid a fair premium."
            ),
        }
    if case.decision_type not in {"deal_proposal", "counteroffer"}:
        return {}
    if case.decision_type == "counteroffer":
        if case.name == "railroad_overpriced_deal_counteroffer":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(OVERPRICED_RAILROAD_DEAL_ID),
                "requested_decision": (
                    "Counteroffer the overpriced Short Line Railroad proposal at a "
                    "strategic value."
                ),
            }
        if case.name == "utility_overpriced_deal_counteroffer":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(OVERPRICED_UTILITY_DEAL_ID),
                "requested_decision": (
                    "Counteroffer the overpriced Water Works proposal at a strategic value."
                ),
            }
        if case.name == "orange_boardwalk_swap_deal_counteroffer":
            return {
                "mode": "live_strategy_smoke",
                "negotiation_id": str(NEGOTIATION_ID),
                "deal_id": str(BOARDWALK_SWAP_DEAL_ID),
                "requested_decision": (
                    "Counteroffer the Tennessee Avenue for Boardwalk proposal with cash "
                    "instead of giving away Boardwalk."
                ),
            }
        deal_id = (
            CASH_DRAINING_DEAL_ID
            if case.name == "orange_cash_draining_deal_counteroffer"
            else OVERPRICED_DEAL_ID
        )
        requested_decision = (
            "Counteroffer the cash-draining Tennessee Avenue proposal while preserving liquidity."
            if case.name == "orange_cash_draining_deal_counteroffer"
            else "Counteroffer the overpriced Tennessee Avenue proposal at a strategic value."
        )
        return {
            "mode": "live_strategy_smoke",
            "negotiation_id": str(NEGOTIATION_ID),
            "deal_id": str(deal_id),
            "requested_decision": requested_decision,
        }
    if case.name == "dark_blue_near_monopoly_deal_proposal":
        return {
            "mode": "live_strategy_smoke",
            "negotiation_id": str(NEGOTIATION_ID),
            "requested_decision": "Propose a structured deal to acquire Park Place.",
        }
    if case.name == "railroad_near_set_deal_proposal":
        return {
            "mode": "live_strategy_smoke",
            "negotiation_id": str(NEGOTIATION_ID),
            "requested_decision": "Propose a structured deal to acquire Short Line Railroad.",
        }
    if case.name == "utility_near_set_deal_proposal":
        return {
            "mode": "live_strategy_smoke",
            "negotiation_id": str(NEGOTIATION_ID),
            "requested_decision": "Propose a structured deal to acquire Water Works.",
        }
    if case.name == "orange_cash_limited_deal_proposal":
        return {
            "mode": "live_strategy_smoke",
            "negotiation_id": str(NEGOTIATION_ID),
            "requested_decision": (
                "Propose a structured deal to acquire Tennessee Avenue while "
                "preserving Grace's cash reserve."
            ),
        }
    return {
        "mode": "live_strategy_smoke",
        "negotiation_id": str(NEGOTIATION_ID),
        "requested_decision": "Propose a structured deal to acquire Tennessee Avenue.",
    }


def _negotiations(case: StrategySmokeCase) -> tuple[dict[str, Any], ...]:
    if case.decision_type not in {"deal_proposal", "counteroffer", "accept_reject"}:
        return ()
    current_deal_id = None
    if case.name == "orange_bad_deal_rejection":
        current_deal_id = str(BAD_DEAL_ID)
    elif case.name == "orange_good_deal_acceptance":
        current_deal_id = str(GOOD_DEAL_ID)
    elif case.name == "orange_cash_return_deal_acceptance":
        current_deal_id = str(CASH_RETURN_DEAL_ID)
    elif case.name == "railroad_good_deal_acceptance":
        current_deal_id = str(FAIR_RAILROAD_DEAL_ID)
    elif case.name == "utility_good_deal_acceptance":
        current_deal_id = str(FAIR_UTILITY_DEAL_ID)
    elif case.name == "railroad_bad_deal_rejection":
        current_deal_id = str(LOWBALL_RAILROAD_DEAL_ID)
    elif case.name == "utility_bad_deal_rejection":
        current_deal_id = str(LOWBALL_UTILITY_DEAL_ID)
    elif case.name == "railroad_overpriced_deal_counteroffer":
        current_deal_id = str(OVERPRICED_RAILROAD_DEAL_ID)
    elif case.name == "utility_overpriced_deal_counteroffer":
        current_deal_id = str(OVERPRICED_UTILITY_DEAL_ID)
    elif case.name == "orange_boardwalk_swap_deal_counteroffer":
        current_deal_id = str(BOARDWALK_SWAP_DEAL_ID)
    elif case.name in {"orange_overpriced_deal_rejection", "orange_overpriced_deal_counteroffer"}:
        current_deal_id = str(OVERPRICED_DEAL_ID)
    elif case.name in {"orange_cash_draining_deal_rejection", "orange_cash_draining_deal_counteroffer"}:
        current_deal_id = str(CASH_DRAINING_DEAL_ID)
    elif case.name == "orange_mutual_completion_deal_rejection":
        current_deal_id = str(MUTUAL_COMPLETION_DEAL_ID)
    elif case.name == "orange_cash_round_trip_deal_rejection":
        current_deal_id = str(CASH_ROUND_TRIP_DEAL_ID)
    elif case.name == "orange_monopoly_breakup_deal_rejection":
        current_deal_id = str(BREAKUP_DEAL_ID)
    elif case.name == "orange_monopoly_breakup_property_swap_rejection":
        current_deal_id = str(BREAKUP_PROPERTY_SWAP_DEAL_ID)
    if case.name == "dark_blue_near_monopoly_deal_proposal":
        return (
            {
                "id": str(NEGOTIATION_ID),
                "opened_by_player_id": str(AI_PLAYER_ID),
                "status": "active",
                "phase": "START_TURN",
                "round_number": 1,
                "context": {
                    "participant_player_ids": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
                    "current_deal_id": current_deal_id,
                    "context": {
                        "topic": "Trade for Park Place to complete Dark Blue",
                        "target_property_id": "property_park_place",
                        "target_property_name": "Park Place",
                        "target_owner_id": str(OTHER_PLAYER_ID),
                        "target_owner_name": "Ada",
                        "suggested_offer": {
                            "cash_budget_floor": 350,
                            "cash_budget_ceiling": 525,
                            "avoid_trading_away_group_property_ids": [
                                "property_boardwalk"
                            ],
                        },
                    },
                },
                "created_at": "2026-07-08T00:00:00Z",
            },
        )
    if case.name in {
        "utility_near_set_deal_proposal",
        "utility_good_deal_acceptance",
        "utility_bad_deal_rejection",
        "utility_overpriced_deal_counteroffer",
    }:
        return (
            {
                "id": str(NEGOTIATION_ID),
                "opened_by_player_id": str(AI_PLAYER_ID),
                "status": "active",
                "phase": "START_TURN",
                "round_number": 1,
                "context": {
                    "participant_player_ids": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
                    "current_deal_id": current_deal_id,
                    "context": {
                        "topic": "Trade for Water Works to complete Utilities",
                        "target_property_id": "property_water_works",
                        "target_property_name": "Water Works",
                        "target_owner_id": str(OTHER_PLAYER_ID),
                        "target_owner_name": "Ada",
                        "suggested_offer": {
                            "cash_budget_floor": 150,
                            "cash_budget_ceiling": 225,
                            "avoid_trading_away_group_property_ids": [
                                "property_electric_company"
                            ],
                        },
                    },
                },
                "created_at": "2026-07-08T00:00:00Z",
            },
        )
    if case.name in {
        "railroad_near_set_deal_proposal",
        "railroad_good_deal_acceptance",
        "railroad_bad_deal_rejection",
        "railroad_overpriced_deal_counteroffer",
    }:
        return (
            {
                "id": str(NEGOTIATION_ID),
                "opened_by_player_id": str(AI_PLAYER_ID),
                "status": "active",
                "phase": "START_TURN",
                "round_number": 1,
                "context": {
                    "participant_player_ids": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
                    "current_deal_id": current_deal_id,
                    "context": {
                        "topic": "Trade for Short Line Railroad to complete Railroads",
                        "target_property_id": "property_short_line_railroad",
                        "target_property_name": "Short Line Railroad",
                        "target_owner_id": str(OTHER_PLAYER_ID),
                        "target_owner_name": "Ada",
                        "suggested_offer": {
                            "cash_budget_floor": 200,
                            "cash_budget_ceiling": 300,
                            "avoid_trading_away_group_property_ids": [
                                "property_reading_railroad",
                                "property_pennsylvania_railroad",
                                "property_b_and_o_railroad",
                            ],
                        },
                    },
                },
                "created_at": "2026-07-08T00:00:00Z",
            },
        )
    return (
        {
            "id": str(NEGOTIATION_ID),
            "opened_by_player_id": str(AI_PLAYER_ID),
            "status": "active",
            "phase": "START_TURN",
            "round_number": 1,
            "context": {
                "participant_player_ids": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
                "current_deal_id": current_deal_id,
                "context": {
                    "topic": "Trade for Tennessee Avenue to complete Orange",
                    "target_property_id": "property_tennessee_avenue",
                    "target_property_name": "Tennessee Avenue",
                    "target_owner_id": str(OTHER_PLAYER_ID),
                    "target_owner_name": "Ada",
                    "suggested_offer": {
                        "cash_budget_floor": 180,
                        "cash_budget_ceiling": 270,
                    },
                },
            },
            "created_at": "2026-07-08T00:00:00Z",
        },
    )


def _negotiation_messages(case: StrategySmokeCase) -> tuple[dict[str, Any], ...]:
    if case.name == "orange_bad_deal_rejection":
        return (
            {
                "id": "live-strategy-lowball-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(AI_PLAYER_ID),
                "recipient_player_id": str(OTHER_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I will pay $1 for Tennessee Avenue so I can complete Orange.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "orange_good_deal_acceptance":
        return (
            {
                "id": "live-strategy-fair-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can sell Tennessee Avenue for $220 so you complete Orange.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "orange_cash_return_deal_acceptance":
        return (
            {
                "id": "live-strategy-cash-return-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": (
                    "I can sell Tennessee Avenue for $400 and return $200 cash in the "
                    "same deal."
                ),
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "railroad_good_deal_acceptance":
        return (
            {
                "id": "live-strategy-fair-railroad-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can sell Short Line Railroad for $250 so you complete Railroads.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "utility_good_deal_acceptance":
        return (
            {
                "id": "live-strategy-fair-utility-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can sell Water Works for $187 so you complete Utilities.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "railroad_bad_deal_rejection":
        return (
            {
                "id": "live-strategy-lowball-railroad-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(AI_PLAYER_ID),
                "recipient_player_id": str(OTHER_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I will pay $1 for Short Line Railroad so I can complete Railroads.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "utility_bad_deal_rejection":
        return (
            {
                "id": "live-strategy-lowball-utility-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(AI_PLAYER_ID),
                "recipient_player_id": str(OTHER_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I will pay $1 for Water Works so I can complete Utilities.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "railroad_overpriced_deal_counteroffer":
        return (
            {
                "id": "live-strategy-overpriced-railroad-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can sell Short Line Railroad for $400 so you complete Railroads.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "utility_overpriced_deal_counteroffer":
        return (
            {
                "id": "live-strategy-overpriced-utility-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can sell Water Works for $300 so you complete Utilities.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "orange_boardwalk_swap_deal_counteroffer":
        return (
            {
                "id": "live-strategy-boardwalk-swap-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can trade Tennessee Avenue for Boardwalk so you complete Orange.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name in {"orange_overpriced_deal_rejection", "orange_overpriced_deal_counteroffer"}:
        return (
            {
                "id": "live-strategy-overpriced-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can sell Tennessee Avenue for $400 so you complete Orange.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name in {"orange_cash_draining_deal_rejection", "orange_cash_draining_deal_counteroffer"}:
        return (
            {
                "id": "live-strategy-cash-draining-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can sell Tennessee Avenue for $220 even though your cash is thin.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "orange_mutual_completion_deal_rejection":
        return (
            {
                "id": "live-strategy-mutual-completion-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": (
                    "I can add $150 and Connecticut Avenue for Tennessee Avenue so we "
                    "both complete sets."
                ),
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "orange_cash_round_trip_deal_rejection":
        return (
            {
                "id": "live-strategy-cash-round-trip-offer-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": (
                    "I can pay $320 for Tennessee Avenue if you return $200 cash "
                    "in the same deal."
                ),
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "orange_monopoly_breakup_deal_rejection":
        return (
            {
                "id": "live-strategy-monopoly-breakup-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can pay $300 for Tennessee Avenue from your Orange monopoly.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.name == "orange_monopoly_breakup_property_swap_rejection":
        return (
            {
                "id": "live-strategy-monopoly-breakup-swap-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(OTHER_PLAYER_ID),
                "recipient_player_id": str(AI_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I can trade Baltic Avenue for Tennessee Avenue from your Orange monopoly.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:02Z",
            },
        )
    if case.decision_type != "deal_proposal":
        return ()
    if case.name == "dark_blue_near_monopoly_deal_proposal":
        return (
            {
                "id": "live-strategy-dark-blue-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(AI_PLAYER_ID),
                "recipient_player_id": str(OTHER_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I want Park Place to complete Dark Blue and can offer fair cash now.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:01Z",
            },
        )
    if case.name == "utility_near_set_deal_proposal":
        return (
            {
                "id": "live-strategy-utility-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(AI_PLAYER_ID),
                "recipient_player_id": str(OTHER_PLAYER_ID),
                "message_type": "freeform_message",
                "body": "I want Water Works to complete Utilities and can offer fair cash now.",
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:01Z",
            },
        )
    if case.name == "railroad_near_set_deal_proposal":
        return (
            {
                "id": "live-strategy-railroad-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(AI_PLAYER_ID),
                "recipient_player_id": str(OTHER_PLAYER_ID),
                "message_type": "freeform_message",
                "body": (
                    "I want Short Line Railroad to complete Railroads and can offer fair cash now."
                ),
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:01Z",
            },
        )
    if case.name == "orange_cash_limited_deal_proposal":
        return (
            {
                "id": "live-strategy-cash-limited-orange-message-1",
                "negotiation_id": str(NEGOTIATION_ID),
                "sender_player_id": str(AI_PLAYER_ID),
                "recipient_player_id": str(OTHER_PLAYER_ID),
                "message_type": "freeform_message",
                "body": (
                    "I want Tennessee Avenue to complete Orange, but I need to preserve "
                    "my cash reserve."
                ),
                "payload": {"message_type": "freeform_message"},
                "created_at": "2026-07-08T00:00:01Z",
            },
        )
    return (
        {
            "id": "live-strategy-message-1",
            "negotiation_id": str(NEGOTIATION_ID),
            "sender_player_id": str(AI_PLAYER_ID),
            "recipient_player_id": str(OTHER_PLAYER_ID),
            "message_type": "freeform_message",
            "body": "I want Tennessee Avenue to complete Orange and can offer fair cash now.",
            "payload": {"message_type": "freeform_message"},
            "created_at": "2026-07-08T00:00:01Z",
        },
    )


def _deals(case: StrategySmokeCase) -> tuple[dict[str, Any], ...]:
    if case.decision_type not in {"counteroffer", "accept_reject"}:
        return ()
    deal_id = BAD_DEAL_ID
    terms = _bad_deal_terms()
    proposer_id = AI_PLAYER_ID
    if case.name == "orange_good_deal_acceptance":
        deal_id = GOOD_DEAL_ID
        terms = _good_deal_terms()
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "orange_cash_return_deal_acceptance":
        deal_id = CASH_RETURN_DEAL_ID
        terms = _cash_return_tennessee_completion_terms()
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "railroad_good_deal_acceptance":
        deal_id = FAIR_RAILROAD_DEAL_ID
        terms = _fair_railroad_deal_terms()
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "utility_good_deal_acceptance":
        deal_id = FAIR_UTILITY_DEAL_ID
        terms = _fair_utility_deal_terms()
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "railroad_bad_deal_rejection":
        deal_id = LOWBALL_RAILROAD_DEAL_ID
        terms = _lowball_railroad_deal_terms()
        proposer_id = AI_PLAYER_ID
    elif case.name == "utility_bad_deal_rejection":
        deal_id = LOWBALL_UTILITY_DEAL_ID
        terms = _lowball_utility_deal_terms()
        proposer_id = AI_PLAYER_ID
    elif case.name == "railroad_overpriced_deal_counteroffer":
        deal_id = OVERPRICED_RAILROAD_DEAL_ID
        terms = _fair_railroad_deal_terms(amount=400)
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "utility_overpriced_deal_counteroffer":
        deal_id = OVERPRICED_UTILITY_DEAL_ID
        terms = _fair_utility_deal_terms(amount=300)
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "orange_boardwalk_swap_deal_counteroffer":
        deal_id = BOARDWALK_SWAP_DEAL_ID
        terms = _boardwalk_for_tennessee_deal_terms()
        proposer_id = OTHER_PLAYER_ID
    elif case.name in {"orange_overpriced_deal_rejection", "orange_overpriced_deal_counteroffer"}:
        deal_id = OVERPRICED_DEAL_ID
        terms = _good_deal_terms(amount=400)
        proposer_id = OTHER_PLAYER_ID
    elif case.name in {"orange_cash_draining_deal_rejection", "orange_cash_draining_deal_counteroffer"}:
        deal_id = CASH_DRAINING_DEAL_ID
        terms = _good_deal_terms(amount=220)
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "orange_mutual_completion_deal_rejection":
        deal_id = MUTUAL_COMPLETION_DEAL_ID
        terms = _mutual_light_blue_for_orange_completion_terms()
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "orange_cash_round_trip_deal_rejection":
        deal_id = CASH_ROUND_TRIP_DEAL_ID
        terms = _cash_round_trip_tennessee_completion_terms()
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "orange_monopoly_breakup_deal_rejection":
        deal_id = BREAKUP_DEAL_ID
        terms = _monopoly_breakup_terms(amount=300)
        proposer_id = OTHER_PLAYER_ID
    elif case.name == "orange_monopoly_breakup_property_swap_rejection":
        deal_id = BREAKUP_PROPERTY_SWAP_DEAL_ID
        terms = _monopoly_breakup_property_swap_terms()
        proposer_id = OTHER_PLAYER_ID
    return (
        {
            "id": str(deal_id),
            "negotiation_id": str(NEGOTIATION_ID),
            "proposed_by_player_id": str(proposer_id),
            "parent_deal_id": None,
            "status": "proposed",
            "version": 1,
            "terms": terms,
            "validation_errors": [],
            "created_at": "2026-07-08T00:00:03Z",
            "updated_at": "2026-07-08T00:00:03Z",
            "accepted_at": None,
        },
    )


def _strategy_rule_snippets(case: StrategySmokeCase) -> tuple[dict[str, str], ...]:
    if case.name == "auction_bid_to_complete_color_group":
        return (
            {
                "id": "live-strategy-bid-auction-to-complete-color-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns St. James Place and New York Avenue. "
                    "property_tennessee_avenue completes Orange, so valuation_basis is "
                    "property_group_completion_premium and the ceiling is $270. BID_AUCTION "
                    "within $181 to $270 instead of passing."
                ),
            },
        )
    if case.name == "auction_bid_to_complete_railroad_group":
        return (
            {
                "id": "live-strategy-bid-auction-to-complete-railroad-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns Reading Railroad, Pennsylvania "
                    "Railroad, and B&O Railroad. property_short_line_railroad completes "
                    "all four railroads, so valuation_basis is property_group_completion_premium "
                    "and the ceiling is $300. BID_AUCTION within $201 to $300 instead of passing."
                ),
            },
        )
    if case.name == "auction_bid_to_complete_utility_group":
        return (
            {
                "id": "live-strategy-bid-auction-to-complete-utility-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns property_electric_company. "
                    "property_water_works completes Utilities, so valuation_basis is "
                    "property_group_completion_premium and the ceiling is $225. BID_AUCTION "
                    "within $151 to $225 instead of passing."
                ),
            },
        )
    if case.name == "active_debt_uses_mortgage":
        return (
            {
                "id": "live-strategy-mortgage-for-active-debt",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has $0 cash, owes Ada $75, and owns "
                    "property_b_and_o_railroad. MORTGAGE_PROPERTY raises $100 and avoids "
                    "DECLARE_BANKRUPTCY. Choose MORTGAGE_PROPERTY for property_b_and_o_railroad."
                ),
            },
        )
    if case.name == "active_debt_settles_cash":
        return (
            {
                "id": "live-strategy-settle-cash-debt",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has $75 cash and owes Ada $75. "
                    "SETTLE_DEBT pays the active debt in full while preserving all property. "
                    "Choose SETTLE_DEBT before SELL_HOUSE, MORTGAGE_PROPERTY, or DECLARE_BANKRUPTCY."
                ),
            },
        )
    if case.name == "active_debt_sells_house_before_mortgage":
        return (
            {
                "id": "live-strategy-sell-house-before-mortgage",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has $0 cash, owes Ada $25, owns the "
                    "Light Blue group with one house on property_oriental_avenue, and owns "
                    "property_b_and_o_railroad. SELL_HOUSE raises exactly $25 and keeps "
                    "the railroad unmortgaged. Choose SELL_HOUSE before MORTGAGE_PROPERTY "
                    "or DECLARE_BANKRUPTCY."
                ),
            },
        )
    if case.name == "healthy_cash_unmortgages_rent_property":
        return (
            {
                "id": "live-strategy-unmortgage-healthy-cash",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has $900 cash and owns mortgaged "
                    "property_b_and_o_railroad. UNMORTGAGE_PROPERTY costs $110, leaves "
                    "$790 cash, and can restore rent collection. Choose UNMORTGAGE_PROPERTY "
                    "for property_b_and_o_railroad before ROLL_DICE."
                ),
            },
        )
    if case.name == "jail_card_used_before_fine_or_roll":
        return (
            {
                "id": "live-strategy-use-jail-card-before-fine",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace is in jail, has $900, and has "
                    "card_community_get_out_of_jail. USE_GET_OUT_OF_JAIL_CARD costs no "
                    "cash and should be chosen before PAY_JAIL_FINE or ROLL_DICE when "
                    "Grace wants to leave jail and keep moving."
                ),
            },
        )
    if case.name == "healthy_cash_avoids_mortgage":
        return (
            {
                "id": "live-strategy-avoid-unnecessary-mortgage",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has $900 cash, no active debt, and owns "
                    "property_b_and_o_railroad. MORTGAGE_PROPERTY is available but should be "
                    "avoided unless debt or urgent liquidity pressure exists. Choose ROLL_DICE."
                ),
            },
        )
    if case.name == "auction_pass_above_valuation":
        return (
            {
                "id": "live-strategy-pass-auction-above-valuation",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace can BID_AUCTION or PASS_AUCTION for "
                    "property_virginia_avenue. The minimum legal bid is $1001, which is "
                    "above the valuation ceiling of $160. Choose PASS_AUCTION."
                ),
            },
        )
    if case.name == "auction_pass_to_preserve_cash_reserve":
        return (
            {
                "id": "live-strategy-pass-auction-to-preserve-cash-reserve",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has only $220 cash in an auction for "
                    "property_virginia_avenue. The minimum legal BID_AUCTION is $51, but "
                    "auction_guidance valuation_ceiling is $0 because bidding would breach "
                    "the $300 cash reserve floor. Choose PASS_AUCTION."
                ),
            },
        )
    if case.name == "auction_bid_within_valuation":
        return (
            {
                "id": "live-strategy-bid-auction-within-valuation",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace can BID_AUCTION or PASS_AUCTION for "
                    "property_virginia_avenue. The legal amount 51 is only the floor, not "
                    "the recommended bid. Bid deliberately above the floor while staying at "
                    "or below the $160 valuation ceiling."
                ),
            },
        )
    if case.name == "auction_bid_to_block_opponent_color_group":
        return (
            {
                "id": "live-strategy-bid-auction-to-block-opponent-color-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Ada owns St. Charles Place and States Avenue. "
                    "property_virginia_avenue would complete Pink for Ada if she wins it, so "
                    "auction_guidance valuation_basis is block_opponent_group_completion_premium "
                    "and the ceiling is $240. BID_AUCTION within $161 to $240 instead of passing."
                ),
            },
        )
    if case.name == "auction_bid_to_block_opponent_railroad_group":
        return (
            {
                "id": "live-strategy-bid-auction-to-block-opponent-railroad-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Ada owns Reading Railroad, Pennsylvania "
                    "Railroad, and B&O Railroad. property_short_line_railroad would complete "
                    "all four railroads for Ada if she wins it, so auction_guidance "
                    "valuation_basis is block_opponent_group_completion_premium and the ceiling "
                    "is $300. BID_AUCTION within $201 to $300 instead of passing."
                ),
            },
        )
    if case.name == "auction_bid_to_block_opponent_utility_group":
        return (
            {
                "id": "live-strategy-bid-auction-to-block-opponent-utility-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Ada owns property_electric_company. "
                    "property_water_works would complete Utilities for Ada if she wins it, "
                    "so auction_guidance valuation_basis is block_opponent_group_completion_premium "
                    "and the ceiling is $225. BID_AUCTION within $151 to $225 instead of passing."
                ),
            },
        )
    if case.name == "railroad_purchase_with_healthy_cash":
        return (
            {
                "id": "live-strategy-buy-railroad-over-auction",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has healthy cash and landed on unowned "
                    "property_reading_railroad. Prefer BUY_PROPERTY over START_AUCTION because "
                    "auctioning gives competitors a chance to win the railroad."
                ),
            },
        )
    if case.name == "boardwalk_purchase_with_healthy_cash":
        return (
            {
                "id": "live-strategy-buy-boardwalk-over-auction",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace has $1300 and landed on unowned "
                    "property_boardwalk. Buying Boardwalk for $400 leaves $900, above "
                    "the healthy cash floor. Prefer BUY_PROPERTY over START_AUCTION because "
                    "auctioning gives competitors a chance to win a premium dark-blue property."
                ),
            },
        )
    if case.name == "railroad_purchase_completes_set_with_thin_cash":
        return (
            {
                "id": "live-strategy-buy-railroad-to-complete-set",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns Reading Railroad, Pennsylvania "
                    "Railroad, and B&O Railroad, has $400, and landed on unowned "
                    "property_short_line_railroad. Buying Short Line for $200 leaves $200 "
                    "and completes all four railroads. purchase_guidance recommendation is "
                    "buy_property_to_complete_group. Choose BUY_PROPERTY rather than "
                    "START_AUCTION."
                ),
            },
        )
    if case.name == "utility_purchase_completes_set_with_thin_cash":
        return (
            {
                "id": "live-strategy-buy-utility-to-complete-set",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns property_electric_company, "
                    "has $300, and landed on unowned property_water_works. Buying Water "
                    "Works for $150 leaves $150 and completes Utilities, doubling utility "
                    "rent multipliers. purchase_guidance recommendation is "
                    "buy_property_to_complete_group. Choose BUY_PROPERTY rather than "
                    "START_AUCTION."
                ),
            },
        )
    if case.name == "purchase_completes_color_group_with_thin_cash":
        return (
            {
                "id": "live-strategy-buy-property-to-complete-color-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns St. James Place and New York Avenue, "
                    "has $400, and landed on unowned property_tennessee_avenue. Buying Tennessee "
                    "for $180 leaves $220 and completes Orange, unlocking BUY_HOUSE development. "
                    "purchase_guidance recommendation is buy_property_to_complete_group. "
                    "Choose BUY_PROPERTY rather than START_AUCTION."
                ),
            },
        )
    if case.name == "purchase_blocks_opponent_color_group_with_thin_cash":
        return (
            {
                "id": "live-strategy-buy-property-to-block-opponent-color-group",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Ada owns St. Charles Place and States Avenue, "
                    "Grace has $400, and Grace landed on unowned property_virginia_avenue. "
                    "Buying Virginia for $160 leaves $240 and blocks Ada from completing Pink. "
                    "purchase_guidance recommendation is buy_property_to_block_opponent_group_completion. "
                    "Choose BUY_PROPERTY rather than START_AUCTION."
                ),
            },
        )
    if case.name == "purchase_blocks_opponent_utility_group_with_thin_cash":
        return (
            {
                "id": "live-strategy-buy-utility-to-block-opponent-set",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Ada owns property_electric_company, "
                    "Grace has $300, and Grace landed on unowned property_water_works. "
                    "Buying Water Works for $150 leaves $150 and blocks Ada from completing "
                    "Utilities. purchase_guidance recommendation is "
                    "buy_property_to_block_opponent_group_completion. Choose BUY_PROPERTY "
                    "rather than START_AUCTION."
                ),
            },
        )
    if case.name == "multiple_near_monopolies_prioritizes_orange_negotiation":
        return (
            {
                "id": "live-strategy-prioritize-orange-near-monopoly",
                "source": "strategy-smoke",
                "text": (
                    "For this open_negotiation decision, Grace has two possible color-group "
                    "completion trades: Connecticut Avenue for Light Blue and Tennessee Avenue "
                    "for Orange. Prioritize Tennessee Avenue because Orange has stronger "
                    "developed-rent pressure. Open negotiation with Ada for property_tennessee_avenue."
                ),
            },
        )
    if case.name == "railroad_near_set_negotiation":
        return (
            {
                "id": "live-strategy-railroad-near-set-negotiation",
                "source": "strategy-smoke",
                "text": (
                    "For this open_negotiation decision, Grace owns Reading Railroad, "
                    "Pennsylvania Railroad, and B&O Railroad while Ada owns "
                    "property_short_line_railroad. Open negotiation with Ada for "
                    "property_short_line_railroad to complete all four railroads."
                ),
            },
        )
    if case.name == "utility_near_set_negotiation":
        return (
            {
                "id": "live-strategy-utility-near-set-negotiation",
                "source": "strategy-smoke",
                "text": (
                    "For this open_negotiation decision, Grace owns "
                    "property_electric_company while Ada owns property_water_works. "
                    "Open negotiation with Ada for Water Works to complete Utilities, "
                    "with a credible cash offer range from $150 to $225."
                ),
            },
        )
    if case.name == "dark_blue_near_monopoly_negotiation":
        return (
            {
                "id": "live-strategy-dark-blue-near-monopoly-negotiation",
                "source": "strategy-smoke",
                "text": (
                    "For this open_negotiation decision, Grace owns property_boardwalk while "
                    "Ada owns property_park_place. Open negotiation with Ada for Park Place "
                    "to complete Dark Blue, with a credible cash offer range from $350 to $525."
                ),
            },
        )
    if case.name == "block_opponent_orange_near_monopoly_negotiation":
        return (
            {
                "id": "live-strategy-block-opponent-orange-near-monopoly",
                "source": "strategy-smoke",
                "text": (
                    "For this open_negotiation decision, Ada owns St. James Place and New York Avenue "
                    "while Linus owns property_tennessee_avenue. Grace should negotiate with Linus for "
                    "Tennessee Avenue to block Ada from completing Orange. Open negotiation with Linus."
                ),
            },
        )
    if case.name == "block_opponent_railroad_near_set_negotiation":
        return (
            {
                "id": "live-strategy-block-opponent-railroad-near-set",
                "source": "strategy-smoke",
                "text": (
                    "For this open_negotiation decision, Ada owns Reading Railroad, "
                    "Pennsylvania Railroad, and B&O Railroad while Linus owns "
                    "property_short_line_railroad. Grace should negotiate with Linus for "
                    "Short Line Railroad to block Ada from completing all four railroads. "
                    "Open negotiation with Linus."
                ),
            },
        )
    if case.name == "block_opponent_utility_near_set_negotiation":
        return (
            {
                "id": "live-strategy-block-opponent-utility-near-set",
                "source": "strategy-smoke",
                "text": (
                    "For this open_negotiation decision, Ada owns property_electric_company "
                    "while Linus owns property_water_works. Grace should negotiate with Ada "
                    "for Electric Company to block Linus from completing Utilities. Open "
                    "negotiation with Ada."
                ),
            },
        )
    if case.name == "multiple_monopolies_prioritizes_orange_development":
        return (
            {
                "id": "live-strategy-prioritize-orange-development",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns both Brown and Orange monopolies "
                    "with enough cash to build. BUY_HOUSE is legal on both groups, but "
                    "Orange has the highest development_priority_score and stronger rent "
                    "pressure. Within Orange, property_new_york_avenue has the highest "
                    "marginal_rent_gain for the next house. Choose BUY_HOUSE on "
                    "property_new_york_avenue before rolling."
                ),
            },
        )
    if case.name == "dark_blue_monopoly_development":
        return (
            {
                "id": "live-strategy-develop-dark-blue",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns Park Place and Boardwalk with "
                    "enough cash to build. BUY_HOUSE is legal on both dark-blue properties, "
                    "and property_boardwalk has the higher marginal_rent_gain for the first "
                    "house. Choose BUY_HOUSE on property_boardwalk before rolling."
                ),
            },
        )
    if case.name == "low_cash_defers_monopoly_development":
        return (
            {
                "id": "live-strategy-defer-low-cash-development",
                "source": "strategy-smoke",
                "text": (
                    "For this action_decision, Grace owns the Orange monopoly but has only "
                    "$350 cash. Each BUY_HOUSE action costs $100 and leaves $250, below "
                    "the $300 cash reserve floor. BUY_HOUSE remains legally available, but "
                    "action_selection_guidance defers every development opportunity. Choose "
                    "ROLL_DICE instead of BUY_HOUSE."
                ),
            },
        )
    if case.decision_type == "counteroffer":
        if case.name == "railroad_overpriced_deal_counteroffer":
            return (
                {
                    "id": "live-strategy-counter-overpriced-railroad-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this counteroffer decision, Grace can receive Short Line "
                        "Railroad from Ada, but Ada's $400 ask exceeds the $300 strategic "
                        "value ceiling. Use counteroffer_guidance.counteroffer_payload_template "
                        "as the base counteroffer so Grace offers exactly $300 while still "
                        "asking for property_short_line_railroad."
                    ),
                },
            )
        if case.name == "utility_overpriced_deal_counteroffer":
            return (
                {
                    "id": "live-strategy-counter-overpriced-utility-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this counteroffer decision, Grace can receive Water Works from "
                        "Ada, but Ada's $300 ask exceeds the $225 strategic value ceiling. "
                        "Use counteroffer_guidance.counteroffer_payload_template as the base "
                        "counteroffer so Grace offers exactly $225 while still asking for "
                        "property_water_works."
                    ),
                },
            )
        if case.name == "orange_boardwalk_swap_deal_counteroffer":
            return (
                {
                    "id": "live-strategy-counter-boardwalk-swap-monopoly-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this counteroffer decision, Grace can receive "
                        "property_tennessee_avenue from Ada, but the proposal asks Grace "
                        "to transfer property_boardwalk. Boardwalk is worth $400, which "
                        "exceeds the $270 strategic value ceiling for completing Orange. "
                        "Use counteroffer_guidance.counteroffer_payload_template as the "
                        "base counteroffer so Grace offers $270 cash, asks for Tennessee "
                        "Avenue, and does not include property_boardwalk."
                    ),
                },
            )
        return (
            {
                "id": "live-strategy-counter-overpriced-monopoly-completer",
                "source": "strategy-smoke",
                "text": (
                    "For this counteroffer decision, Grace can receive "
                    "property_tennessee_avenue from Ada, but Ada's $400 ask exceeds "
                    "the $270 strategic value ceiling. Use "
                    "counteroffer_guidance.counteroffer_payload_template as the base "
                    "counteroffer so Grace offers no more than $270 while still asking "
                    "for Tennessee Avenue."
                ),
            },
        )
    if case.decision_type == "accept_reject":
        if case.name == "orange_good_deal_acceptance":
            return (
                {
                    "id": "live-strategy-accept-fair-monopoly-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Grace can receive property_tennessee_avenue "
                        "from Ada for $220. Tennessee Avenue completes Grace's Orange monopoly, the "
                        "cash price is below the $270 strategic ceiling, and cash_after_payment remains "
                        "$1280. Accept the deal."
                    ),
                },
            )
        if case.name == "orange_cash_return_deal_acceptance":
            return (
                {
                    "id": "live-strategy-accept-net-fair-orange-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Grace pays $400 but receives "
                        "$200 back and property_tennessee_avenue, so the net cash "
                        "payment is $200. Tennessee Avenue completes Orange within the "
                        "$270 value ceiling and preserves liquidity. Accept the deal."
                    ),
                },
            )
        if case.name == "railroad_good_deal_acceptance":
            return (
                {
                    "id": "live-strategy-accept-fair-railroad-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Ada offers Short Line Railroad "
                        "to Grace for $250. Grace owns the other three railroads, so this "
                        "deal completes Grace's railroad set within the $300 strategic "
                        "value ceiling and preserves liquidity. Accept the deal."
                    ),
                },
            )
        if case.name == "utility_good_deal_acceptance":
            return (
                {
                    "id": "live-strategy-accept-fair-utility-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Ada offers Water Works to Grace "
                        "for $187. Grace owns Electric Company, so this deal completes "
                        "Grace's utility set within the $225 strategic value ceiling and "
                        "preserves liquidity. Accept the deal."
                    ),
                },
            )
        if case.name == "railroad_bad_deal_rejection":
            return (
                {
                    "id": "live-strategy-reject-lowball-railroad-enabler",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Ada owns Short Line Railroad. "
                        "Reject a proposal that gives Short Line Railroad to Grace for only "
                        "$1 because it completes Grace's railroad set and gives Ada far "
                        "below the $300 strategic floor."
                    ),
                },
            )
        if case.name == "utility_bad_deal_rejection":
            return (
                {
                    "id": "live-strategy-reject-lowball-utility-enabler",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Ada owns Water Works. Reject a "
                        "proposal that gives Water Works to Grace for only $1 because it "
                        "completes Grace's utility set and gives Ada far below the $225 "
                        "strategic floor."
                    ),
                },
            )
        if case.name == "orange_overpriced_deal_rejection":
            return (
                {
                    "id": "live-strategy-reject-overpriced-monopoly-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Grace can receive property_tennessee_avenue "
                        "from Ada for $400. Tennessee Avenue completes Grace's Orange monopoly, but "
                        "$400 exceeds the $270 strategic value ceiling. Reject the deal."
                    ),
                },
            )
        if case.name == "orange_cash_draining_deal_rejection":
            return (
                {
                    "id": "live-strategy-reject-cash-draining-monopoly-completer",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Grace has only $300 and can receive "
                        "property_tennessee_avenue from Ada for $220. Tennessee Avenue completes "
                        "Grace's Orange monopoly, but cash_after_payment is $80, below the $100 "
                        "group completion cash floor. Reject the deal."
                    ),
                },
            )
        if case.name == "orange_mutual_completion_deal_rejection":
            return (
                {
                    "id": "live-strategy-reject-mutual-stronger-orange-completion",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Ada offers Connecticut Avenue "
                        "and $150 for Tennessee Avenue. Grace would complete Light Blue, "
                        "but Ada would complete Orange, which has the stronger completion "
                        "priority. Reject because this trade gives Ada the stronger completed set."
                    ),
                },
            )
        if case.name == "orange_cash_round_trip_deal_rejection":
            return (
                {
                    "id": "live-strategy-reject-low-net-cash-orange-enabler",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Ada offers $320 for "
                        "property_tennessee_avenue, but Grace also sends $200 back, "
                        "so Grace's net compensation is only $120. Tennessee Avenue "
                        "completes Ada's Orange set, and the net compensation is below "
                        "the $270 strategic floor. Reject the deal."
                    ),
                },
            )
        if case.name == "orange_monopoly_breakup_deal_rejection":
            return (
                {
                    "id": "live-strategy-reject-monopoly-breakup",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Grace owns the full Orange monopoly. "
                        "Ada offers $300 for property_tennessee_avenue, but accepting breaks "
                        "Grace's complete Orange group and the offer is below the $540 monopoly "
                        "breakup cash value floor. Reject the deal."
                    ),
                },
            )
        if case.name == "orange_monopoly_breakup_property_swap_rejection":
            return (
                {
                    "id": "live-strategy-reject-monopoly-breakup-property-swap",
                    "source": "strategy-smoke",
                    "text": (
                        "For this accept_reject decision, Grace owns the full Orange monopoly. "
                        "Ada offers property_baltic_avenue for property_tennessee_avenue, "
                        "but Baltic Avenue is worth only $60 and accepting breaks Grace's "
                        "complete Orange group below the $540 breakup cash value floor. "
                        "Reject the deal."
                    ),
                },
            )
        return (
            {
                "id": "live-strategy-reject-lowball-monopoly-enabler",
                "source": "strategy-smoke",
                "text": (
                    "For this accept_reject decision, Ada owns property_tennessee_avenue. "
                    "Reject a proposal that gives Tennessee Avenue to Grace for only $1 because "
                    "it completes Grace's Orange monopoly and gives Ada far below strategic value."
                ),
            },
        )
    if case.decision_type != "deal_proposal":
        return ()
    if case.name == "dark_blue_near_monopoly_deal_proposal":
        return (
            {
                "id": "live-strategy-dark-blue-deal-shape",
                "source": "strategy-smoke",
                "text": (
                    "For this deal_proposal, propose structured_deal terms containing "
                    "immediate_cash_transfer from Grace to Ada and immediate_property_transfer "
                    "of property_park_place from Ada to Grace. Offer cash between $350 and $525. "
                    "Use deal_proposal_guidance.deal_payload_template as the base proposal. "
                    "Do not trade away property_boardwalk."
                ),
            },
        )
    if case.name == "railroad_near_set_deal_proposal":
        return (
            {
                "id": "live-strategy-railroad-deal-shape",
                "source": "strategy-smoke",
                "text": (
                    "For this deal_proposal, propose structured_deal terms containing "
                    "immediate_cash_transfer from Grace to Ada and immediate_property_transfer "
                    "of property_short_line_railroad from Ada to Grace. Offer cash between "
                    "$200 and $300. Use deal_proposal_guidance.deal_payload_template as the "
                    "base proposal. Do not trade away property_reading_railroad, "
                    "property_pennsylvania_railroad, or property_b_and_o_railroad."
                ),
            },
        )
    if case.name == "utility_near_set_deal_proposal":
        return (
            {
                "id": "live-strategy-utility-deal-shape",
                "source": "strategy-smoke",
                "text": (
                    "For this deal_proposal, propose structured_deal terms containing "
                    "immediate_cash_transfer from Grace to Ada and immediate_property_transfer "
                    "of property_water_works from Ada to Grace. Offer cash between $150 and $225. "
                    "Use deal_proposal_guidance.deal_payload_template as the base proposal. "
                    "Do not trade away property_electric_company."
                ),
            },
        )
    if case.name == "orange_cash_limited_deal_proposal":
        return (
            {
                "id": "live-strategy-cash-limited-orange-deal-shape",
                "source": "strategy-smoke",
                "text": (
                    "For this deal_proposal, Grace has $500 and must preserve a $300 "
                    "cash reserve, so current_cash_budget_ceiling is $200. Use "
                    "deal_proposal_guidance.deal_payload_template as the base proposal "
                    "and offer $190 for property_tennessee_avenue, with no cash term "
                    "above $200."
                ),
            },
        )
    return (
        {
            "id": "live-strategy-deal-shape",
            "source": "strategy-smoke",
            "text": (
                "For this deal_proposal, propose structured_deal terms containing "
                "immediate_cash_transfer from Grace to Ada and immediate_property_transfer "
                "of property_tennessee_avenue from Ada to Grace. Offer cash between $180 and $270. "
                "Use deal_proposal_guidance.deal_payload_template as the base proposal. "
                "Set deal.terms to a valid JSON string; do not prefix it with structured_deal: "
                "or any other label."
            ),
        },
    )


BAD_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b302")
GOOD_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b303")
OVERPRICED_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b304")
CASH_DRAINING_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b305")
BREAKUP_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b306")
FAIR_RAILROAD_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b307")
FAIR_UTILITY_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b308")
LOWBALL_RAILROAD_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b309")
LOWBALL_UTILITY_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b30a")
OVERPRICED_RAILROAD_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b30b")
OVERPRICED_UTILITY_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b30c")
BOARDWALK_SWAP_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b30d")
MUTUAL_COMPLETION_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b30e")
BREAKUP_PROPERTY_SWAP_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b30f")
CASH_RETURN_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b310")
CASH_ROUND_TRIP_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b311")


def _bad_deal_terms() -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-lowball-tennessee",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-lowball-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": 1,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-lowball-tennessee-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
        ],
    }


def _good_deal_terms(*, amount: int = 220) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-fair-tennessee",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-fair-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": amount,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-fair-tennessee-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
        ],
    }


def _cash_return_tennessee_completion_terms() -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-cash-return-tennessee-completion",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-cash-return-gross-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": 400,
            },
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-cash-returned",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "amount": 200,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-cash-return-tennessee-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
        ],
    }


def _cash_round_trip_tennessee_completion_terms() -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-cash-round-trip-tennessee-completion",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-cash-round-trip-gross-cash",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "amount": 320,
            },
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-cash-round-trip-returned-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": 200,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-cash-round-trip-tennessee-transfer",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
        ],
    }


def _fair_railroad_deal_terms(*, amount: int = 250) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-fair-railroad-completer",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-fair-railroad-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": amount,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-fair-short-line-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_short_line_railroad",
            },
        ],
    }


def _fair_utility_deal_terms(*, amount: int = 187) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-fair-utility-completer",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-fair-utility-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": amount,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-fair-water-works-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_water_works",
            },
        ],
    }


def _lowball_railroad_deal_terms(*, amount: int = 1) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-lowball-railroad-completer",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-lowball-railroad-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": amount,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-lowball-short-line-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_short_line_railroad",
            },
        ],
    }


def _lowball_utility_deal_terms(*, amount: int = 1) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-lowball-utility-completer",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-lowball-utility-cash",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": amount,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-lowball-water-works-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_water_works",
            },
        ],
    }


def _boardwalk_for_tennessee_deal_terms() -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-boardwalk-for-tennessee-completion",
        "terms": [
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-boardwalk-transfer",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "property_id": "property_boardwalk",
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-tennessee-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
        ],
    }


def _mutual_light_blue_for_orange_completion_terms() -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-mutual-light-blue-for-orange-completion",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-mutual-sweetener-cash",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "amount": 150,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-connecticut-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_connecticut_avenue",
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-mutual-tennessee-transfer",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
        ],
    }


def _monopoly_breakup_terms(*, amount: int) -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-orange-breakup",
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-breakup-cash",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "amount": amount,
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-breakup-tennessee-transfer",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
        ],
    }


def _monopoly_breakup_property_swap_terms() -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms_hash": "live-strategy-orange-breakup-baltic-swap",
        "terms": [
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-breakup-tennessee-swap-transfer",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "property_id": "property_tennessee_avenue",
            },
            {
                "kind": "immediate_property_transfer",
                "instrument_id": "live-strategy-breakup-baltic-transfer",
                "from_player_id": str(OTHER_PLAYER_ID),
                "to_player_id": str(AI_PLAYER_ID),
                "property_id": "property_baltic_avenue",
            },
        ],
    }


def _railroad_purchase_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-railroad-purchase")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[0]["position"] = 5
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
        }
    )


def _boardwalk_purchase_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-boardwalk-purchase")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1300
    players[0]["position"] = 39
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
        }
    )


def _railroad_purchase_completes_set_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-railroad-purchase-completes-set")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 400
    players[0]["position"] = 35
    owned_property_ids = {
        "property_reading_railroad",
        "property_pennsylvania_railroad",
        "property_b_and_o_railroad",
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id in owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
        }
    )


def _utility_purchase_completes_set_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-utility-purchase-completes-set")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 300
    players[0]["position"] = 28
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id == "property_electric_company"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
        }
    )


def _purchase_completes_color_group_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-purchase-completes-color-group")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 400
    players[0]["position"] = 18
    owned_property_ids = {"property_st_james_place", "property_new_york_avenue"}
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id in owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
        }
    )


def _purchase_blocks_opponent_color_group_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-purchase-blocks-opponent-color-group")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 400
    players[0]["position"] = 14
    opponent_owned_property_ids = {"property_st_charles_place", "property_states_avenue"}
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(OTHER_PLAYER_ID),
        }
        if item.property_id in opponent_owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
        }
    )


def _purchase_blocks_opponent_utility_group_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-purchase-blocks-opponent-utility-group")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 300
    players[0]["position"] = 28
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(OTHER_PLAYER_ID),
        }
        if item.property_id == "property_electric_company"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
        }
    )


def _healthy_cash_mortgage_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-avoid-mortgage")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 900
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id == "property_b_and_o_railroad"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _healthy_cash_unmortgage_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-unmortgage")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 900
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
            "mortgaged": True,
        }
        if item.property_id == "property_b_and_o_railroad"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _jail_card_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-jail-card")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0].update(
        {
            "cash": 900,
            "position": 10,
            "in_jail": True,
            "jail_turns": 1,
            "get_out_of_jail_card_ids": ("card_community_get_out_of_jail",),
        }
    )
    ownership = [item.model_dump(mode="python") for item in state.property_ownership]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _active_debt_mortgage_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-debt-mortgage")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 0
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id == "property_b_and_o_railroad"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PAYMENT_RESOLUTION,
            },
            "active_payment": {
                "debtor_id": str(AI_PLAYER_ID),
                "creditor_id": str(OTHER_PLAYER_ID),
                "amount_owed": 75,
                "amount_paid": 0,
                "reason": "rent",
                "negotiation_allowed": False,
            },
        }
    )


def _active_debt_settlement_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-debt-settlement")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 75
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id == "property_b_and_o_railroad"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PAYMENT_RESOLUTION,
            },
            "active_payment": {
                "debtor_id": str(AI_PLAYER_ID),
                "creditor_id": str(OTHER_PLAYER_ID),
                "amount_owed": 75,
                "amount_paid": 0,
                "reason": "rent",
                "negotiation_allowed": False,
            },
        }
    )


def _active_debt_sell_house_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-debt-sell-house")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 0
    owned_property_ids = {
        "property_oriental_avenue",
        "property_vermont_avenue",
        "property_connecticut_avenue",
        "property_b_and_o_railroad",
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
            "houses": 1 if item.property_id == "property_oriental_avenue" else 0,
        }
        if item.property_id in owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "bank_inventory": {
                **state.bank_inventory.model_dump(mode="python"),
                "houses": 31,
            },
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PAYMENT_RESOLUTION,
            },
            "active_payment": {
                "debtor_id": str(AI_PLAYER_ID),
                "creditor_id": str(OTHER_PLAYER_ID),
                "amount_owed": 25,
                "amount_paid": 0,
                "reason": "rent",
                "negotiation_allowed": False,
            },
        }
    )


def _auction_bid_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-bid")
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_virginia_avenue",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 50,
                "passed_player_ids": [],
            },
        }
    )


def _auction_pass_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-pass")
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_virginia_avenue",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 1000,
                "passed_player_ids": [],
            },
        }
    )


def _auction_cash_reserve_pass_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-cash-reserve-pass")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 220
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_virginia_avenue",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 50,
                "passed_player_ids": [],
            },
        }
    )


def _auction_color_group_completion_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-complete-color-group")
    owned_property_ids = {"property_st_james_place", "property_new_york_avenue"}
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id in owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_tennessee_avenue",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 180,
                "passed_player_ids": [],
            },
        }
    )


def _auction_railroad_group_completion_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-complete-railroad-group")
    owned_property_ids = {
        "property_reading_railroad",
        "property_pennsylvania_railroad",
        "property_b_and_o_railroad",
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id in owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_short_line_railroad",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 200,
                "passed_player_ids": [],
            },
        }
    )


def _auction_utility_group_completion_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-complete-utility-group")
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
        }
        if item.property_id == "property_electric_company"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_water_works",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 150,
                "passed_player_ids": [],
            },
        }
    )


def _auction_block_opponent_color_group_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-block-opponent-color-group")
    opponent_owned_property_ids = {"property_st_charles_place", "property_states_avenue"}
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(OTHER_PLAYER_ID),
        }
        if item.property_id in opponent_owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_virginia_avenue",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 160,
                "passed_player_ids": [],
            },
        }
    )


def _auction_block_opponent_railroad_group_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-block-opponent-railroad-group")
    opponent_owned_property_ids = {
        "property_reading_railroad",
        "property_pennsylvania_railroad",
        "property_b_and_o_railroad",
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(OTHER_PLAYER_ID),
        }
        if item.property_id in opponent_owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_short_line_railroad",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 200,
                "passed_player_ids": [],
            },
        }
    )


def _auction_block_opponent_utility_group_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-auction-block-opponent-utility-group")
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(OTHER_PLAYER_ID),
        }
        if item.property_id == "property_electric_company"
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "property_ownership": ownership,
            "turn": {
                **state.turn.model_dump(mode="python"),
                "phase": TurnPhase.PURCHASE_OR_AUCTION,
            },
            "active_auction": {
                "property_id": "property_water_works",
                "high_bidder_id": str(OTHER_PLAYER_ID),
                "high_bid_amount": 150,
                "passed_player_ids": [],
            },
        }
    )


def _orange_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-orange-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 3000
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in ORANGE_PROPERTY_IDS
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _orange_monopoly_and_opponent_baltic_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-orange-monopoly-opponent-baltic")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 3000
    owner_by_property_id = {
        "property_st_james_place": str(AI_PLAYER_ID),
        "property_tennessee_avenue": str(AI_PLAYER_ID),
        "property_new_york_avenue": str(AI_PLAYER_ID),
        "property_baltic_avenue": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _dark_blue_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-dark-blue-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 3000
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in DARK_BLUE_PROPERTY_IDS
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _brown_and_orange_monopolies_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-multiple-monopoly-development")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 3000
    owned_property_ids = BROWN_PROPERTY_IDS | ORANGE_PROPERTY_IDS
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owned_property_ids
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _low_cash_orange_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-low-cash-orange-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 350
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": str(AI_PLAYER_ID),
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in ORANGE_PROPERTY_IDS
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _orange_near_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-orange-near-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_st_james_place": str(AI_PLAYER_ID),
        "property_new_york_avenue": str(AI_PLAYER_ID),
        "property_tennessee_avenue": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _cash_limited_orange_near_monopoly_state(game_id: UUID) -> GameState:
    state = _orange_near_monopoly_state(game_id)
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 500
    ownership = [item.model_dump(mode="python") for item in state.property_ownership]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _cash_return_orange_near_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-cash-return-orange-near-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_st_james_place": str(AI_PLAYER_ID),
        "property_new_york_avenue": str(AI_PLAYER_ID),
        "property_tennessee_avenue": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _orange_near_monopoly_with_boardwalk_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-orange-near-monopoly-with-boardwalk")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_st_james_place": str(AI_PLAYER_ID),
        "property_new_york_avenue": str(AI_PLAYER_ID),
        "property_boardwalk": str(AI_PLAYER_ID),
        "property_tennessee_avenue": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _railroad_near_set_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-railroad-near-set")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_reading_railroad": str(AI_PLAYER_ID),
        "property_pennsylvania_railroad": str(AI_PLAYER_ID),
        "property_b_and_o_railroad": str(AI_PLAYER_ID),
        "property_short_line_railroad": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _utility_near_set_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-utility-near-set")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_electric_company": str(AI_PLAYER_ID),
        "property_water_works": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _dark_blue_near_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-dark-blue-near-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_boardwalk": str(AI_PLAYER_ID),
        "property_park_place": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _low_cash_orange_near_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-low-cash-orange-near-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 300
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_st_james_place": str(AI_PLAYER_ID),
        "property_new_york_avenue": str(AI_PLAYER_ID),
        "property_tennessee_avenue": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _multiple_near_monopolies_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-multiple-near-monopolies")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_oriental_avenue": str(AI_PLAYER_ID),
        "property_vermont_avenue": str(AI_PLAYER_ID),
        "property_connecticut_avenue": str(OTHER_PLAYER_ID),
        "property_st_james_place": str(AI_PLAYER_ID),
        "property_new_york_avenue": str(AI_PLAYER_ID),
        "property_tennessee_avenue": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _light_blue_near_set_and_opponent_orange_near_set_state(game_id: UUID) -> GameState:
    state = _base_state(
        game_id,
        seed="live-strategy-light-blue-near-set-opponent-orange-near-set",
    )
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    owner_by_property_id = {
        "property_oriental_avenue": str(AI_PLAYER_ID),
        "property_vermont_avenue": str(AI_PLAYER_ID),
        "property_connecticut_avenue": str(OTHER_PLAYER_ID),
        "property_tennessee_avenue": str(AI_PLAYER_ID),
        "property_st_james_place": str(OTHER_PLAYER_ID),
        "property_new_york_avenue": str(OTHER_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _opponent_orange_near_monopoly_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-block-opponent-orange-near-monopoly")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    players[2]["cash"] = 1500
    owner_by_property_id = {
        "property_st_james_place": str(OTHER_PLAYER_ID),
        "property_new_york_avenue": str(OTHER_PLAYER_ID),
        "property_tennessee_avenue": str(THIRD_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _opponent_railroad_near_set_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-block-opponent-railroad-near-set")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    players[2]["cash"] = 1500
    owner_by_property_id = {
        "property_reading_railroad": str(OTHER_PLAYER_ID),
        "property_pennsylvania_railroad": str(OTHER_PLAYER_ID),
        "property_b_and_o_railroad": str(OTHER_PLAYER_ID),
        "property_short_line_railroad": str(THIRD_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _opponent_utility_near_set_state(game_id: UUID) -> GameState:
    state = _base_state(game_id, seed="live-strategy-block-opponent-utility-near-set")
    players = [player.model_dump(mode="python") for player in state.players]
    players[0]["cash"] = 1500
    players[1]["cash"] = 1500
    players[2]["cash"] = 1500
    owner_by_property_id = {
        "property_electric_company": str(OTHER_PLAYER_ID),
        "property_water_works": str(THIRD_PLAYER_ID),
    }
    ownership = [
        {
            **item.model_dump(mode="python"),
            "owner_id": owner_by_property_id[item.property_id],
            "mortgaged": False,
            "houses": 0,
            "hotel": False,
        }
        if item.property_id in owner_by_property_id
        else item.model_dump(mode="python")
        for item in state.property_ownership
    ]
    return _state_with_debug_values(state, players=players, ownership=ownership)


def _base_state(game_id: UUID, *, seed: str) -> GameState:
    return create_initial_game_state(
        seed=seed,
        game_id=str(game_id),
        players=(
            PlayerSetup(id=str(AI_PLAYER_ID), name="Grace", kind="ai"),
            PlayerSetup(id=str(OTHER_PLAYER_ID), name="Ada", kind="ai"),
            PlayerSetup(id=str(THIRD_PLAYER_ID), name="Linus", kind="ai"),
            PlayerSetup(id=str(FOURTH_PLAYER_ID), name="Marie", kind="ai"),
        ),
    )


def _state_with_debug_values(
    state: GameState,
    *,
    players: list[dict[str, Any]],
    ownership: list[dict[str, Any]],
) -> GameState:
    return GameState.model_validate(
        {
            **state.model_dump(mode="python"),
            "players": players,
            "property_ownership": ownership,
            "turn": {**state.turn.model_dump(mode="python"), "phase": TurnPhase.START_TURN},
        }
    )


def _read_last_message(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
