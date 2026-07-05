from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.ai.profiles import STRATEGY_TRAIT_FIELDS, generate_ai_profile
from app.core.config import Settings
from app.db.metadata import ai_profiles, games, metadata
from app.main import create_app


TEST_DATABASE_URL = os.getenv(
    "MONOPOLY_TEST_DATABASE_URL",
    "postgresql+asyncpg://monopoly:monopoly@127.0.0.1:5432/monopoly_ai_game",
)


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
async def api_app() -> AsyncIterator[FastAPI]:
    app = create_app(
        settings=Settings(
            api_env="test",
            database_url=TEST_DATABASE_URL,
            cors_origins="http://localhost:3000",
        )
    )
    try:
        yield app
    finally:
        await app.state.database_engine.dispose()


@pytest_asyncio.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://testserver",
    ) as client:
        yield client


async def delete_game(session_factory: async_sessionmaker, game_id: str | UUID) -> None:
    async with session_factory() as session:
        async with session.begin():
            await session.execute(games.delete().where(games.c.id == UUID(str(game_id))))


async def fetch_profile_rows(
    session_factory: async_sessionmaker,
    game_id: str | UUID,
) -> list[dict[str, Any]]:
    async with session_factory() as session:
        result = await session.execute(
            sa.select(ai_profiles)
            .where(ai_profiles.c.game_id == UUID(str(game_id)))
            .order_by(ai_profiles.c.player_id)
        )
        return [dict(row) for row in result.mappings().all()]


async def create_seeded_game(client: httpx.AsyncClient, *, seed: str) -> dict[str, Any]:
    response = await client.post(
        "/games",
        json={
            "seed": seed,
            "players": [
                {"name": "Ada", "kind": "human"},
                {"name": "Grace", "kind": "ai"},
                {"name": "Linus", "kind": "ai"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


GENERATED_PROFILE_CONTENT_FIELDS: tuple[str, ...] = (
    "persona_name",
    "strategy_profile",
    "persona_summary",
    "traits",
    "personality",
    "play_style",
    *STRATEGY_TRAIT_FIELDS,
)


def generated_profile_content_by_display_name(
    profiles: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        profile["display_name"]: {
            field: profile[field] for field in GENERATED_PROFILE_CONTENT_FIELDS
        }
        for profile in profiles
    }


def test_generate_ai_profile_is_deterministic_and_varied() -> None:
    # deterministic profile generation by seed
    grace = generate_ai_profile(
        game_seed="stage-7-profile-seed",
        player_id="00000000-0000-0000-0000-000000000002",
        seat_order=1,
        player_name="Grace",
    )
    grace_again = generate_ai_profile(
        game_seed="stage-7-profile-seed",
        player_id="00000000-0000-0000-0000-000000000002",
        seat_order=1,
        player_name="Grace",
    )
    linus = generate_ai_profile(
        game_seed="stage-7-profile-seed",
        player_id="00000000-0000-0000-0000-000000000003",
        seat_order=2,
        player_name="Linus",
    )

    assert grace == grace_again
    assert set(STRATEGY_TRAIT_FIELDS) <= set(grace.strategy_profile)
    assert isinstance(grace.persona_summary["summary"], str)
    assert grace.persona_summary["summary"]

    # Different AI players have meaningfully varied traits
    different_traits = [
        trait
        for trait in STRATEGY_TRAIT_FIELDS
        if grace.strategy_profile[trait] != linus.strategy_profile[trait]
    ]
    assert len(different_traits) >= 3
    assert grace.persona_summary["summary"] != linus.persona_summary["summary"]


def test_generate_ai_profile_ignores_random_player_uuid_for_seeded_traits() -> None:
    first = generate_ai_profile(
        game_seed="stage-7-reproducible-profile-seed",
        player_id="00000000-0000-0000-0000-000000000101",
        seat_order=1,
        player_name="Grace",
    )
    second = generate_ai_profile(
        game_seed="stage-7-reproducible-profile-seed",
        player_id="00000000-0000-0000-0000-000000000202",
        seat_order=1,
        player_name="Grace",
    )

    assert first == second
    assert first.strategy_profile["source"] == {
        "game_seed": "stage-7-reproducible-profile-seed",
        "seat_order": 1,
        "player_name": "Grace",
        "player_stable_key": "seat-1:Grace",
    }


@pytest.mark.asyncio
async def test_ai_profiles_are_created_persisted_and_returned_for_seeded_games(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    # AI players in the same seeded game get stable profiles
    # Profiles persist in Postgres
    # Persona summary visible in audit UI
    created = await create_seeded_game(client, seed="stage-7-stable-profile-seed")
    game_id = created["id"]
    players = created["players"]
    ai_player_ids = {player["id"] for player in players if player["controller_type"] == "ai"}
    human_player_ids = {
        player["id"] for player in players if player["controller_type"] == "human"
    }

    try:
        rows_after_create = await fetch_profile_rows(session_factory, game_id)
        assert len(rows_after_create) == 2

        first_response = await client.get(f"/games/{game_id}/ai/profiles")
        second_response = await client.get(f"/games/{game_id}/ai/profiles")

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        assert first_response.json() == second_response.json()

        profiles = first_response.json()["profiles"]
        assert {profile["player_id"] for profile in profiles} == ai_player_ids
        assert not ({profile["player_id"] for profile in profiles} & human_player_ids)
        assert len(await fetch_profile_rows(session_factory, game_id)) == 2

        for profile in profiles:
            assert profile["ai_profile_id"]
            assert profile["display_name"]
            assert profile["persona_summary"]
            assert profile["personality"]
            assert profile["play_style"]
            assert set(STRATEGY_TRAIT_FIELDS) <= set(profile["strategy_profile"])
            for trait in STRATEGY_TRAIT_FIELDS:
                assert profile[trait] == profile["strategy_profile"][trait]
                assert 0 <= profile[trait] <= 1

        [first_profile, second_profile] = profiles
        different_traits = [
            trait
            for trait in STRATEGY_TRAIT_FIELDS
            if first_profile[trait] != second_profile[trait]
        ]
        assert len(different_traits) >= 3
        assert first_profile["persona_summary"] != second_profile["persona_summary"]
    finally:
        await delete_game(session_factory, game_id)


@pytest.mark.asyncio
async def test_seeded_ai_profiles_reproduce_across_new_games_with_fresh_player_ids(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
) -> None:
    seed = "stage-7-cross-game-reproducible-profile-seed"
    first_game = await create_seeded_game(client, seed=seed)
    second_game = await create_seeded_game(client, seed=seed)
    first_game_id = first_game["id"]
    second_game_id = second_game["id"]

    try:
        first_response = await client.get(f"/games/{first_game_id}/ai/profiles")
        second_response = await client.get(f"/games/{second_game_id}/ai/profiles")

        assert first_response.status_code == 200
        assert second_response.status_code == 200
        first_profiles = first_response.json()["profiles"]
        second_profiles = second_response.json()["profiles"]

        first_player_ids = {profile["player_id"] for profile in first_profiles}
        second_player_ids = {profile["player_id"] for profile in second_profiles}
        assert first_player_ids
        assert second_player_ids
        assert first_player_ids.isdisjoint(second_player_ids)

        assert generated_profile_content_by_display_name(
            first_profiles
        ) == generated_profile_content_by_display_name(second_profiles)

        for profile in first_profiles + second_profiles:
            source = profile["strategy_profile"]["source"]
            assert "player_id" not in source
            assert set(source) == {
                "game_seed",
                "seat_order",
                "player_name",
                "player_stable_key",
            }

        first_profile_content = generated_profile_content_by_display_name(first_profiles)
        assert len(first_profile_content) == 2
        assert len({content["persona_name"] for content in first_profile_content.values()}) > 1
        trait_vectors = {
            tuple(content["strategy_profile"][trait] for trait in STRATEGY_TRAIT_FIELDS)
            for content in first_profile_content.values()
        }
        assert len(trait_vectors) > 1
    finally:
        await delete_game(session_factory, first_game_id)
        await delete_game(session_factory, second_game_id)


@pytest.mark.asyncio
async def test_ai_profiles_endpoint_returns_404_for_unknown_game(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/games/00000000-0000-0000-0000-000000000404/ai/profiles")

    assert response.status_code == 404
