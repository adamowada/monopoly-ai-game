from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_SMOKE_PATH = REPO_ROOT / "scripts" / "product_smoke.py"
LIVE_SMOKE_PATH = REPO_ROOT / "services" / "api" / "scripts" / "live_codex_ai_smoke.py"
LIVE_STRATEGY_SMOKE_PATH = REPO_ROOT / "services" / "api" / "scripts" / "live_codex_ai_strategy_smoke.py"
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
    assert "orange_monopoly_development" in source
    assert "orange_near_monopoly_negotiation" in source
    assert "orange_near_monopoly_deal_proposal" in source
    assert "orange_bad_deal_rejection" in source
    assert "FOURTH_PLAYER_ID" in source
    assert 'PlayerSetup(id=str(FOURTH_PLAYER_ID), name="Marie", kind="ai")' in source
    assert "BUY_HOUSE" in source
    assert "open_negotiation" in source
    assert "deal_proposal" in source
    assert "immediate_cash_transfer" in source
    assert "immediate_property_transfer" in source
    assert "property_tennessee_avenue" in source
    assert "participant_player_ids" in source
    assert "accept_reject" in source
    assert "expected reject" in source
    assert "treating as pass" not in source


def test_several_turn_scripted_smoke_rejects_actions_without_player_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
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
            "players": [{"id": player_id, "controller_type": "human"} for player_id in self.player_ids],
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
            "type": "END_TURN" if self.rotates_turns and self.phase != "START_TURN" else "ROLL_DICE",
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
