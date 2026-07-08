from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.decision_schema import (
    AI_OUTPUT_SCHEMA,
    AI_MEMORY_CATEGORIES,
    AIDecisionValidationError,
    DECISION_TYPES,
    MALFORMED_AI_OUTPUT_REASON_CODE,
    output_schema,
    rejected_ai_output,
    validate_ai_decision_output,
)


GAME_ID = "00000000-0000-0000-0000-000000000701"
PLAYER_ID = "00000000-0000-0000-0000-000000000702"
RECIPIENT_ID = "00000000-0000-0000-0000-000000000703"
NEGOTIATION_ID = "00000000-0000-0000-0000-000000000704"
DEAL_ID = "00000000-0000-0000-0000-000000000705"
THIRD_PLAYER_ID = "00000000-0000-0000-0000-000000000706"


def _metadata() -> dict[str, Any]:
    return {
        "self_dialogue": {
            "status": "provided",
            "text": "Boardwalk is not available, so preserve cash and keep tempo.",
        },
        "memory_updates": [
            {
                "visibility": "private",
                "category": "strategic_belief",
                "importance": 6,
                "content": "Preserve liquidity until an orange property can be traded for.",
            }
        ],
        "confidence": 0.74,
        "rationale": "The selected output is legal-looking and references the current state.",
    }


def _base(decision_type: str) -> dict[str, Any]:
    return {
        "decision_type": decision_type,
        "game_id": GAME_ID,
        "player_id": PLAYER_ID,
        **_metadata(),
    }


def test_valid_ai_decision_shapes_parse_before_mutation() -> None:
    # AI output validates mechanically before every game mutation
    # Confidence/rationale metadata
    examples: list[tuple[str, dict[str, Any]]] = [
        (
            "Action decision shape",
            {
                **_base("action_decision"),
                "expected_state_hash": "state-hash-7-2",
                "expected_event_sequence": 12,
                "action": {
                    "type": "ROLL_DICE",
                    "payload": {},
                },
            },
        ),
        (
            "Negotiation message shape",
            {
                **_base("negotiation_message"),
                "negotiation_id": NEGOTIATION_ID,
                "message": {
                    "recipient_player_id": RECIPIENT_ID,
                    "body": "I can trade Baltic if you leave the light blues alone.",
                    "metadata": {"tone": "firm"},
                },
            },
        ),
        (
            "Deal proposal shape",
            {
                **_base("deal_proposal"),
                "negotiation_id": NEGOTIATION_ID,
                "deal": {
                    "recipient_player_ids": [RECIPIENT_ID],
                    "terms": {
                        "cash": [{"from_player_id": PLAYER_ID, "to_player_id": RECIPIENT_ID, "amount": 100}],
                        "properties": [],
                    },
                    "message": "Cash now for future rent relief.",
                },
            },
        ),
        (
            "Counteroffer shape",
            {
                **_base("counteroffer"),
                "negotiation_id": NEGOTIATION_ID,
                "counteroffer": {
                    "responds_to_deal_id": DEAL_ID,
                    "terms": {
                        "cash": [{"from_player_id": RECIPIENT_ID, "to_player_id": PLAYER_ID, "amount": 140}],
                        "properties": ["property_baltic_avenue"],
                    },
                    "message": "I need more cash to offset the property.",
                },
            },
        ),
        (
            "Accept/reject shape",
            {
                **_base("accept_reject"),
                "negotiation_id": NEGOTIATION_ID,
                "accept_reject": {
                    "deal_id": DEAL_ID,
                    "decision": "reject",
                    "message": "The cash position is too weak after this deal.",
                },
            },
        ),
        (
            "Self-dialogue shape",
            {
                **_base("self_dialogue"),
                "self_dialogue": {
                    "status": "empty",
                    "reason": "No private reasoning was produced for this turn.",
                },
            },
        ),
        (
            "Memory update shape",
            {
                **_base("memory_update"),
                "self_dialogue": {
                    "status": "rejected",
                    "reason": "Self-dialogue was withheld by policy.",
                },
                "memory_updates": [
                    {
                        "visibility": "private",
                        "category": "player_trust_model",
                        "importance": 8,
                        "content": "Grace rejected cash-heavy deals twice in this negotiation.",
                    }
                ],
            },
        ),
    ]

    for label, raw_output in examples:
        parsed = validate_ai_decision_output(raw_output)

        assert parsed.root.decision_type == raw_output["decision_type"], label
        assert str(parsed.root.game_id) == GAME_ID
        assert str(parsed.root.player_id) == PLAYER_ID
        assert parsed.root.confidence == raw_output["confidence"]
        assert parsed.root.rationale == raw_output["rationale"]


def test_provided_self_dialogue_without_text_normalizes_to_empty_dialogue() -> None:
    raw_output = {
        **_base("action_decision"),
        "expected_state_hash": "state-hash-empty-dialogue",
        "expected_event_sequence": 12,
        "action": {
            "type": "END_TURN",
            "payload": {},
        },
        "self_dialogue": {"status": "provided"},
    }

    parsed = validate_ai_decision_output(raw_output)

    assert parsed.root.self_dialogue.status == "empty"
    assert parsed.root.self_dialogue.text is None
    assert parsed.root.self_dialogue.reason == "No self-dialogue text provided."


def test_schema_export_is_serializable_for_codex_exec_output_schema() -> None:
    # schema is used by codex exec --json --output-schema
    serialized = json.dumps(AI_OUTPUT_SCHEMA)

    for decision_type in DECISION_TYPES:
        assert decision_type in serialized
    assert "expected_state_hash" in serialized
    assert "confidence" in serialized
    assert "rationale" in serialized


def test_schema_export_closes_all_objects_for_codex_response_format() -> None:
    def walk(node: object) -> list[dict[str, Any]]:
        if isinstance(node, dict):
            found = [node] if node.get("type") == "object" else []
            for value in node.values():
                found.extend(walk(value))
            return found
        if isinstance(node, list):
            found: list[dict[str, Any]] = []
            for value in node:
                found.extend(walk(value))
            return found
        return []

    object_schemas = walk(AI_OUTPUT_SCHEMA)

    assert object_schemas
    assert all(schema.get("additionalProperties") is False for schema in object_schemas)


def test_decision_specific_schema_is_single_strict_object_for_codex_response_format() -> None:
    schema = output_schema("action_decision")

    assert schema["type"] == "object"
    assert "oneOf" not in schema
    assert "$defs" not in schema
    assert schema["properties"]["decision_type"]["const"] == "action_decision"

    def walk(node: object) -> list[dict[str, Any]]:
        if isinstance(node, dict):
            assert "$ref" not in node
            found = [node] if node.get("type") == "object" else []
            for value in node.values():
                found.extend(walk(value))
            return found
        if isinstance(node, list):
            found: list[dict[str, Any]] = []
            for value in node:
                found.extend(walk(value))
            return found
        return []

    object_schemas = walk(schema)

    assert object_schemas
    for object_schema in object_schemas:
        assert object_schema.get("additionalProperties") is False
        properties = object_schema.get("properties")
        assert isinstance(properties, dict)
        assert set(object_schema.get("required", ())) == set(properties)


def test_action_decision_output_schema_keeps_dynamic_payload_expressible() -> None:
    schema = output_schema("action_decision")
    payload_schema = schema["properties"]["action"]["properties"]["payload"]

    assert payload_schema["type"] == "string"
    assert payload_schema.get("properties") is None
    assert payload_schema.get("additionalProperties") is None


def test_deal_proposal_output_schema_keeps_dynamic_terms_expressible() -> None:
    schema = output_schema("deal_proposal")
    terms_schema = schema["properties"]["deal"]["properties"]["terms"]

    assert terms_schema["type"] == "string"
    assert terms_schema.get("properties") is None
    assert terms_schema.get("additionalProperties") is None


def test_codex_boundary_action_payload_json_string_normalizes_to_dict() -> None:
    raw_output = {
        **_base("action_decision"),
        "expected_state_hash": "state-hash-7-2",
        "expected_event_sequence": 12,
        "action": {
            "type": "BID_AUCTION",
            "payload": json.dumps(
                {
                    "property_id": "property_mediterranean_avenue",
                    "amount": 80,
                    "source": {"kind": "codex-boundary"},
                }
            ),
        },
    }

    parsed = validate_ai_decision_output(raw_output)
    dumped = parsed.root.model_dump(mode="json")

    assert dumped["action"]["payload"] == {
        "property_id": "property_mediterranean_avenue",
        "amount": 80,
        "source": {"kind": "codex-boundary"},
    }


def test_codex_boundary_deal_terms_json_string_normalizes_to_dict() -> None:
    raw_output = {
        **_base("deal_proposal"),
        "negotiation_id": NEGOTIATION_ID,
        "deal": {
            "recipient_player_ids": [RECIPIENT_ID],
            "terms": json.dumps(
                {
                    "terms": [
                        {
                            "type": "deferred_cash_payment",
                            "from_player_id": PLAYER_ID,
                            "to_player_id": RECIPIENT_ID,
                            "amount": 125,
                            "due_turn": 4,
                        }
                    ],
                    "metadata": {"codex_boundary": True},
                }
            ),
            "message": "Pay later for immediate position.",
        },
    }

    parsed = validate_ai_decision_output(raw_output)
    dumped = parsed.root.model_dump(mode="json")

    assert dumped["deal"]["terms"] == {
        "terms": [
            {
                "type": "deferred_cash_payment",
                "from_player_id": PLAYER_ID,
                "to_player_id": RECIPIENT_ID,
                "amount": 125,
                "due_turn": 4,
            }
        ],
        "metadata": {"codex_boundary": True},
    }


def test_open_negotiation_parses_without_negotiation_id() -> None:
    raw_output = {
        **_base("open_negotiation"),
        "negotiation": {
            "participant_player_ids": [PLAYER_ID, RECIPIENT_ID, THIRD_PLAYER_ID],
        },
    }

    parsed = validate_ai_decision_output(raw_output)

    assert parsed.root.decision_type == "open_negotiation"
    assert not hasattr(parsed.root, "negotiation_id")
    assert [str(player_id) for player_id in parsed.root.negotiation.participant_player_ids] == [
        PLAYER_ID,
        RECIPIENT_ID,
        THIRD_PLAYER_ID,
    ]
    assert parsed.root.negotiation.context == {}


def test_open_negotiation_is_present_in_exported_schema_without_required_negotiation_id() -> None:
    serialized = json.dumps(AI_OUTPUT_SCHEMA)

    assert "open_negotiation" in DECISION_TYPES
    assert "open_negotiation" in serialized
    open_negotiation_schema = AI_OUTPUT_SCHEMA["$defs"]["OpenNegotiationOutput"]
    open_negotiation_payload_schema = AI_OUTPUT_SCHEMA["$defs"]["OpenNegotiationPayload"]
    participant_schema = open_negotiation_payload_schema["properties"]["participant_player_ids"]
    assert participant_schema["minItems"] == 2
    assert participant_schema["maxItems"] == 5
    assert participant_schema["uniqueItems"] is True
    assert "context" in open_negotiation_payload_schema["properties"]
    assert "context" not in open_negotiation_payload_schema["required"]
    assert "negotiation_id" not in open_negotiation_schema["properties"]
    assert "negotiation_id" not in open_negotiation_schema["required"]


@pytest.mark.parametrize(
    "participant_player_ids",
    [
        [PLAYER_ID],
        [PLAYER_ID, PLAYER_ID],
        [PLAYER_ID, RECIPIENT_ID, THIRD_PLAYER_ID, NEGOTIATION_ID, DEAL_ID, PLAYER_ID],
    ],
)
def test_malformed_open_negotiation_participant_payloads_are_rejected(
    participant_player_ids: list[str],
) -> None:
    raw_output = {
        **_base("open_negotiation"),
        "negotiation": {
            "participant_player_ids": participant_player_ids,
            "context": {"topic": "invalid participant list"},
        },
    }

    with pytest.raises(AIDecisionValidationError) as exc_info:
        validate_ai_decision_output(raw_output)

    assert exc_info.value.reason_code == MALFORMED_AI_OUTPUT_REASON_CODE
    assert {issue.code for issue in exc_info.value.errors} == {"malformed_ai_output"}
    assert any("participant_player_ids" in (issue.field or issue.message) for issue in exc_info.value.errors)


def test_overlong_ai_negotiation_messages_are_rejected_by_schema_before_lifecycle_application() -> None:
    # Overlong AI negotiation messages are rejected by schema before lifecycle application
    body_schema = AI_OUTPUT_SCHEMA["$defs"]["NegotiationMessagePayload"]["properties"]["body"]
    assert body_schema["maxLength"] == 4000

    raw_output = {
        **_base("negotiation_message"),
        "negotiation_id": NEGOTIATION_ID,
        "message": {
            "recipient_player_id": RECIPIENT_ID,
            "body": "x" * 4001,
            "metadata": {"tone": "firm"},
        },
    }

    with pytest.raises(AIDecisionValidationError) as exc_info:
        validate_ai_decision_output(raw_output)

    assert exc_info.value.reason_code == MALFORMED_AI_OUTPUT_REASON_CODE
    assert {issue.code for issue in exc_info.value.errors} == {"malformed_ai_output"}
    assert any(issue.field == "message.body" for issue in exc_info.value.errors)


@pytest.mark.parametrize(
    "raw_output, expected_field",
    [
        (
            {
                **_base("action_decision"),
                "expected_event_sequence": 12,
                "action": {"type": "ROLL_DICE", "payload": {}},
            },
            "expected_state_hash",
        ),
        (
            {
                **_base("memory_update"),
                "self_dialogue": {"status": "empty", "reason": "No private reasoning."},
                "memory_updates": [
                    {
                        "visibility": "private",
                        "category": "strategic_belief",
                        "importance": 11,
                        "content": "Importance must stay within the schema range.",
                    }
                ],
            },
            "memory_updates.0.importance",
        ),
    ],
)
def test_missing_or_invalid_fields_produce_malformed_ai_output_errors(
    raw_output: dict[str, Any],
    expected_field: str,
) -> None:
    # Malformed or incomplete output is rejected and audited
    with pytest.raises(AIDecisionValidationError) as exc_info:
        validate_ai_decision_output(raw_output)

    assert exc_info.value.reason_code == MALFORMED_AI_OUTPUT_REASON_CODE
    assert {issue.code for issue in exc_info.value.errors} == {"malformed_ai_output"}
    assert any(issue.field == expected_field for issue in exc_info.value.errors)


def test_rejected_ai_output_audit_payload_keeps_raw_output_and_no_substitute_move() -> None:
    raw_output = {
        **_base("action_decision"),
        "expected_event_sequence": 12,
        "action": {"type": "ROLL_DICE", "payload": {}},
    }

    with pytest.raises(AIDecisionValidationError) as exc_info:
        validate_ai_decision_output(raw_output)

    audit_payload = rejected_ai_output(raw_output, exc_info.value)

    assert audit_payload.reason_code == "malformed_ai_output"
    assert audit_payload.raw_output
    assert "action_decision" in audit_payload.raw_output
    assert audit_payload.validation_errors
    assert audit_payload.game_id == GAME_ID
    assert audit_payload.player_id == PLAYER_ID
    assert audit_payload.substitute_move is None
    assert audit_payload.no_substitute_move is True
    assert audit_payload.model_dump()["substitute_move"] is None
    assert audit_payload.model_dump()["no_substitute_move"] is True
    assert audit_payload.audit_payload["raw_output"] == audit_payload.raw_output
    assert audit_payload.audit_payload["validation_errors"][0]["code"] == "malformed_ai_output"
    assert audit_payload.audit_payload["no_substitute_move"] is True


@pytest.mark.parametrize("category", AI_MEMORY_CATEGORIES)
def test_stage_8_2_memory_canonical_categories_are_accepted(category: str) -> None:
    raw_output = {
        **_base("memory_update"),
        "self_dialogue": {"status": "empty", "reason": "No private reasoning."},
        "memory_updates": [
            {
                "visibility": "private",
                "category": category,
                "importance": 5,
                "content": f"Persist canonical memory category {category}.",
                "metadata": {"stage": "8.2"},
            }
        ],
    }

    parsed = validate_ai_decision_output(raw_output)

    assert parsed.root.memory_updates[0].category == category


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ("", {}),
        ("not-json", {}),
        (json.dumps(["not", "an", "object"]), {}),
        (json.dumps("not an object"), {}),
        (json.dumps({"source": "ai-note", "turn": 3}), {"source": "ai-note", "turn": 3}),
    ],
)
def test_memory_metadata_strings_are_audit_safe_optional_objects(
    metadata: str,
    expected: dict[str, Any],
) -> None:
    raw_output = {
        **_base("memory_update"),
        "self_dialogue": {"status": "empty", "reason": "No private reasoning."},
        "memory_updates": [
            {
                "visibility": "private",
                "category": "strategic_belief",
                "importance": 5,
                "content": "Optional metadata should not block a legal AI action.",
                "metadata": metadata,
            }
        ],
    }

    parsed = validate_ai_decision_output(raw_output)

    assert parsed.root.memory_updates[0].metadata == expected


def test_stage_8_2_memory_invalid_category_is_rejected_before_persistence() -> None:
    raw_output = {
        **_base("memory_update"),
        "self_dialogue": {"status": "empty", "reason": "No private reasoning."},
        "memory_updates": [
            {
                "visibility": "private",
                "category": "opponent_model",
                "importance": 5,
                "content": "Legacy non-canonical category must be rejected.",
                "metadata": {"stage": "8.2"},
            }
        ],
    }

    with pytest.raises(AIDecisionValidationError) as exc_info:
        validate_ai_decision_output(raw_output)

    assert exc_info.value.reason_code == MALFORMED_AI_OUTPUT_REASON_CODE
    assert any(issue.field == "memory_updates.0.category" for issue in exc_info.value.errors)
