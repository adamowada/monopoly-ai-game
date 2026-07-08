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


@dataclass(frozen=True, slots=True)
class StrategySmokeCase:
    name: str
    game_id: UUID
    decision_type: str
    state_factory: Callable[[UUID], GameState]
    verifier: Callable[[dict[str, Any]], None]


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
            name="orange_monopoly_development",
            game_id=UUID("00000000-0000-0000-0000-00000000b201"),
            decision_type="action_decision",
            state_factory=_orange_monopoly_state,
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
            name="orange_near_monopoly_deal_proposal",
            game_id=UUID("00000000-0000-0000-0000-00000000b203"),
            decision_type="deal_proposal",
            state_factory=_orange_near_monopoly_state,
            verifier=_verify_orange_near_monopoly_deal_proposal,
        ),
    )


def _run_strategy_case(case: StrategySmokeCase) -> dict[str, Any]:
    state = case.state_factory(case.game_id)
    pack = build_ai_context_pack(
        state,
        player_id=str(AI_PLAYER_ID),
        decision_type=case.decision_type,
        caller_request_context=_caller_request_context(case),
        negotiations=_negotiations(case),
        negotiation_messages=_negotiation_messages(case),
        rule_snippets=_strategy_rule_snippets(case),
    )
    request = CodexExecAIDecisionRequest(
        game_id=case.game_id,
        player_id=AI_PLAYER_ID,
        decision_type=case.decision_type,
        negotiation_id=NEGOTIATION_ID if case.decision_type == "deal_proposal" else None,
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


def _verify_orange_monopoly_development(parsed: dict[str, Any]) -> None:
    action = _dict(parsed.get("action"))
    payload = _dict(action.get("payload"))
    assert parsed.get("decision_type") == "action_decision"
    assert action.get("type") == "BUY_HOUSE", f"expected BUY_HOUSE, got {action.get('type')}"
    assert payload.get("property_id") in ORANGE_PROPERTY_IDS
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


def _case_summary(case: StrategySmokeCase, parsed: dict[str, Any]) -> dict[str, Any]:
    if case.decision_type == "action_decision":
        action = _dict(parsed.get("action"))
        return {"case": case.name, "status": "ok", "action_type": action.get("type")}
    if case.decision_type == "deal_proposal":
        terms = _dict(_dict(parsed.get("deal")).get("terms"))
        return {
            "case": case.name,
            "status": "ok",
            "decision_type": parsed.get("decision_type"),
            "term_kinds": [term.get("kind") for term in terms.get("terms", []) if isinstance(term, dict)],
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
    if case.decision_type != "deal_proposal":
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


def _strategy_rule_snippets(case: StrategySmokeCase) -> tuple[dict[str, str], ...]:
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
