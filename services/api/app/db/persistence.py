from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.metadata import game_events, game_snapshots, games, players
from app.rules.events import EventModel, GameEvent
from app.rules.reducer import InvalidEventError, apply_event
from app.rules.state import GameState


DEFAULT_SNAPSHOT_INTERVAL = 25


class EventPersistenceError(RuntimeError):
    """Raised when event persistence cannot complete without mutation ambiguity."""


class GameNotFoundError(EventPersistenceError):
    """Raised when a persistence operation references an unknown game."""


class StaleEventSequenceError(EventPersistenceError):
    """Raised when a caller attempts to append against a stale event sequence."""


class SnapshotCorruptionError(EventPersistenceError):
    """Raised when a stored snapshot payload or hash is inconsistent."""


@dataclass(frozen=True)
class EventAppendResult:
    event_id: UUID
    sequence: int
    state_hash: str
    snapshot_created: bool
    state: GameState


@dataclass(frozen=True)
class AcceptedEventTemplate:
    event_type: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class AcceptedEventRecord:
    id: UUID
    game_id: UUID
    sequence: int
    actor_player_id: UUID | None
    event_type: str
    payload: Mapping[str, Any]
    state_hash: str
    created_at: datetime

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "id": str(self.id) if mode == "json" else self.id,
            "game_id": str(self.game_id) if mode == "json" else self.game_id,
            "sequence": self.sequence,
            "actor_player_id": str(self.actor_player_id)
            if mode == "json" and self.actor_player_id is not None
            else self.actor_player_id,
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "state_hash": self.state_hash,
            "created_at": self.created_at.isoformat() if mode == "json" else self.created_at,
        }


@dataclass(frozen=True)
class EventAppendManyResult:
    events: tuple[AcceptedEventRecord, ...]
    state: GameState


@dataclass(frozen=True)
class SnapshotVerificationResult:
    game_id: UUID
    event_count: int
    snapshot_count: int
    replayed_state_hash: str
    latest_snapshot_state_hash: str


class EventPersistence:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        snapshot_interval: int = DEFAULT_SNAPSHOT_INTERVAL,
    ) -> None:
        self._session_factory = session_factory
        self._snapshot_interval = _validate_snapshot_interval(snapshot_interval)

    async def append_accepted_event(
        self,
        *,
        game_id: UUID | str,
        event_type: str,
        payload: Mapping[str, Any],
        actor_player_id: UUID | str | None = None,
        expected_sequence: int | None = None,
        snapshot_interval: int | None = None,
    ) -> EventAppendResult:
        normalized_game_id = _coerce_uuid(game_id)
        normalized_actor_player_id = (
            None if actor_player_id is None else _coerce_uuid(actor_player_id)
        )
        effective_snapshot_interval = (
            self._snapshot_interval
            if snapshot_interval is None
            else _validate_snapshot_interval(snapshot_interval)
        )

        async with self._session_factory() as session:
            async with session.begin():
                state = await self._replay_current_state_for_append(session, normalized_game_id)
                next_sequence = state.event_sequence + 1
                if expected_sequence is not None and expected_sequence != next_sequence:
                    raise StaleEventSequenceError(
                        f"expected sequence {expected_sequence} does not match next "
                        f"sequence {next_sequence}"
                    )

                event_id = uuid4()
                event = _build_game_event(
                    event_id=event_id,
                    sequence=next_sequence,
                    event_type=event_type,
                    payload=payload,
                )
                next_state = apply_event(state, event)
                state_hash = next_state.state_hash()

                await session.execute(
                    game_events.insert().values(
                        id=event_id,
                        game_id=normalized_game_id,
                        sequence=next_sequence,
                        actor_player_id=normalized_actor_player_id,
                        event_type=event.type,
                        payload=_payload_for_storage(event.payload),
                        state_hash=state_hash,
                    )
                )

                snapshot_created = False
                if next_sequence % effective_snapshot_interval == 0:
                    await session.execute(
                        game_snapshots.insert().values(
                            game_id=normalized_game_id,
                            last_event_id=event_id,
                            event_sequence=next_sequence,
                            state_payload=next_state.model_dump(mode="json"),
                            state_hash=state_hash,
                        )
                    )
                    snapshot_created = True

                return EventAppendResult(
                    event_id=event_id,
                    sequence=next_sequence,
                    state_hash=state_hash,
                    snapshot_created=snapshot_created,
                    state=next_state,
                )

    async def append_accepted_events(
        self,
        *,
        game_id: UUID | str,
        event_templates: Sequence[AcceptedEventTemplate],
        actor_player_id: UUID | str | None = None,
        expected_base_sequence: int | None = None,
        expected_base_state_hash: str | None = None,
        snapshot_interval: int | None = None,
    ) -> EventAppendManyResult:
        if not event_templates:
            raise EventPersistenceError("at least one accepted event is required")

        normalized_game_id = _coerce_uuid(game_id)
        normalized_actor_player_id = (
            None if actor_player_id is None else _coerce_uuid(actor_player_id)
        )
        effective_snapshot_interval = (
            self._snapshot_interval
            if snapshot_interval is None
            else _validate_snapshot_interval(snapshot_interval)
        )

        async with self._session_factory() as session:
            async with session.begin():
                state = await self._replay_current_state_for_append(session, normalized_game_id)
                return await self.append_accepted_events_to_locked_state(
                    session=session,
                    game_id=normalized_game_id,
                    state=state,
                    actor_player_id=normalized_actor_player_id,
                    event_templates=event_templates,
                    expected_base_sequence=expected_base_sequence,
                    expected_base_state_hash=expected_base_state_hash,
                    snapshot_interval=effective_snapshot_interval,
                )

    async def replay_current_state_for_update(
        self,
        session: AsyncSession,
        game_id: UUID | str,
    ) -> GameState:
        normalized_game_id = _coerce_uuid(game_id)
        return await self._replay_current_state_for_append(session, normalized_game_id)

    async def append_accepted_events_to_locked_state(
        self,
        *,
        session: AsyncSession,
        game_id: UUID | str,
        state: GameState,
        event_templates: Sequence[AcceptedEventTemplate],
        actor_player_id: UUID | str | None = None,
        expected_base_sequence: int | None = None,
        expected_base_state_hash: str | None = None,
        snapshot_interval: int | None = None,
    ) -> EventAppendManyResult:
        if not event_templates:
            raise EventPersistenceError("at least one accepted event is required")

        normalized_game_id = _coerce_uuid(game_id)
        normalized_actor_player_id = (
            None if actor_player_id is None else _coerce_uuid(actor_player_id)
        )
        effective_snapshot_interval = (
            self._snapshot_interval
            if snapshot_interval is None
            else _validate_snapshot_interval(snapshot_interval)
        )

        if expected_base_sequence is not None and state.event_sequence != expected_base_sequence:
            raise StaleEventSequenceError(
                f"expected base sequence {expected_base_sequence} does not match current "
                f"sequence {state.event_sequence}"
            )
        if expected_base_state_hash is not None and state.state_hash() != expected_base_state_hash:
            raise StaleEventSequenceError("expected base state hash does not match current state")

        records: list[AcceptedEventRecord] = []
        current_state = state
        for template in event_templates:
            next_sequence = current_state.event_sequence + 1
            event_id = uuid4()
            event = _build_game_event(
                event_id=event_id,
                sequence=next_sequence,
                event_type=template.event_type,
                payload=template.payload,
            )
            next_state = apply_event(current_state, event)
            state_hash = next_state.state_hash()

            result = await session.execute(
                game_events.insert()
                .values(
                    id=event_id,
                    game_id=normalized_game_id,
                    sequence=next_sequence,
                    actor_player_id=normalized_actor_player_id,
                    event_type=event.type,
                    payload=_payload_for_storage(event.payload),
                    state_hash=state_hash,
                )
                .returning(game_events)
            )
            records.append(_accepted_event_record_from_row(dict(result.mappings().one())))

            if next_sequence % effective_snapshot_interval == 0:
                await session.execute(
                    game_snapshots.insert().values(
                        game_id=normalized_game_id,
                        last_event_id=event_id,
                        event_sequence=next_sequence,
                        state_payload=next_state.model_dump(mode="json"),
                        state_hash=state_hash,
                    )
                )

            current_state = next_state

        await _update_game_current_state(session, normalized_game_id, current_state)

        return EventAppendManyResult(events=tuple(records), state=current_state)

    async def list_accepted_events(self, game_id: UUID | str) -> list[AcceptedEventRecord]:
        return await self.list_accepted_events_after(game_id, sequence=0)

    async def list_accepted_events_after(
        self,
        game_id: UUID | str,
        *,
        sequence: int,
    ) -> list[AcceptedEventRecord]:
        normalized_game_id = _coerce_uuid(game_id)
        async with self._session_factory() as session:
            await _load_initial_state(session, normalized_game_id)
            rows = await _load_event_rows_after(session, normalized_game_id, sequence=sequence)
            return [_accepted_event_record_from_row(row) for row in rows]

    async def replay_from_event_zero(self, game_id: UUID | str) -> GameState:
        normalized_game_id = _coerce_uuid(game_id)
        async with self._session_factory() as session:
            initial_state = await _load_initial_state(session, normalized_game_id)
            event_rows = await _load_event_rows_after(session, normalized_game_id, sequence=0)
            return _apply_event_rows(initial_state, event_rows)

    async def replay_from_latest_snapshot(self, game_id: UUID | str) -> GameState:
        normalized_game_id = _coerce_uuid(game_id)
        async with self._session_factory() as session:
            return await self._replay_from_latest_snapshot(session, normalized_game_id)

    async def verify_game_snapshots(self, game_id: UUID | str) -> SnapshotVerificationResult:
        normalized_game_id = _coerce_uuid(game_id)
        async with self._session_factory() as session:
            from_zero = await _load_initial_state(session, normalized_game_id)
            event_rows = await _load_event_rows_after(session, normalized_game_id, sequence=0)
            state_by_sequence = {0: from_zero}
            current_state = from_zero
            for event_row in event_rows:
                current_state = _apply_event_row(current_state, event_row)
                state_by_sequence[current_state.event_sequence] = current_state

            snapshot_rows = await _load_snapshot_rows(session, normalized_game_id)
            latest_snapshot_state = from_zero
            for snapshot_row in snapshot_rows:
                snapshot_state = _validate_snapshot_row(snapshot_row)
                replayed_state = state_by_sequence.get(int(snapshot_row["event_sequence"]))
                if replayed_state is None:
                    raise SnapshotCorruptionError(
                        "snapshot references an event sequence that does not exist"
                    )
                if snapshot_state.state_hash() != replayed_state.state_hash():
                    raise SnapshotCorruptionError(
                        "snapshot state does not match replayed state at its event sequence"
                    )
                latest_snapshot_state = snapshot_state

            from_latest = await self._replay_from_latest_snapshot(session, normalized_game_id)
            if from_latest.state_hash() != current_state.state_hash():
                raise SnapshotCorruptionError(
                    "latest snapshot replay does not match replay from event zero"
                )

            return SnapshotVerificationResult(
                game_id=normalized_game_id,
                event_count=len(event_rows),
                snapshot_count=len(snapshot_rows),
                replayed_state_hash=current_state.state_hash(),
                latest_snapshot_state_hash=from_latest.state_hash()
                if snapshot_rows
                else latest_snapshot_state.state_hash(),
            )

    async def _replay_current_state_for_append(
        self,
        session: AsyncSession,
        game_id: UUID,
    ) -> GameState:
        await _lock_game_row(session, game_id)
        return await self._replay_from_latest_snapshot(session, game_id)

    async def _replay_from_latest_snapshot(
        self,
        session: AsyncSession,
        game_id: UUID,
    ) -> GameState:
        snapshot_row = await _load_latest_snapshot_row(session, game_id)
        if snapshot_row is None:
            base_state = await _load_initial_state(session, game_id)
            from_sequence = 0
        else:
            base_state = _validate_snapshot_row(snapshot_row)
            from_sequence = int(snapshot_row["event_sequence"])

        event_rows = await _load_event_rows_after(session, game_id, sequence=from_sequence)
        return _apply_event_rows(base_state, event_rows)


async def _lock_game_row(session: AsyncSession, game_id: UUID) -> None:
    result = await session.execute(
        sa.select(games.c.id).where(games.c.id == game_id).with_for_update()
    )
    if result.scalar_one_or_none() is None:
        raise GameNotFoundError(f"game {game_id} was not found")


async def _load_initial_state(session: AsyncSession, game_id: UUID) -> GameState:
    result = await session.execute(
        sa.select(games.c.initial_state).where(games.c.id == game_id)
    )
    initial_state_payload = result.scalar_one_or_none()
    if initial_state_payload is None:
        raise GameNotFoundError(f"game {game_id} was not found")

    try:
        initial_state = GameState.model_validate(initial_state_payload)
    except (TypeError, ValueError, ValidationError) as exc:
        raise EventPersistenceError(f"game {game_id} initial state is invalid") from exc

    if initial_state.game_id != str(game_id):
        raise EventPersistenceError(
            f"game {game_id} initial state is for game {initial_state.game_id}"
        )
    if initial_state.event_sequence != 0:
        raise EventPersistenceError("initial state must have event sequence zero")
    return initial_state


async def _load_event_rows_after(
    session: AsyncSession,
    game_id: UUID,
    *,
    sequence: int,
) -> list[Mapping[str, Any]]:
    result = await session.execute(
        sa.select(game_events)
        .where(game_events.c.game_id == game_id, game_events.c.sequence > sequence)
        .order_by(game_events.c.sequence)
    )
    return [dict(row) for row in result.mappings().all()]


async def _load_snapshot_rows(
    session: AsyncSession,
    game_id: UUID,
) -> list[Mapping[str, Any]]:
    result = await session.execute(
        sa.select(game_snapshots)
        .where(game_snapshots.c.game_id == game_id)
        .order_by(game_snapshots.c.event_sequence)
    )
    return [dict(row) for row in result.mappings().all()]


async def _load_latest_snapshot_row(
    session: AsyncSession,
    game_id: UUID,
) -> Mapping[str, Any] | None:
    result = await session.execute(
        sa.select(game_snapshots)
        .where(game_snapshots.c.game_id == game_id)
        .order_by(game_snapshots.c.event_sequence.desc())
        .limit(1)
    )
    row = result.mappings().first()
    return None if row is None else dict(row)


def _apply_event_rows(state: GameState, event_rows: list[Mapping[str, Any]]) -> GameState:
    current_state = state
    for event_row in event_rows:
        current_state = _apply_event_row(current_state, event_row)
    return current_state


def _apply_event_row(state: GameState, event_row: Mapping[str, Any]) -> GameState:
    event = _event_from_row(event_row)
    try:
        next_state = apply_event(state, event)
    except InvalidEventError as exc:
        raise EventPersistenceError(
            f"event sequence {event_row['sequence']} cannot be replayed"
        ) from exc

    stored_hash = event_row.get("state_hash")
    if stored_hash is not None and next_state.state_hash() != stored_hash:
        raise EventPersistenceError(
            f"event sequence {event_row['sequence']} state hash does not match replay"
        )
    return next_state


def _event_from_row(event_row: Mapping[str, Any]) -> GameEvent:
    try:
        return GameEvent.model_validate(
            {
                "event_id": str(event_row["id"]),
                "sequence": event_row["sequence"],
                "type": event_row["event_type"],
                "payload": event_row["payload"],
            }
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise EventPersistenceError(
            f"event sequence {event_row.get('sequence')} payload is invalid"
        ) from exc


def _validate_snapshot_row(snapshot_row: Mapping[str, Any]) -> GameState:
    try:
        snapshot_state = GameState.model_validate(snapshot_row["state_payload"])
    except (TypeError, ValueError, ValidationError) as exc:
        raise SnapshotCorruptionError("snapshot payload is invalid") from exc

    event_sequence = int(snapshot_row["event_sequence"])
    if snapshot_state.event_sequence != event_sequence:
        raise SnapshotCorruptionError(
            "snapshot payload event sequence does not match snapshot row"
        )

    if snapshot_state.state_hash() != snapshot_row["state_hash"]:
        raise SnapshotCorruptionError("snapshot state hash does not match payload")

    return snapshot_state


def _build_game_event(
    *,
    event_id: UUID,
    sequence: int,
    event_type: str,
    payload: Mapping[str, Any],
) -> GameEvent:
    try:
        return GameEvent.model_validate(
            {
                "event_id": str(event_id),
                "sequence": sequence,
                "type": event_type,
                "payload": payload,
            }
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise EventPersistenceError(f"accepted event payload is invalid: {exc}") from exc


def _payload_for_storage(payload: EventModel) -> dict[str, Any]:
    return payload.model_dump(mode="json", exclude_unset=True)


async def _update_game_current_state(
    session: AsyncSession,
    game_id: UUID,
    state: GameState,
) -> None:
    await session.execute(
        games.update()
        .where(games.c.id == game_id)
        .values(current_phase=state.turn.phase.value, updated_at=sa.func.now())
    )
    for player_state in state.players:
        await session.execute(
            players.update()
            .where(players.c.game_id == game_id, players.c.id == _coerce_uuid(player_state.id))
            .values(
                status="bankrupt" if player_state.is_bankrupt else "active",
                state=player_state.model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
        )


def _accepted_event_record_from_row(row: Mapping[str, Any]) -> AcceptedEventRecord:
    return AcceptedEventRecord(
        id=row["id"],
        game_id=row["game_id"],
        sequence=int(row["sequence"]),
        actor_player_id=row["actor_player_id"],
        event_type=row["event_type"],
        payload=row["payload"],
        state_hash=row["state_hash"],
        created_at=row["created_at"],
    )


def _coerce_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _validate_snapshot_interval(snapshot_interval: int) -> int:
    if snapshot_interval < 1:
        raise ValueError("snapshot_interval must be at least 1")
    return snapshot_interval


__all__ = [
    "DEFAULT_SNAPSHOT_INTERVAL",
    "AcceptedEventRecord",
    "AcceptedEventTemplate",
    "EventAppendResult",
    "EventAppendManyResult",
    "EventPersistence",
    "EventPersistenceError",
    "GameNotFoundError",
    "SnapshotCorruptionError",
    "SnapshotVerificationResult",
    "StaleEventSequenceError",
]
