from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.metadata import players, rejected_actions


@dataclass(frozen=True)
class RejectedActionRecord:
    id: UUID
    game_id: UUID
    actor_player_id: UUID | None
    action_type: str
    payload: Mapping[str, Any]
    reason_code: str
    validation_errors: Sequence[Mapping[str, Any]]
    legal_action_context: Mapping[str, Any] | None
    phase: str | None
    state_hash: str | None
    created_at: datetime

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "id": str(self.id) if mode == "json" else self.id,
            "game_id": str(self.game_id) if mode == "json" else self.game_id,
            "actor_player_id": str(self.actor_player_id)
            if mode == "json" and self.actor_player_id is not None
            else self.actor_player_id,
            "action_type": self.action_type,
            "payload": dict(self.payload),
            "reason_code": self.reason_code,
            "validation_errors": [dict(error) for error in self.validation_errors],
            "legal_action_context": None
            if self.legal_action_context is None
            else dict(self.legal_action_context),
            "phase": self.phase,
            "state_hash": self.state_hash,
            "created_at": self.created_at.isoformat() if mode == "json" else self.created_at,
        }


class RejectedActionAudit:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def persist_rejected_action(
        self,
        *,
        game_id: UUID | str,
        actor_player_id: UUID | str | None,
        action_type: str,
        payload: Mapping[str, Any],
        reason_code: str,
        validation_errors: Sequence[Mapping[str, Any]],
        legal_action_context: Mapping[str, Any] | None,
        phase: str | None,
        state_hash: str | None,
    ) -> RejectedActionRecord:
        normalized_game_id = _coerce_uuid(game_id)
        normalized_actor_player_id = (
            None if actor_player_id is None else _coerce_uuid(actor_player_id)
        )

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    rejected_actions.insert()
                    .values(
                        game_id=normalized_game_id,
                        actor_player_id=normalized_actor_player_id,
                        action_type=action_type,
                        payload=dict(payload),
                        reason_code=reason_code,
                        validation_errors=[dict(error) for error in validation_errors],
                        legal_action_context=None
                        if legal_action_context is None
                        else dict(legal_action_context),
                        phase=phase,
                        state_hash=state_hash,
                    )
                    .returning(rejected_actions)
                )
                return _record_from_row(dict(result.mappings().one()))

    async def list_rejected_actions(
        self,
        game_id: UUID | str,
        *,
        actor_player_id: UUID | str | None = None,
    ) -> list[RejectedActionRecord]:
        normalized_game_id = _coerce_uuid(game_id)
        normalized_actor_player_id = (
            None if actor_player_id is None else _coerce_uuid(actor_player_id)
        )

        statement = sa.select(rejected_actions).where(
            rejected_actions.c.game_id == normalized_game_id
        )
        if normalized_actor_player_id is not None:
            statement = statement.where(
                rejected_actions.c.actor_player_id == normalized_actor_player_id
            )

        statement = statement.order_by(
            rejected_actions.c.created_at.desc(),
            rejected_actions.c.id.desc(),
        )

        async with self._session_factory() as session:
            result = await session.execute(statement)
            return [_record_from_row(dict(row)) for row in result.mappings().all()]

    async def resolve_actor_player_id(
        self,
        *,
        game_id: UUID | str,
        actor_id: str | None,
    ) -> UUID | None:
        if actor_id is None:
            return None

        try:
            normalized_actor_id = _coerce_uuid(actor_id)
        except ValueError:
            return None

        normalized_game_id = _coerce_uuid(game_id)
        async with self._session_factory() as session:
            result = await session.execute(
                sa.select(players.c.id).where(
                    players.c.game_id == normalized_game_id,
                    players.c.id == normalized_actor_id,
                )
            )
            return result.scalar_one_or_none()


def _record_from_row(row: Mapping[str, Any]) -> RejectedActionRecord:
    return RejectedActionRecord(
        id=row["id"],
        game_id=row["game_id"],
        actor_player_id=row["actor_player_id"],
        action_type=row["action_type"],
        payload=row["payload"],
        reason_code=row["reason_code"],
        validation_errors=row["validation_errors"],
        legal_action_context=row["legal_action_context"],
        phase=row["phase"],
        state_hash=row["state_hash"],
        created_at=row["created_at"],
    )


def _coerce_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


__all__ = [
    "RejectedActionAudit",
    "RejectedActionRecord",
]
