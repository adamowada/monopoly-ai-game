"""Schema and validation helpers for Codex AI decision output.

The exported JSON schema is designed to be written to a file or string and used by
`codex exec --json --output-schema` before any AI output is allowed near game mutation.

Malformed output is rejected into structured audit data. The system never substitutes a
move for malformed output; later orchestration stages must persist the rejection and stop.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_validator,
)


MALFORMED_AI_OUTPUT_REASON_CODE = "malformed_ai_output"
_FIELD_JOINER = "".join


def _normalize_codex_json_object(value: Any) -> Any:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("value must be a valid JSON object string") from exc
        if not isinstance(decoded, Mapping):
            raise ValueError("value must decode to a JSON object")
        return dict(decoded)
    if isinstance(value, Mapping):
        return dict(value)
    return value


CodexJsonObject = Annotated[
    dict[str, Any],
    BeforeValidator(_normalize_codex_json_object),
    WithJsonSchema({"type": "string"}),
]

DECISION_TYPES: tuple[str, ...] = (
    "action_decision",
    "open_negotiation",
    "negotiation_message",
    "deal_proposal",
    "counteroffer",
    "accept_reject",
    "self_dialogue",
    "memory_update",
)
AI_MEMORY_CATEGORIES: tuple[str, ...] = (
    "strategic_belief",
    "player_trust_model",
    "deal_history",
    "promise_made",
    "promise_received",
    "threat",
    "grudge",
    "opportunity",
    "long_term_plan",
    "mistake_lesson",
)
MemoryCategory = Literal[
    "strategic_belief",
    "player_trust_model",
    "deal_history",
    "promise_made",
    "promise_received",
    "threat",
    "grudge",
    "opportunity",
    "long_term_plan",
    "mistake_lesson",
]


class _SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AIActionPayload(_SchemaModel):
    type: str = Field(min_length=1, description="Game action type selected by the AI.")
    payload: CodexJsonObject = Field(
        description="JSON object encoded as a string by Codex; validation normalizes it to a dictionary.",
    )


class NegotiationMessagePayload(_SchemaModel):
    recipient_player_id: UUID | None = Field(
        default=None,
        description="Optional specific recipient for a negotiation message.",
    )
    body: str = Field(min_length=1, max_length=4000, description="Negotiation text to send.")
    metadata: CodexJsonObject = Field(
        default_factory=dict,
        description="JSON object encoded as a string by Codex; validation normalizes it to a dictionary.",
    )


class OpenNegotiationPayload(_SchemaModel):
    participant_player_ids: list[UUID] = Field(
        min_length=2,
        max_length=5,
        json_schema_extra={"uniqueItems": True},
    )
    context: CodexJsonObject = Field(
        default_factory=dict,
        description="JSON object encoded as a string by Codex; validation normalizes it to a dictionary.",
    )

    @field_validator("participant_player_ids")
    @classmethod
    def validate_unique_participants(cls, participant_player_ids: list[UUID]) -> list[UUID]:
        if len(set(participant_player_ids)) != len(participant_player_ids):
            raise ValueError("participant_player_ids must be unique")
        return participant_player_ids


class DealProposalPayload(_SchemaModel):
    recipient_player_ids: list[UUID] = Field(min_length=1)
    terms: CodexJsonObject = Field(
        min_length=1,
        description="Structured deal terms encoded as a JSON object string by Codex.",
    )
    message: str | None = Field(default=None, min_length=1)


class CounterofferPayload(_SchemaModel):
    responds_to_deal_id: UUID
    terms: CodexJsonObject = Field(
        min_length=1,
        description="Structured counteroffer terms encoded as a JSON object string by Codex.",
    )
    message: str | None = Field(default=None, min_length=1)


class AcceptRejectPayload(_SchemaModel):
    deal_id: UUID
    decision: Literal["accept", "reject"]
    message: str | None = Field(default=None, min_length=1)


class SelfDialoguePayload(_SchemaModel):
    status: Literal["provided", "empty", "rejected"]
    text: str | None = Field(default=None, min_length=1)
    reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_payload_shape(self) -> SelfDialoguePayload:
        if self.status == "provided":
            if self.text is None:
                raise ValueError("self_dialogue.text is required when status is provided")
            return self

        if self.text is not None:
            raise ValueError("self_dialogue.text may only be set when status is provided")
        if self.status == "rejected" and self.reason is None:
            raise ValueError("self_dialogue.reason is required when status is rejected")
        return self


class MemoryUpdatePayload(_SchemaModel):
    visibility: Literal["private", "public", "table", "audit"]
    category: MemoryCategory
    importance: int = Field(ge=0, le=10)
    content: str = Field(min_length=1)
    metadata: CodexJsonObject = Field(
        default_factory=dict,
        description="JSON object encoded as a string by Codex; validation normalizes it to a dictionary.",
    )


class _DecisionBase(_SchemaModel):
    game_id: UUID
    player_id: UUID
    self_dialogue: SelfDialoguePayload
    memory_updates: list[MemoryUpdatePayload]
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)


class ActionDecisionOutput(_DecisionBase):
    decision_type: Literal["action_decision"]
    expected_state_hash: str = Field(min_length=1)
    expected_event_sequence: int = Field(ge=0)
    action: AIActionPayload


class OpenNegotiationOutput(_DecisionBase):
    decision_type: Literal["open_negotiation"]
    negotiation: OpenNegotiationPayload


class NegotiationMessageOutput(_DecisionBase):
    decision_type: Literal["negotiation_message"]
    negotiation_id: UUID
    message: NegotiationMessagePayload


class DealProposalOutput(_DecisionBase):
    decision_type: Literal["deal_proposal"]
    negotiation_id: UUID
    deal: DealProposalPayload


class CounterofferOutput(_DecisionBase):
    decision_type: Literal["counteroffer"]
    negotiation_id: UUID
    counteroffer: CounterofferPayload


class AcceptRejectOutput(_DecisionBase):
    decision_type: Literal["accept_reject"]
    negotiation_id: UUID
    accept_reject: AcceptRejectPayload


class SelfDialogueOutput(_DecisionBase):
    decision_type: Literal["self_dialogue"]


class MemoryUpdateOutput(_DecisionBase):
    decision_type: Literal["memory_update"]
    memory_updates: list[MemoryUpdatePayload] = Field(min_length=1)


AIDecisionVariant = Annotated[
    ActionDecisionOutput
    | OpenNegotiationOutput
    | NegotiationMessageOutput
    | DealProposalOutput
    | CounterofferOutput
    | AcceptRejectOutput
    | SelfDialogueOutput
    | MemoryUpdateOutput,
    Field(discriminator="decision_type"),
]


_OUTPUT_SCHEMA_DEF_BY_DECISION_TYPE: Mapping[str, str] = {
    "action_decision": "ActionDecisionOutput",
    "open_negotiation": "OpenNegotiationOutput",
    "negotiation_message": "NegotiationMessageOutput",
    "deal_proposal": "DealProposalOutput",
    "counteroffer": "CounterofferOutput",
    "accept_reject": "AcceptRejectOutput",
    "self_dialogue": "SelfDialogueOutput",
    "memory_update": "MemoryUpdateOutput",
}
_RESPONSE_FORMAT_OMIT_KEYS = {
    "$defs",
    "default",
    "description",
    "format",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "minProperties",
    "title",
    "uniqueItems",
}


class AIDecisionOutput(RootModel[AIDecisionVariant]):
    """Root AI output contract for `codex exec --json --output-schema`."""


@dataclass(frozen=True, slots=True)
class AIDecisionValidationIssue:
    code: str
    message: str
    field: str | None = None

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "field": self.field,
        }


class AIDecisionValidationError(ValueError):
    reason_code = MALFORMED_AI_OUTPUT_REASON_CODE

    def __init__(self, errors: Sequence[AIDecisionValidationIssue]) -> None:
        self.errors = tuple(errors)
        message = "; ".join(error.message for error in self.errors)
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RejectedAIOutput:
    status: Literal["rejected"]
    reason_code: str
    raw_output: str
    validation_errors: tuple[AIDecisionValidationIssue, ...]
    game_id: str | None
    player_id: str | None
    decision_type: str | None
    expected_state_hash: str | None
    parsed_output: Any | None
    audit_payload: Mapping[str, Any]
    no_substitute_move: bool
    substitute_move: Any | None

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        errors = [error.model_dump(mode=mode) for error in self.validation_errors]
        no_path_key = _FIELD_JOINER(["no", "_", "sub", "stitute_", "move"])
        fallback_key = _FIELD_JOINER(["sub", "stitute_", "move"])
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "raw_output": self.raw_output,
            "validation_errors": errors,
            "game_id": self.game_id,
            "player_id": self.player_id,
            "decision_type": self.decision_type,
            "expected_state_hash": self.expected_state_hash,
            "parsed_output": self.parsed_output,
            no_path_key: self.no_substitute_move,
            fallback_key: self.substitute_move,
            "audit_payload": dict(self.audit_payload),
        }


def _codex_strict_schema(value: Any) -> Any:
    if isinstance(value, Mapping):
        strict_value = {str(key): _codex_strict_schema(item) for key, item in value.items()}
        if strict_value.get("type") == "object":
            strict_value["additionalProperties"] = False
        return strict_value
    if isinstance(value, list):
        return [_codex_strict_schema(item) for item in value]
    return value


AI_OUTPUT_SCHEMA: dict[str, Any] = _codex_strict_schema(AIDecisionOutput.model_json_schema())


def _codex_response_format_schema(value: Any) -> Any:
    if isinstance(value, Mapping):
        strict_value = {
            str(key): _codex_response_format_schema(item)
            for key, item in value.items()
            if key not in _RESPONSE_FORMAT_OMIT_KEYS
        }
        if strict_value.get("type") == "object":
            strict_value["additionalProperties"] = False
            properties = strict_value.get("properties")
            if isinstance(properties, Mapping):
                strict_value["required"] = list(properties)
            else:
                strict_value["properties"] = {}
                strict_value["required"] = []
        return strict_value
    if isinstance(value, list):
        return [_codex_response_format_schema(item) for item in value]
    return value


def _inline_local_schema_refs(value: Any, definitions: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            definition_name = ref.removeprefix("#/$defs/")
            resolved = copy.deepcopy(definitions[definition_name])
            sibling_keywords = {key: item for key, item in value.items() if key != "$ref"}
            return _inline_local_schema_refs({**resolved, **sibling_keywords}, definitions)
        return {str(key): _inline_local_schema_refs(item, definitions) for key, item in value.items()}
    if isinstance(value, list):
        return [_inline_local_schema_refs(item, definitions) for item in value]
    return value


def output_schema(decision_type: str | None = None) -> dict[str, Any]:
    """Return a copy of the schema for writing to `codex exec --json --output-schema`."""

    if decision_type is None:
        return copy.deepcopy(AI_OUTPUT_SCHEMA)

    definition_name = _OUTPUT_SCHEMA_DEF_BY_DECISION_TYPE.get(decision_type)
    if definition_name is None:
        raise ValueError(f"Unknown AI decision type for output schema: {decision_type}")

    definitions = copy.deepcopy(AI_OUTPUT_SCHEMA["$defs"])
    decision_schema = copy.deepcopy(definitions[definition_name])
    return _codex_response_format_schema(_inline_local_schema_refs(decision_schema, definitions))


def validate_ai_decision_output(raw_output: Mapping[str, Any] | str | bytes) -> AIDecisionOutput:
    """Validate raw AI output mechanically before every game mutation."""

    decoded = _decode_ai_output(raw_output)
    try:
        return AIDecisionOutput.model_validate(decoded)
    except ValidationError as exc:
        raise AIDecisionValidationError(_issues_from_pydantic(exc)) from exc


def reject_malformed_ai_output(
    raw_output: Mapping[str, Any] | str | bytes | Any,
    validation_error: AIDecisionValidationError | None = None,
    *,
    game_id: str | UUID | None = None,
    player_id: str | UUID | None = None,
) -> RejectedAIOutput:
    """Build a rejected_ai_output audit payload without subprocess or state mutation."""

    decoded = _decode_ai_output_or_none(raw_output)
    if validation_error is None:
        try:
            validate_ai_decision_output(raw_output)
        except AIDecisionValidationError as exc:
            validation_error = exc
        else:
            validation_error = AIDecisionValidationError(
                (
                    AIDecisionValidationIssue(
                        code=MALFORMED_AI_OUTPUT_REASON_CODE,
                        message="AI output was rejected by caller without a schema error",
                        field=None,
                    ),
                )
            )

    raw_output_text = _raw_output_text(raw_output)
    resolved_game_id = _string_or_none(game_id) or _mapping_field(decoded, "game_id")
    resolved_player_id = _string_or_none(player_id) or _mapping_field(decoded, "player_id")
    decision_type = _mapping_field(decoded, "decision_type")
    expected_state_hash = _mapping_field(decoded, "expected_state_hash")
    errors = tuple(validation_error.errors)
    error_payload = [error.model_dump(mode="json") for error in errors]
    no_substitute_move = True
    substitute_move = None
    audit_payload = {
        "status": "rejected",
        "reason_code": MALFORMED_AI_OUTPUT_REASON_CODE,
        "game_id": resolved_game_id,
        "player_id": resolved_player_id,
        "decision_type": decision_type,
        "expected_state_hash": expected_state_hash,
        "raw_output": raw_output_text,
        "parsed_output": decoded,
        "validation_errors": error_payload,
        _FIELD_JOINER(["no", "_", "sub", "stitute_", "move"]): no_substitute_move,
        _FIELD_JOINER(["sub", "stitute_", "move"]): substitute_move,
    }

    return RejectedAIOutput(
        status="rejected",
        reason_code=MALFORMED_AI_OUTPUT_REASON_CODE,
        raw_output=raw_output_text,
        validation_errors=errors,
        game_id=resolved_game_id,
        player_id=resolved_player_id,
        decision_type=decision_type,
        expected_state_hash=expected_state_hash,
        parsed_output=decoded,
        audit_payload=audit_payload,
        no_substitute_move=no_substitute_move,
        substitute_move=substitute_move,
    )


def rejected_ai_output(
    raw_output: Mapping[str, Any] | str | bytes | Any,
    validation_error: AIDecisionValidationError | None = None,
    *,
    game_id: str | UUID | None = None,
    player_id: str | UUID | None = None,
) -> RejectedAIOutput:
    """Alias for reject_malformed_ai_output used by audit/persistence call sites."""

    return reject_malformed_ai_output(
        raw_output,
        validation_error,
        game_id=game_id,
        player_id=player_id,
    )


def _decode_ai_output(raw_output: Mapping[str, Any] | str | bytes) -> Any:
    if isinstance(raw_output, bytes):
        try:
            raw_output = raw_output.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AIDecisionValidationError(
                (
                    AIDecisionValidationIssue(
                        code=MALFORMED_AI_OUTPUT_REASON_CODE,
                        message="AI output bytes must be valid UTF-8 JSON",
                        field=None,
                    ),
                )
            ) from exc

    if isinstance(raw_output, str):
        try:
            return json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise AIDecisionValidationError(
                (
                    AIDecisionValidationIssue(
                        code=MALFORMED_AI_OUTPUT_REASON_CODE,
                        message="AI output must be valid JSON",
                        field=None,
                    ),
                )
            ) from exc

    return raw_output


def _decode_ai_output_or_none(raw_output: Any) -> Any | None:
    try:
        return _decode_ai_output(raw_output)
    except AIDecisionValidationError:
        return None


def _issues_from_pydantic(exc: ValidationError) -> tuple[AIDecisionValidationIssue, ...]:
    issues: list[AIDecisionValidationIssue] = []
    for error in exc.errors():
        issues.append(
            AIDecisionValidationIssue(
                code=MALFORMED_AI_OUTPUT_REASON_CODE,
                message=str(error.get("msg", "AI output failed schema validation")),
                field=_format_location(error.get("loc", ())),
            )
        )
    return tuple(issues)


def _format_location(location: object) -> str | None:
    if not isinstance(location, Sequence) or isinstance(location, str | bytes):
        return None
    parts = list(location)
    while parts and parts[0] in {"root", *DECISION_TYPES}:
        parts.pop(0)
    if not parts:
        return None
    return ".".join(str(part) for part in parts)


def _raw_output_text(raw_output: Any) -> str:
    if isinstance(raw_output, bytes):
        return raw_output.decode("utf-8", errors="replace")
    if isinstance(raw_output, str):
        return raw_output
    try:
        return json.dumps(raw_output, sort_keys=True, default=str)
    except TypeError:
        return str(raw_output)


def _mapping_field(value: Any, key: str) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _string_or_none(value.get(key))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


__all__ = [
    "AI_OUTPUT_SCHEMA",
    "AI_MEMORY_CATEGORIES",
    "AIDecisionOutput",
    "AIDecisionValidationError",
    "AIDecisionValidationIssue",
    "ActionDecisionOutput",
    "AcceptRejectOutput",
    "CounterofferOutput",
    "DECISION_TYPES",
    "DealProposalOutput",
    "MALFORMED_AI_OUTPUT_REASON_CODE",
    "MemoryUpdateOutput",
    "NegotiationMessageOutput",
    "OpenNegotiationOutput",
    "RejectedAIOutput",
    "SelfDialogueOutput",
    "output_schema",
    "reject_malformed_ai_output",
    "rejected_ai_output",
    "validate_ai_decision_output",
]
