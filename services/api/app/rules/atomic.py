from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol


class AtomicResolutionKind(StrEnum):
    DICE_ROLL = "DICE_ROLL"
    MOVEMENT = "MOVEMENT"
    CARD_DRAW = "CARD_DRAW"
    CARD_EFFECT = "CARD_EFFECT"
    PAYMENT_CREATION = "PAYMENT_CREATION"
    FORCED_MOVEMENT = "FORCED_MOVEMENT"


ATOMIC_RESOLUTION_KIND_NAMES: Final = tuple(kind.value for kind in AtomicResolutionKind)


class _TurnLike(Protocol):
    @property
    def phase(self) -> object: ...


class _ActiveAtomicResolutionLike(Protocol):
    @property
    def kind(self) -> object: ...


class _StateLike(Protocol):
    @property
    def game_id(self) -> str: ...

    @property
    def event_sequence(self) -> int: ...

    @property
    def turn(self) -> _TurnLike: ...

    @property
    def active_atomic_resolution(self) -> _ActiveAtomicResolutionLike | None: ...

    def state_hash(self) -> str: ...


class _ActionLike(Protocol):
    @property
    def actor_id(self) -> str: ...

    @property
    def type(self) -> str: ...

    @property
    def payload(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class RejectedActionAuditEntry:
    game_id: str
    state_hash: str
    event_sequence: int
    phase: str
    active_atomic_kind: str | None
    actor_id: str
    action_type: str
    action_payload: Mapping[str, object] = field(default_factory=dict)
    errors: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", _string_value(self.phase))
        object.__setattr__(self, "active_atomic_kind", _optional_string_value(self.active_atomic_kind))
        object.__setattr__(self, "action_payload", _freeze_mapping(self.action_payload))
        object.__setattr__(
            self,
            "errors",
            tuple(_freeze_mapping(error) for error in self.errors),
        )

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        return {
            "game_id": self.game_id,
            "state_hash": self.state_hash,
            "event_sequence": self.event_sequence,
            "phase": self.phase,
            "active_atomic_kind": self.active_atomic_kind,
            "actor_id": self.actor_id,
            "action_type": self.action_type,
            "action_payload": _thaw_value(self.action_payload, mode=mode),
            "errors": tuple(_thaw_value(error, mode=mode) for error in self.errors),
        }


def build_rejected_action_audit_entry(
    state: _StateLike,
    action: _ActionLike,
    errors: Sequence[object],
) -> RejectedActionAuditEntry:
    active_atomic = state.active_atomic_resolution
    return RejectedActionAuditEntry(
        game_id=state.game_id,
        state_hash=state.state_hash(),
        event_sequence=state.event_sequence,
        phase=_string_value(state.turn.phase),
        active_atomic_kind=None if active_atomic is None else _string_value(active_atomic.kind),
        actor_id=action.actor_id,
        action_type=action.type,
        action_payload=action.payload,
        errors=tuple(_error_mapping(error) for error in errors),
    )


def is_atomic_section_active(state: _StateLike) -> bool:
    return state.active_atomic_resolution is not None


def _error_mapping(error: object) -> Mapping[str, object]:
    model_dump = getattr(error, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return {str(key): value for key, value in dumped.items()}
        return {"message": str(dumped)}
    if isinstance(error, Mapping):
        return {str(key): value for key, value in error.items()}
    return {"message": str(error)}


def _optional_string_value(value: object | None) -> str | None:
    if value is None:
        return None
    return _string_value(value)


def _string_value(value: object) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _freeze_mapping(mapping: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze_value(value) for key, value in mapping.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: object, *, mode: str) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(inner_value, mode=mode) for key, inner_value in value.items()}
    if isinstance(value, tuple):
        thawed_items = [_thaw_value(item, mode=mode) for item in value]
        return thawed_items if mode == "json" else tuple(thawed_items)
    return value


__all__ = [
    "ATOMIC_RESOLUTION_KIND_NAMES",
    "AtomicResolutionKind",
    "RejectedActionAuditEntry",
    "build_rejected_action_audit_entry",
    "is_atomic_section_active",
]
