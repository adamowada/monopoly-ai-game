from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.metadata import ai_profiles, games, players


STRATEGY_TRAIT_FIELDS: tuple[str, ...] = (
    "risk_tolerance",
    "liquidity_preference",
    "debt_appetite",
    "aggressiveness",
    "cooperation",
    "negotiation_creativity",
    "trust",
    "monopoly_focus",
)

_PERSONA_ADJECTIVES: tuple[str, ...] = (
    "Calculated",
    "Opportunistic",
    "Patient",
    "Assertive",
    "Inventive",
    "Guarded",
    "Collaborative",
    "Relentless",
)

_PERSONA_ARCHETYPES: tuple[str, ...] = (
    "Capital Planner",
    "Auction Hawk",
    "Deal Architect",
    "Rent Builder",
    "Liquidity Keeper",
    "Monopoly Hunter",
    "Debt Strategist",
    "Table Diplomat",
)


class AIProfileGameNotFoundError(RuntimeError):
    """Raised when AI profiles are requested for an unknown game."""


@dataclass(frozen=True)
class GeneratedAIProfile:
    persona_name: str
    strategy_profile: dict[str, Any]
    persona_summary: dict[str, str]


@dataclass(frozen=True)
class AIProfile:
    id: UUID
    game_id: UUID
    player_id: UUID
    display_name: str
    persona_name: str
    strategy_profile: Mapping[str, Any]
    persona_summary: str
    traits: tuple[str, ...]
    personality: str
    play_style: str
    risk_tolerance: float
    liquidity_preference: float
    debt_appetite: float
    aggressiveness: float
    cooperation: float
    negotiation_creativity: float
    trust: float
    monopoly_focus: float
    created_at: datetime
    updated_at: datetime


def generate_ai_profile(
    *,
    game_seed: str,
    player_id: str | UUID,
    seat_order: int,
    player_name: str,
) -> GeneratedAIProfile:
    generated = seeded_personality_generator(
        game_seed=game_seed,
        player_id=player_id,
        seat_order=seat_order,
        player_name=player_name,
    )
    return GeneratedAIProfile(
        persona_name=str(generated["persona_name"]),
        strategy_profile=dict(generated["strategy_profile"]),
        persona_summary=dict(generated["persona_summary"]),
    )


def seeded_personality_generator(
    *,
    game_seed: str,
    player_id: str | UUID,
    seat_order: int,
    player_name: str,
) -> dict[str, Any]:
    seed_material = f"{game_seed}|{player_id}|{seat_order}|{player_name}"
    trait_values = {
        trait: _bounded_trait_value(seed_material, trait) for trait in STRATEGY_TRAIT_FIELDS
    }

    adjective = _pick(_PERSONA_ADJECTIVES, seed_material, "persona-adjective")
    archetype = _pick(_PERSONA_ARCHETYPES, seed_material, "persona-archetype")
    persona_name = f"{adjective} {archetype}"

    traits = _trait_labels(trait_values)
    personality = _personality_sentence(trait_values)
    play_style = _play_style_sentence(trait_values)
    summary = (
        f"{player_name} plays as a {persona_name.lower()} with {traits[0]} and "
        f"{traits[1]}; {play_style[0].lower()}{play_style[1:]}"
    )
    persona_summary = {
        "summary": summary,
        "personality": personality,
        "play_style": play_style,
    }
    strategy_profile: dict[str, Any] = {
        **trait_values,
        "schema_version": 1,
        "traits": traits,
        "personality": personality,
        "play_style": play_style,
        "persona_summary": summary,
        "source": {
            "game_seed": game_seed,
            "player_id": str(player_id),
            "seat_order": seat_order,
            "player_name": player_name,
        },
    }
    return {
        "persona_name": persona_name,
        "strategy_profile": strategy_profile,
        "persona_summary": persona_summary,
    }


async def ensure_ai_profiles_for_game(
    session: AsyncSession,
    *,
    game_id: UUID,
) -> list[AIProfile]:
    game_row = await _load_game_for_profiles(session, game_id)
    player_rows = await _load_players_for_profiles(session, game_id)
    existing_profiles = await _load_existing_profile_rows(session, game_id)
    existing_by_player_id = {row["player_id"]: row for row in existing_profiles}

    for player_row in player_rows:
        if player_row["controller_type"] != "ai" or player_row["id"] in existing_by_player_id:
            continue
        generated = generate_ai_profile(
            game_seed=game_row["seed"] or str(game_id),
            player_id=player_row["id"],
            seat_order=int(player_row["seat_order"]),
            player_name=str(player_row["name"]),
        )
        await session.execute(
            ai_profiles.insert().values(
                game_id=game_id,
                player_id=player_row["id"],
                persona_name=generated.persona_name,
                strategy_profile=generated.strategy_profile,
                persona_summary=generated.persona_summary,
            )
        )

    return await load_ai_profiles_for_game(session, game_id=game_id)


async def load_ai_profiles_for_game(
    session: AsyncSession,
    *,
    game_id: UUID,
) -> list[AIProfile]:
    await _load_game_for_profiles(session, game_id, lock=False)
    result = await session.execute(
        sa.select(
            ai_profiles,
            players.c.name.label("player_name"),
            players.c.seat_order.label("seat_order"),
        )
        .join(players, players.c.id == ai_profiles.c.player_id)
        .where(
            ai_profiles.c.game_id == game_id,
            players.c.game_id == game_id,
            players.c.controller_type == "ai",
        )
        .order_by(players.c.seat_order)
    )
    return [_profile_from_row(dict(row)) for row in result.mappings().all()]


async def _load_game_for_profiles(
    session: AsyncSession,
    game_id: UUID,
    *,
    lock: bool = True,
) -> Mapping[str, Any]:
    statement = sa.select(games.c.id, games.c.seed).where(games.c.id == game_id)
    if lock:
        statement = statement.with_for_update()
    result = await session.execute(statement)
    row = result.mappings().first()
    if row is None:
        raise AIProfileGameNotFoundError(f"game {game_id} was not found")
    return dict(row)


async def _load_players_for_profiles(
    session: AsyncSession,
    game_id: UUID,
) -> list[Mapping[str, Any]]:
    result = await session.execute(
        sa.select(
            players.c.id,
            players.c.seat_order,
            players.c.name,
            players.c.controller_type,
        )
        .where(players.c.game_id == game_id)
        .order_by(players.c.seat_order)
    )
    return [dict(row) for row in result.mappings().all()]


async def _load_existing_profile_rows(
    session: AsyncSession,
    game_id: UUID,
) -> list[Mapping[str, Any]]:
    result = await session.execute(
        sa.select(ai_profiles).where(ai_profiles.c.game_id == game_id)
    )
    return [dict(row) for row in result.mappings().all()]


def _profile_from_row(row: Mapping[str, Any]) -> AIProfile:
    strategy_profile = _mapping_or_empty(row["strategy_profile"])
    persona_summary_payload = _mapping_or_empty(row["persona_summary"])
    trait_values = {
        trait: _trait_float(strategy_profile.get(trait)) for trait in STRATEGY_TRAIT_FIELDS
    }
    personality = _string_value(strategy_profile.get("personality"), "Measured competitor")
    play_style = _string_value(strategy_profile.get("play_style"), "Balances cash and assets.")
    summary = _string_value(
        persona_summary_payload.get("summary"),
        _string_value(strategy_profile.get("persona_summary"), play_style),
    )
    persona_name = _string_value(row["persona_name"], f"{row['player_name']} AI profile")
    traits = strategy_profile.get("traits")
    if not isinstance(traits, list) or not all(isinstance(item, str) for item in traits):
        traits = _trait_labels(trait_values)

    return AIProfile(
        id=row["id"],
        game_id=row["game_id"],
        player_id=row["player_id"],
        display_name=f"{row['player_name']} - {persona_name}",
        persona_name=persona_name,
        strategy_profile=strategy_profile,
        persona_summary=summary,
        traits=tuple(traits),
        personality=personality,
        play_style=play_style,
        risk_tolerance=trait_values["risk_tolerance"],
        liquidity_preference=trait_values["liquidity_preference"],
        debt_appetite=trait_values["debt_appetite"],
        aggressiveness=trait_values["aggressiveness"],
        cooperation=trait_values["cooperation"],
        negotiation_creativity=trait_values["negotiation_creativity"],
        trust=trait_values["trust"],
        monopoly_focus=trait_values["monopoly_focus"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _bounded_trait_value(seed_material: str, trait: str) -> float:
    digest = hashlib.sha256(f"{seed_material}|{trait}".encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
    return round(0.1 + raw * 0.8, 2)


def _pick(options: tuple[str, ...], seed_material: str, key: str) -> str:
    digest = hashlib.sha256(f"{seed_material}|{key}".encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(options)
    return options[index]


def _trait_labels(values: Mapping[str, float]) -> list[str]:
    return [
        _stance(values["risk_tolerance"], "bold risk taker", "balanced risk reader", "risk-aware planner"),
        _stance(
            values["liquidity_preference"],
            "cash-buffered operator",
            "flexible cash manager",
            "asset-heavy investor",
        ),
        _stance(values["debt_appetite"], "debt-leveraged buyer", "selective borrower", "debt-averse saver"),
        _stance(values["aggressiveness"], "pressure-first bidder", "measured competitor", "patient table watcher"),
        _stance(values["cooperation"], "deal-friendly partner", "situational collaborator", "solo negotiator"),
        _stance(
            values["negotiation_creativity"],
            "creative dealmaker",
            "practical negotiator",
            "plain-terms trader",
        ),
        _stance(values["trust"], "trusting coalition builder", "earned-trust evaluator", "skeptical verifier"),
        _stance(values["monopoly_focus"], "monopoly hunter", "portfolio balancer", "cash-flow optimizer"),
    ]


def _personality_sentence(values: Mapping[str, float]) -> str:
    if values["aggressiveness"] >= 0.67 and values["risk_tolerance"] >= 0.55:
        return "Assertive and pressure-oriented, willing to trade safety for board control."
    if values["cooperation"] >= 0.67 and values["trust"] >= 0.5:
        return "Collaborative but strategic, looking for deals that keep future options open."
    if values["liquidity_preference"] >= 0.67:
        return "Careful and cash-aware, preferring reserves before expensive commitments."
    if values["negotiation_creativity"] >= 0.67:
        return "Inventive and deal-minded, comfortable with unusual but enforceable terms."
    return "Measured and adaptive, balancing rent pressure, cash, and negotiation leverage."


def _play_style_sentence(values: Mapping[str, float]) -> str:
    if values["monopoly_focus"] >= 0.67:
        return "Prioritizes color-set completion and uses trades to unlock building paths."
    if values["debt_appetite"] >= 0.67:
        return "Uses mortgages and obligations as tools when they can accelerate position."
    if values["liquidity_preference"] >= 0.67:
        return "Keeps cash reserves high and avoids overextending during auctions."
    if values["aggressiveness"] >= 0.67:
        return "Bids early, pressures opponents, and seeks tempo advantages."
    return "Adjusts between property acquisition, rent defense, and selective negotiation."


def _stance(value: float, high: str, middle: str, low: str) -> str:
    if value >= 0.67:
        return high
    if value <= 0.33:
        return low
    return middle


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_value(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _trait_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        return round(max(0.0, min(1.0, float(value))), 2)
    return 0.0


__all__ = [
    "AIProfile",
    "AIProfileGameNotFoundError",
    "GeneratedAIProfile",
    "STRATEGY_TRAIT_FIELDS",
    "ensure_ai_profiles_for_game",
    "generate_ai_profile",
    "load_ai_profiles_for_game",
    "seeded_personality_generator",
]
