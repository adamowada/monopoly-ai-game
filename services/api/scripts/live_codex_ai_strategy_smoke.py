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
            print(json.dumps({"case": case.name, "status": "failed", "message": str(exc)}, sort_keys=True))
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
            name="purchase_completes_color_group_with_thin_cash",
            game_id=UUID("00000000-0000-0000-0000-00000000b215"),
            decision_type="action_decision",
            state_factory=_purchase_completes_color_group_state,
            verifier=_verify_purchase_completes_color_group_with_thin_cash,
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
            name="orange_monopoly_development",
            game_id=UUID("00000000-0000-0000-0000-00000000b201"),
            decision_type="action_decision",
            state_factory=_orange_monopoly_state,
            verifier=_verify_orange_monopoly_development,
        ),
        StrategySmokeCase(
            name="multiple_monopolies_prioritizes_orange_development",
            game_id=UUID("00000000-0000-0000-0000-00000000b218"),
            decision_type="action_decision",
            state_factory=_brown_and_orange_monopolies_state,
            verifier=_verify_orange_monopoly_development,
        ),
        StrategySmokeCase(
            name="orange_near_monopoly_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b202"),
            decision_type="open_negotiation",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_near_monopoly_negotiation,
        ),
        StrategySmokeCase(
            name="multiple_near_monopolies_prioritizes_orange_negotiation",
            game_id=UUID("00000000-0000-0000-0000-00000000b216"),
            decision_type="open_negotiation",
            state_factory=_multiple_near_monopolies_state,
            verifier=_verify_orange_near_monopoly_negotiation,
        ),
        StrategySmokeCase(
            name="orange_near_monopoly_deal_proposal",
            game_id=UUID("00000000-0000-0000-0000-00000000b203"),
            decision_type="deal_proposal",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_near_monopoly_deal_proposal,
        ),
        StrategySmokeCase(
            name="orange_bad_deal_rejection",
            game_id=UUID("00000000-0000-0000-0000-00000000b204"),
            decision_type="accept_reject",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_bad_deal_rejection,
            actor_player_id=OTHER_PLAYER_ID,
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
        negotiation_id=NEGOTIATION_ID if case.decision_type in {"deal_proposal", "accept_reject"} else None,
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
        final_output = _read_last_message(output_last_message_path) or parsed_events.final_assistant_output
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


def _verify_purchase_completes_color_group_with_thin_cash(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_PROPERTY", f"expected BUY_PROPERTY, got {action.get('type')}"
    assert payload.get("property_id") == "property_tennessee_avenue"
    if "price" in payload:
        assert payload.get("price") == 180


def _verify_healthy_cash_avoids_mortgage(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "ROLL_DICE", f"expected ROLL_DICE, got {action.get('type')}"


def _verify_active_debt_uses_mortgage(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))

    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "MORTGAGE_PROPERTY", f"expected MORTGAGE_PROPERTY, got {action.get('type')}"
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
    assert action.get("type") == "UNMORTGAGE_PROPERTY", f"expected UNMORTGAGE_PROPERTY, got {action.get('type')}"
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
    assert 52 <= amount <= 160, f"expected a deliberate bid above floor and within valuation, got {amount}"


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


def _verify_orange_monopoly_development(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_HOUSE", f"expected BUY_HOUSE, got {action.get('type')}"
    assert payload.get("property_id") == "property_new_york_avenue"
    assert payload.get("cost") == 100


def _verify_orange_near_monopoly_negotiation(parsed: dict[str, Any]) -> None:
    negotiation = _dict(parsed.get("negotiation"))
    participant_player_ids = [str(player_id) for player_id in negotiation.get("participant_player_ids", [])]
    context = _dict(negotiation.get("context"))
    assert parsed.get("decision_type") == "open_negotiation"
    assert participant_player_ids == [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)]
    assert context.get("target_property_id") == "property_tennessee_avenue"
    assert context.get("target_owner_id") == str(OTHER_PLAYER_ID)


def _verify_orange_near_monopoly_deal_proposal(parsed: dict[str, Any]) -> None:
    deal = _dict(parsed.get("deal"))
    terms = _dict(deal.get("terms"))
    instruments = [_dict(term) for term in terms.get("terms", [])]
    cash_terms = [term for term in instruments if term.get("kind") == "immediate_cash_transfer"]
    property_terms = [term for term in instruments if term.get("kind") == "immediate_property_transfer"]

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


def _verify_orange_bad_deal_rejection(parsed: dict[str, Any]) -> None:
    accept_reject = _dict(parsed.get("accept_reject"))

    assert parsed.get("decision_type") == "accept_reject"
    assert parsed.get("negotiation_id") == str(NEGOTIATION_ID)
    assert accept_reject.get("deal_id") == str(BAD_DEAL_ID)
    assert accept_reject.get("decision") == "reject", f"expected reject, got {accept_reject.get('decision')}"


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
            "term_kinds": [term.get("kind") for term in terms.get("terms", []) if isinstance(term, dict)],
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
    if case.decision_type != "deal_proposal":
        return {}
    deal_terms_template = _deal_terms_template()
    return {
        "mode": "live_strategy_smoke",
        "negotiation_id": str(NEGOTIATION_ID),
        "requested_decision": "Propose a structured deal to acquire Tennessee Avenue.",
        "deal_terms_template": deal_terms_template,
        "deal_terms_json_string_example": json.dumps(
            deal_terms_template,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _negotiations(case: StrategySmokeCase) -> tuple[dict[str, Any], ...]:
    if case.decision_type not in {"deal_proposal", "accept_reject"}:
        return ()
    return (
        {
            "id": str(NEGOTIATION_ID),
            "opened_by_player_id": str(AI_PLAYER_ID),
            "status": "active",
            "phase": "START_TURN",
            "round_number": 1,
            "context": {
                "participant_player_ids": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
                "current_deal_id": str(BAD_DEAL_ID) if case.decision_type == "accept_reject" else None,
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
    if case.decision_type == "accept_reject":
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
    if case.decision_type != "deal_proposal":
        return ()
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
    if case.decision_type != "accept_reject":
        return ()
    return (
        {
            "id": str(BAD_DEAL_ID),
            "negotiation_id": str(NEGOTIATION_ID),
            "proposed_by_player_id": str(AI_PLAYER_ID),
            "parent_deal_id": None,
            "status": "proposed",
            "version": 1,
            "terms": _bad_deal_terms(),
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
    if case.decision_type == "accept_reject":
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
    return (
        {
            "id": "live-strategy-deal-shape",
            "source": "strategy-smoke",
            "text": (
                "For this deal_proposal, propose structured_deal terms containing "
                "immediate_cash_transfer from Grace to Ada and immediate_property_transfer "
                "of property_tennessee_avenue from Ada to Grace. Offer cash between $180 and $270. "
                "Set deal.terms to a valid JSON string that decodes to the provided "
                "deal_terms_template; do not prefix it with structured_deal: or any other label."
            ),
        },
    )


def _deal_terms_template() -> dict[str, Any]:
    return {
        "kind": "structured_deal",
        "deal_schema_version": 1,
        "participants": [str(AI_PLAYER_ID), str(OTHER_PLAYER_ID)],
        "terms": [
            {
                "kind": "immediate_cash_transfer",
                "instrument_id": "live-strategy-cash-for-tennessee",
                "from_player_id": str(AI_PLAYER_ID),
                "to_player_id": str(OTHER_PLAYER_ID),
                "amount": 240,
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


BAD_DEAL_ID = UUID("00000000-0000-0000-0000-00000000b302")


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
