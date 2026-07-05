from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from app.rag.corpus import (
    CorpusDocument,
    build_ai_memory_corpus,
    build_contract_example_corpus,
    build_negotiation_history_corpus,
    build_past_decision_corpus,
    build_rules_corpus,
    build_static_local_corpus,
)
from app.rag.lexical import search_corpus
from app.rules.financial_instruments import combination_deal
from app.rules.static_data import load_classic_monopoly_data


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTENT_RULES = REPO_ROOT / "content" / "rules"


def test_stage_9_1_local_retrieval_corpus_static_sources_include_required_types() -> None:
    documents = build_static_local_corpus(content_rules_dir=CONTENT_RULES)

    assert documents
    assert {"rules", "house_rules", "contract_examples"} <= {
        document.source_type for document in documents
    }
    assert all(document.document_id for document in documents)
    assert all(document.text.strip() for document in documents)


def test_stage_9_1_local_retrieval_corpus_finds_classic_boardwalk_rent_data() -> None:
    documents = build_rules_corpus(CONTENT_RULES / "classic_monopoly.json")

    results = search_corpus(documents, "Boardwalk hotel rent", limit=3)

    assert results
    top = results[0].document
    assert top.source_type == "rules"
    assert top.source_id == "property_boardwalk"
    assert "Boardwalk" in top.text
    assert "hotel rent 2000" in top.text


def test_stage_9_1_local_retrieval_corpus_finds_no_fallback_ai_deviation() -> None:
    documents = build_static_local_corpus(content_rules_dir=CONTENT_RULES)

    results = search_corpus(documents, "no fallback AI deviation substitute move", limit=5)

    assert results
    assert any(
        result.document.source_type == "house_rules"
        and result.document.source_id == "no_fallback_ai_decisions"
        and "No fallback" in result.document.text
        for result in results
    )


def test_stage_9_1_local_retrieval_corpus_finds_rent_share_contract_example() -> None:
    documents = build_contract_example_corpus(CONTENT_RULES / "contract_examples.json")

    results = search_corpus(documents, "rent-share Boardwalk contract example", limit=3)

    assert results
    top = results[0].document
    assert top.source_type == "contract_examples"
    assert top.source_id == "rent_share_boardwalk_example"
    assert "rent-share" in top.text
    assert "Boardwalk" in top.text


def test_stage_9_1_contract_examples_validate_with_backend_financial_instrument_validator() -> None:
    data = json.loads((CONTENT_RULES / "contract_examples.json").read_text(encoding="utf-8"))
    property_ids = {property_data.id for property_data in load_classic_monopoly_data().properties}
    failures: list[str] = []

    for example in data["examples"]:
        example_id = str(example.get("id", "<missing-id>"))
        party_aliases = example.get("parties")
        instruments = example.get("instruments")
        assert isinstance(party_aliases, list), f"{example_id} must define a party list"
        assert isinstance(instruments, list), f"{example_id} must define an instrument list"

        party_id_by_alias = {
            str(alias): f"00000000-0000-4000-8000-{index + 1:012d}"
            for index, alias in enumerate(party_aliases)
        }
        validator_payloads: list[Mapping[str, Any]] = []
        for index, instrument in enumerate(instruments):
            assert isinstance(instrument, dict), (
                f"{example_id} instrument {index} must be an object"
            )
            validator_payloads.append(
                cast(
                    Mapping[str, Any],
                    _resolve_contract_example_party_aliases(instrument, party_id_by_alias),
                )
            )
        _, errors = combination_deal(
            validator_payloads,
            player_ids=list(party_id_by_alias.values()),
            property_ids=property_ids,
            field=f"examples.{example_id}.instruments",
        )
        failures.extend(
            f"{example_id}: {error.field}: {error.message}"
            for error in errors
        )

    assert failures == []


def test_stage_9_1_local_retrieval_corpus_builds_game_derived_documents_from_rows() -> None:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
    game_id = UUID("00000000-0000-0000-0000-000000009101")
    ai_player_id = UUID("00000000-0000-0000-0000-000000009102")
    human_player_id = UUID("00000000-0000-0000-0000-000000009103")
    memory_id = UUID("00000000-0000-0000-0000-000000009104")
    negotiation_id = UUID("00000000-0000-0000-0000-000000009105")
    message_id = UUID("00000000-0000-0000-0000-000000009106")
    deal_id = UUID("00000000-0000-0000-0000-000000009107")
    decision_id = UUID("00000000-0000-0000-0000-000000009108")

    memory_docs = build_ai_memory_corpus(
        [
            {
                "id": memory_id,
                "game_id": game_id,
                "player_id": ai_player_id,
                "category": "strategic_belief",
                "visibility": "private",
                "content": "Protect the dark-blue monopoly plan and preserve cash.",
                "importance": 8,
                "metadata_blob": {"evidence": "manual fixture"},
                "created_at": now,
            }
        ]
    )
    negotiation_docs = build_negotiation_history_corpus(
        negotiation_rows=[
            {
                "id": negotiation_id,
                "game_id": game_id,
                "opened_by_player_id": ai_player_id,
                "status": "active",
                "phase": "NEGOTIATION",
                "round_number": 2,
                "context": {"topic": "Boardwalk rent share"},
                "created_at": now,
            }
        ],
        message_rows=[
            {
                "id": message_id,
                "game_id": game_id,
                "negotiation_id": negotiation_id,
                "sender_player_id": ai_player_id,
                "recipient_player_id": human_player_id,
                "message_type": "offer",
                "body": "I will pay cash now for a future rent share.",
                "payload": {"tone": "firm"},
                "created_at": now,
            }
        ],
        deal_rows=[
            {
                "id": deal_id,
                "game_id": game_id,
                "negotiation_id": negotiation_id,
                "proposed_by_player_id": ai_player_id,
                "status": "proposed",
                "version": 1,
                "terms": {
                    "instruments": [
                        {
                            "kind": "rent_share",
                            "property_id": "property_boardwalk",
                            "share_percent": 25,
                        }
                    ]
                },
                "created_at": now,
            }
        ],
    )
    decision_docs = build_past_decision_corpus(
        [
            {
                "id": decision_id,
                "game_id": game_id,
                "player_id": ai_player_id,
                "decision_type": "action_decision",
                "status": "accepted",
                "phase": "ROLL_REQUIRED",
                "state_hash": "state-hash",
                "prompt_context_hash": "prompt-hash",
                "raw_output": '{"decision": "roll"}',
                "parsed_output": {"kind": "action", "action": {"action_type": "ROLL_DICE"}},
                "validation_result": {"status": "accepted"},
                "created_at": now,
            }
        ]
    )

    assert _only(memory_docs).source_type == "ai_memory"
    assert "dark-blue monopoly plan" in _only(memory_docs).text
    assert {document.source_type for document in negotiation_docs} == {"negotiation_history"}
    assert any("future rent share" in document.text for document in negotiation_docs)
    assert any("property_boardwalk" in document.text for document in negotiation_docs)
    assert _only(decision_docs).source_type == "past_decision"
    assert "ROLL_DICE" in _only(decision_docs).text


def test_stage_9_1_local_retrieval_corpus_jsonl_index_command_is_deterministic(
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "corpus_first.jsonl"
    second_output = tmp_path / "corpus_second.jsonl"
    script = REPO_ROOT / "services" / "api" / "scripts" / "build_rag_index.py"

    for output in (first_output, second_output):
        completed = subprocess.run(
            [sys.executable, str(script), "--output", str(output)],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert completed.returncode == 0, completed.stdout

    first_text = first_output.read_text(encoding="utf-8")
    second_text = second_output.read_text(encoding="utf-8")
    assert first_text == second_text

    rows = [json.loads(line) for line in first_text.splitlines()]
    assert rows
    assert {"rules", "house_rules", "contract_examples"} <= {
        row["source_type"] for row in rows
    }
    assert all(row["document_id"].strip() for row in rows)
    assert all(row["text"].strip() for row in rows)
    assert all(isinstance(row["metadata"], dict) for row in rows)


def _only(documents: list[CorpusDocument]) -> CorpusDocument:
    assert len(documents) == 1
    return documents[0]


def _resolve_contract_example_party_aliases(
    value: object,
    party_id_by_alias: Mapping[str, str],
) -> object:
    if isinstance(value, str):
        return party_id_by_alias.get(value, value)
    if isinstance(value, list):
        return [
            _resolve_contract_example_party_aliases(item, party_id_by_alias)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: _resolve_contract_example_party_aliases(item, party_id_by_alias)
            for key, item in value.items()
        }
    return value
