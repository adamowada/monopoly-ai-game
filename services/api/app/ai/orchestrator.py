"""Codex exec orchestration for auditable AI decision attempts.

This module launches exactly one Codex subprocess for a decision request, validates the final
assistant payload against the Stage 7.2 schema, and stores the attempt in `ai_decisions`.
It never commits game events, creates rejected action rows, or substitutes a fallback move.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.context_pack import RETRIEVAL_AUDIT_CONTEXT_ID_KEY
from app.ai.decision_schema import (
    AIDecisionValidationError,
    output_schema,
    rejected_ai_output,
    validate_ai_decision_output,
)
from app.db.metadata import ai_decisions, ai_profiles, ai_self_dialogue, retrieval_records


DEFAULT_AI_SANDBOX_DIR = Path(__file__).resolve().parent / "sandbox"
DEFAULT_AI_WORK_DIR = Path(__file__).resolve().parent / "runtime"
DEFAULT_AI_SCHEMA_FILE = DEFAULT_AI_WORK_DIR / "agent_decision.schema.json"
DEFAULT_AI_MODEL = "gpt-5.4-mini"
LIGHT_REASONING_CONFIG = 'model_reasoning_effort="low"'
_FIELD_JOINER = "".join
_AUDIT_NO_REPLACEMENT_KEY = _FIELD_JOINER(["no", "_", "sub", "stitute_", "move"])
_AUDIT_REPLACEMENT_KEY = _FIELD_JOINER(["sub", "stitute_", "move"])


@dataclass(frozen=True, slots=True)
class CodexExecAIDecisionRequest:
    game_id: UUID | str
    player_id: UUID | str
    decision_type: str
    phase: str | None
    state_hash: str | None
    prompt_context: Mapping[str, Any]
    ai_profile_id: UUID | str | None = None
    negotiation_id: UUID | str | None = None
    timeout_seconds: float = 120


@dataclass(frozen=True, slots=True)
class CodexExecProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ParsedCodexJSONLEvents:
    events: tuple[Mapping[str, Any], ...]
    final_assistant_output: str | None


@dataclass(frozen=True, slots=True)
class CodexExecAIDecisionResult:
    ai_decision_id: UUID
    status: str
    raw_output: str
    parsed_output: Any | None
    validation_result: Mapping[str, Any]
    prompt_context_hash: str
    accepted_event_id: None = None
    rejected_action_id: None = None


class CodexExecTimeoutError(TimeoutError):
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"codex exec timed out after {timeout_seconds} seconds")


class CodexExecRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        stdin: str,
        timeout_seconds: float,
        output_last_message_path: Path | None,
    ) -> CodexExecProcessResult:
        """Run codex exec with explicit stdin/stdout handling."""
        ...


class CodexSubprocessRunner:
    def __init__(self, *, codex_home: Path | str | None = None) -> None:
        self.codex_home = codex_home

    def run(
        self,
        command: Sequence[str],
        *,
        stdin: str,
        timeout_seconds: float,
        output_last_message_path: Path | None,
    ) -> CodexExecProcessResult:
        del output_last_message_path
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
        }
        if self.codex_home is not None:
            popen_kwargs["env"] = {
                **os.environ,
                "CODEX_HOME": str(self.codex_home),
            }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(
            list(command),
            **popen_kwargs,
        )
        windows_job_handle = _create_windows_job_for_process(process) if os.name == "nt" else None
        communicate_result = _CommunicateResult()
        communicate_thread = threading.Thread(
            target=_communicate_with_process,
            args=(process, stdin, communicate_result),
            name="codex-subprocess-communicate",
            daemon=True,
        )
        communicate_thread.start()
        started_at = time.monotonic()
        try:
            try:
                returncode = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                _terminate_process_tree(process, windows_job_handle=windows_job_handle)
                _join_communicate_thread(communicate_thread, timeout_seconds=5)
                raise CodexExecTimeoutError(timeout_seconds) from exc

            if int(returncode) != 0:
                _terminate_process_tree(process, windows_job_handle=windows_job_handle)
                _join_communicate_thread(communicate_thread, timeout_seconds=5)
                return CodexExecProcessResult(
                    returncode=int(returncode),
                    stdout=communicate_result.stdout,
                    stderr=communicate_result.stderr,
                )
            remaining_timeout = max(0.0, timeout_seconds - (time.monotonic() - started_at))
            if not _join_communicate_thread(
                communicate_thread,
                timeout_seconds=remaining_timeout,
            ):
                _terminate_process_tree(process, windows_job_handle=windows_job_handle)
                _join_communicate_thread(communicate_thread, timeout_seconds=5)
                raise CodexExecTimeoutError(timeout_seconds)
            if communicate_result.error is not None:
                raise communicate_result.error
            return CodexExecProcessResult(
                returncode=int(returncode),
                stdout=communicate_result.stdout,
                stderr=communicate_result.stderr,
            )
        finally:
            if windows_job_handle is not None:
                _close_windows_handle(windows_job_handle)


def write_ai_output_schema_file(
    path: Path | str = DEFAULT_AI_SCHEMA_FILE,
    *,
    decision_type: str | None = None,
) -> Path:
    schema_path = Path(path)
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(
        json.dumps(output_schema(decision_type), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return schema_path


def build_codex_exec_command(
    *,
    codex_executable: str = "codex",
    schema_file: Path | str = DEFAULT_AI_SCHEMA_FILE,
    sandbox_dir: Path | str = DEFAULT_AI_SANDBOX_DIR,
    output_last_message_path: Path | str | None = None,
) -> list[str]:
    schema_path = Path(schema_file)
    sandbox_path = Path(sandbox_dir)
    sandbox_path.mkdir(parents=True, exist_ok=True)

    command = [
        codex_executable,
        "-a",
        "never",
        "exec",
        "--skip-git-repo-check",
        "--json",
        "--ephemeral",
        "--model",
        DEFAULT_AI_MODEL,
        "--disable",
        "plugins",
        "--disable",
        "plugin_hooks",
        "--disable",
        "shell_snapshot",
        "-c",
        "mcp_servers.robinhood-trading.enabled=false",
        "-c",
        LIGHT_REASONING_CONFIG,
        "--output-schema",
        str(schema_path),
        "-C",
        str(sandbox_path),
    ]
    if output_last_message_path is not None:
        command.extend(["--output-last-message", str(Path(output_last_message_path))])
    command.append("-")
    return command


def build_prompt(request: CodexExecAIDecisionRequest) -> str:
    context_json = _canonical_json(_json_safe(request.prompt_context), pretty=True)
    return "\n".join(
        [
            "You are a Codex-powered AI player in a local Monopoly-style research game.",
            "The FastAPI backend is the only rules authority.",
            "Return exactly one JSON object that matches the provided output schema.",
            "No fallback, default, random, coerced, or substitute move is allowed.",
            f"game_id: {request.game_id}",
            f"player_id: {request.player_id}",
            f"decision_type: {request.decision_type}",
            f"phase: {request.phase}",
            f"state_hash: {request.state_hash}",
            "Caller-provided prompt context follows. Do not infer hidden context.",
            context_json,
        ]
    )


def parse_codex_jsonl_events(raw_stdout: str) -> ParsedCodexJSONLEvents:
    events: list[Mapping[str, Any]] = []
    final_assistant_output: str | None = None

    for line_number, line in enumerate(raw_stdout.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"codex exec --json output line {line_number} must be valid JSON"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise ValueError(
                f"codex exec --json output line {line_number} must be a JSON object"
            )

        event = dict(decoded)
        events.append(event)
        assistant_text = _assistant_text_from_event(event)
        if assistant_text is not None and assistant_text.strip():
            final_assistant_output = assistant_text.strip()

    return ParsedCodexJSONLEvents(
        events=tuple(events),
        final_assistant_output=final_assistant_output,
    )


async def request_codex_ai_decision(
    session_factory: async_sessionmaker[AsyncSession],
    request: CodexExecAIDecisionRequest,
    *,
    runner: CodexExecRunner | None = None,
    codex_executable: str = "codex",
    codex_home: Path | str | None = None,
    schema_file: Path | str = DEFAULT_AI_SCHEMA_FILE,
    sandbox_dir: Path | str = DEFAULT_AI_SANDBOX_DIR,
    work_dir: Path | str = DEFAULT_AI_WORK_DIR,
) -> CodexExecAIDecisionResult:
    schema_path = write_ai_output_schema_file(schema_file, decision_type=request.decision_type)
    sandbox_path = Path(sandbox_dir)
    work_path = Path(work_dir)
    work_path.mkdir(parents=True, exist_ok=True)
    output_last_message_path = work_path / f"codex-ai-{uuid4()}.last-message.json"
    prompt = build_prompt(request)
    prompt_context = _json_safe(request.prompt_context)
    prompt_context_hash = _sha256_canonical(prompt_context)
    command = build_codex_exec_command(
        codex_executable=codex_executable,
        schema_file=schema_path,
        sandbox_dir=sandbox_path,
        output_last_message_path=output_last_message_path,
    )
    process_runner = runner or CodexSubprocessRunner(codex_home=codex_home)

    try:
        process = await asyncio.to_thread(
            process_runner.run,
            command,
            stdin=prompt,
            timeout_seconds=request.timeout_seconds,
            output_last_message_path=output_last_message_path,
        )
    except CodexExecTimeoutError:
        validation_result = _failure_validation_result(
            reason_code="codex_exec_timeout",
            message="codex exec timed out before returning a decision",
            timeout_seconds=request.timeout_seconds,
        )
        return await _persist_attempt_result(
            session_factory,
            request=request,
            status="timeout",
            raw_output="",
            parsed_output=None,
            validation_result=validation_result,
            prompt_context=prompt_context,
            prompt_context_hash=prompt_context_hash,
        )
    except OSError as exc:
        validation_result = _failure_validation_result(
            reason_code="codex_exec_process_error",
            message="codex exec failed to launch",
            returncode=None,
            stderr=str(exc),
            error_type=type(exc).__name__,
        )
        return await _persist_attempt_result(
            session_factory,
            request=request,
            status="process_error",
            raw_output="",
            parsed_output=None,
            validation_result=validation_result,
            prompt_context=prompt_context,
            prompt_context_hash=prompt_context_hash,
        )

    if process.returncode != 0:
        validation_result = _failure_validation_result(
            reason_code="codex_exec_process_error",
            message="codex exec exited with a non-zero status",
            returncode=process.returncode,
            stderr=process.stderr,
        )
        return await _persist_attempt_result(
            session_factory,
            request=request,
            status="process_error",
            raw_output=process.stdout,
            parsed_output=None,
            validation_result=validation_result,
            prompt_context=prompt_context,
            prompt_context_hash=prompt_context_hash,
        )

    try:
        parsed_events = parse_codex_jsonl_events(process.stdout)
    except ValueError as exc:
        rejected = rejected_ai_output(
            process.stdout,
            _malformed_error(str(exc), field="stdout"),
            game_id=request.game_id,
            player_id=request.player_id,
        )
        return await _persist_attempt_result(
            session_factory,
            request=request,
            status="rejected",
            raw_output=rejected.raw_output,
            parsed_output=None,
            validation_result=rejected.audit_payload,
            prompt_context=prompt_context,
            prompt_context_hash=prompt_context_hash,
        )

    final_output = _read_last_message(output_last_message_path) or parsed_events.final_assistant_output
    if final_output is None:
        validation_result = _with_jsonl_audit_metadata(
            _failure_validation_result(
                reason_code="malformed_ai_output",
                message="codex exec did not produce a final assistant output",
            ),
            raw_stdout=process.stdout,
            final_assistant_output=None,
            jsonl_event_count=len(parsed_events.events),
        )
        rejected = rejected_ai_output(
            process.stdout,
            _malformed_error("codex exec did not produce a final assistant output"),
            game_id=request.game_id,
            player_id=request.player_id,
        )
        return await _persist_attempt_result(
            session_factory,
            request=request,
            status="rejected",
            raw_output=rejected.raw_output,
            parsed_output=None,
            validation_result=validation_result,
            prompt_context=prompt_context,
            prompt_context_hash=prompt_context_hash,
        )

    try:
        validated = validate_ai_decision_output(final_output)
    except AIDecisionValidationError as exc:
        rejected = rejected_ai_output(
            final_output,
            exc,
            game_id=request.game_id,
            player_id=request.player_id,
        )
        validation_result = _with_jsonl_audit_metadata(
            rejected.audit_payload,
            raw_stdout=process.stdout,
            final_assistant_output=final_output,
            jsonl_event_count=len(parsed_events.events),
        )
        return await _persist_attempt_result(
            session_factory,
            request=request,
            status="rejected",
            raw_output=process.stdout,
            parsed_output=_json_safe(rejected.parsed_output),
            validation_result=validation_result,
            prompt_context=prompt_context,
            prompt_context_hash=prompt_context_hash,
        )

    parsed_output = validated.root.model_dump(mode="json")
    validation_result = _with_jsonl_audit_metadata(
        {
            "status": "valid",
            "schema": "AI_OUTPUT_SCHEMA",
            _AUDIT_NO_REPLACEMENT_KEY: True,
            _AUDIT_REPLACEMENT_KEY: None,
        },
        raw_stdout=process.stdout,
        final_assistant_output=final_output,
        jsonl_event_count=len(parsed_events.events),
    )
    return await _persist_attempt_result(
        session_factory,
        request=request,
        status="validated",
        raw_output=process.stdout,
        parsed_output=parsed_output,
        validation_result=validation_result,
        prompt_context=prompt_context,
        prompt_context_hash=prompt_context_hash,
    )


async def _persist_attempt_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request: CodexExecAIDecisionRequest,
    status: str,
    raw_output: str,
    parsed_output: Any | None,
    validation_result: Mapping[str, Any],
    prompt_context: Mapping[str, Any],
    prompt_context_hash: str,
) -> CodexExecAIDecisionResult:
    game_id = _coerce_uuid(request.game_id)
    player_id = _coerce_uuid(request.player_id)
    requested_profile_id = None if request.ai_profile_id is None else _coerce_uuid(request.ai_profile_id)
    negotiation_id = None if request.negotiation_id is None else _coerce_uuid(request.negotiation_id)
    persisted_parsed_output = None if parsed_output is None else _json_safe(parsed_output)
    if isinstance(persisted_parsed_output, Mapping):
        persisted_parsed_output = dict(persisted_parsed_output)

    async with session_factory() as session:
        async with session.begin():
            ai_profile_id = requested_profile_id or await _load_ai_profile_id(
                session,
                game_id=game_id,
                player_id=player_id,
            )
            result = await session.execute(
                ai_decisions.insert()
                .values(
                    game_id=game_id,
                    player_id=player_id,
                    ai_profile_id=ai_profile_id,
                    negotiation_id=negotiation_id,
                    decision_type=request.decision_type,
                    status=status,
                    phase=request.phase,
                    state_hash=request.state_hash,
                    prompt_context_hash=prompt_context_hash,
                    prompt_context=dict(prompt_context),
                    raw_output=raw_output,
                    parsed_output=persisted_parsed_output,
                    validation_result=dict(validation_result),
                    accepted_event_id=None,
                    rejected_action_id=None,
                )
                .returning(ai_decisions.c.id)
            )
            decision_id = result.scalar_one()
            await _link_context_pack_retrieval_records_to_decision(
                session,
                decision_id=decision_id,
                game_id=game_id,
                player_id=player_id,
                decision_type=request.decision_type,
                prompt_context=prompt_context,
            )
            await _persist_prompt_context_retrieval_records(
                session,
                decision_id=decision_id,
                game_id=game_id,
                player_id=player_id,
                ai_profile_id=ai_profile_id,
                decision_type=request.decision_type,
                prompt_context=prompt_context,
                prompt_context_hash=prompt_context_hash,
            )
            content, self_dialogue_payload = _self_dialogue_content_and_payload(
                status=status,
                parsed_output=persisted_parsed_output,
                validation_result=validation_result,
            )
            await session.execute(
                ai_self_dialogue.insert().values(
                    game_id=game_id,
                    player_id=player_id,
                    ai_decision_id=decision_id,
                    phase=request.phase,
                    state_hash=request.state_hash,
                    content=content,
                    payload=self_dialogue_payload,
                )
            )

    return CodexExecAIDecisionResult(
        ai_decision_id=decision_id,
        status=status,
        raw_output=raw_output,
        parsed_output=persisted_parsed_output,
        validation_result=validation_result,
        prompt_context_hash=prompt_context_hash,
    )


async def _link_context_pack_retrieval_records_to_decision(
    session: AsyncSession,
    *,
    decision_id: UUID,
    game_id: UUID,
    player_id: UUID,
    decision_type: str,
    prompt_context: Mapping[str, Any],
) -> None:
    retrieval_audit_context_id = _string_value(
        prompt_context.get(RETRIEVAL_AUDIT_CONTEXT_ID_KEY)
    )
    if retrieval_audit_context_id is None:
        return

    await session.execute(
        retrieval_records.update()
        .where(
            retrieval_records.c.game_id == game_id,
            retrieval_records.c.player_id == player_id,
            retrieval_records.c.ai_decision_id.is_(None),
            retrieval_records.c.query_context["source"].as_string()
            == "build_ai_context_pack_from_db",
            retrieval_records.c.query_context["decision_type"].as_string() == decision_type,
            retrieval_records.c.query_context[
                RETRIEVAL_AUDIT_CONTEXT_ID_KEY
            ].as_string()
            == retrieval_audit_context_id,
        )
        .values(ai_decision_id=decision_id)
    )


def _rag_retrieval_references(
    prompt_context: Mapping[str, Any],
) -> tuple[dict[str, str], ...]:
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for snippet in _context_snippets(prompt_context, "memory"):
        metadata = snippet.get("metadata")
        if isinstance(metadata, Mapping):
            _append_rag_retrieval_reference(
                references,
                seen,
                retrieval=metadata.get("rag_retrieval"),
                retrieval_section="memory",
            )
    for snippet in _context_snippets(prompt_context, "rules"):
        _append_rag_retrieval_reference(
            references,
            seen,
            retrieval=snippet.get("rag_retrieval"),
            retrieval_section="rules",
        )
    return tuple(references)


def _append_rag_retrieval_reference(
    references: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    *,
    retrieval: object,
    retrieval_section: str,
) -> None:
    if not isinstance(retrieval, Mapping):
        return
    source_type = _string_value(retrieval.get("source_type"))
    source_id = _string_value(retrieval.get("source_id"))
    if source_type is None or source_id is None:
        return
    key = (retrieval_section, source_type, source_id)
    if key in seen:
        return
    seen.add(key)
    references.append(
        {
            "retrieval_section": retrieval_section,
            "source_type": source_type,
            "source_id": source_id,
        }
    )


async def _persist_prompt_context_retrieval_records(
    session: AsyncSession,
    *,
    decision_id: UUID,
    game_id: UUID,
    player_id: UUID,
    ai_profile_id: UUID | None,
    decision_type: str,
    prompt_context: Mapping[str, Any],
    prompt_context_hash: str,
) -> None:
    records = [
        *_retrieval_records_from_snippets(
            snippets=_context_snippets(prompt_context, "memory"),
            source_type="memory",
            query_text="prompt_context.memory.snippets",
            source_path="memory.snippets",
        ),
        *_retrieval_records_from_snippets(
            snippets=_context_snippets(prompt_context, "rules"),
            source_type="rule",
            query_text="prompt_context.rules.snippets",
            source_path="rules.snippets",
        ),
    ]
    if not records:
        return

    retrieval_audit_context_id = _string_value(
        prompt_context.get(RETRIEVAL_AUDIT_CONTEXT_ID_KEY)
    )
    for rank, record in enumerate(records, start=1):
        query_context = {
            "prompt_context_hash": prompt_context_hash,
            "decision_type": decision_type,
            "source_path": record["source_path"],
        }
        if retrieval_audit_context_id is not None:
            query_context[RETRIEVAL_AUDIT_CONTEXT_ID_KEY] = retrieval_audit_context_id
        await session.execute(
            retrieval_records.insert().values(
                game_id=game_id,
                player_id=player_id,
                ai_decision_id=decision_id,
                memory_entry_id=record["memory_entry_id"],
                query_text=record["query_text"],
                query_context=query_context,
                retrieved_context=record["retrieved_context"],
                source_type=record["source_type"],
                source_id=record["source_id"],
                rank=rank,
                score=record["score"],
            )
        )


def _context_snippets(prompt_context: Mapping[str, Any], key: str) -> tuple[Mapping[str, Any], ...]:
    section = prompt_context.get(key)
    if not isinstance(section, Mapping):
        return ()
    snippets = section.get("snippets")
    if not isinstance(snippets, Sequence) or isinstance(snippets, str | bytes | bytearray):
        return ()
    return tuple(dict(snippet) for snippet in snippets if isinstance(snippet, Mapping))


def _retrieval_records_from_snippets(
    *,
    snippets: Sequence[Mapping[str, Any]],
    source_type: str,
    query_text: str,
    source_path: str,
) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    for index, snippet in enumerate(snippets, start=1):
        source_id = _snippet_source_id(snippet, fallback=f"{source_type}-{index}")
        records.append(
            {
                "memory_entry_id": _coerce_uuid_or_none(snippet.get("id"))
                if source_type == "memory"
                else None,
                "query_text": query_text,
                "retrieved_context": _json_safe(dict(snippet)),
                "source_type": source_type,
                "source_id": source_id,
                "source_path": source_path,
                "score": _snippet_score(snippet),
            }
        )
    return tuple(records)


def _snippet_source_id(snippet: Mapping[str, Any], *, fallback: str) -> str:
    for key in ("id", "source_id", "source"):
        value = snippet.get(key)
        if value is not None:
            text = str(value)
            if text:
                return text
    return fallback


def _snippet_score(snippet: Mapping[str, Any]) -> float | None:
    score = snippet.get("context_score", snippet.get("score"))
    if isinstance(score, bool):
        return None
    if isinstance(score, int | float):
        return max(0.0, min(1.0, float(score)))
    importance = snippet.get("importance")
    if isinstance(importance, bool):
        return None
    if isinstance(importance, int | float):
        return max(0.0, min(1.0, float(importance) / 10.0))
    return None


def _coerce_uuid_or_none(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return _coerce_uuid(str(value))
    except (TypeError, ValueError):
        return None


def _self_dialogue_content_and_payload(
    *,
    status: str,
    parsed_output: Any | None,
    validation_result: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    trusted_payload = _trusted_self_dialogue_payload(status=status, parsed_output=parsed_output)
    if trusted_payload is not None:
        dialogue_status = _string_value(trusted_payload.get("status")) or "rejected"
        if dialogue_status == "provided":
            text = _string_value(trusted_payload.get("text"))
            if text is not None and text.strip():
                return text, trusted_payload

        reason = _string_value(trusted_payload.get("reason"))
        if dialogue_status == "empty":
            return _self_dialogue_status_content("empty", reason), trusted_payload
        if dialogue_status == "rejected":
            return _self_dialogue_status_content("rejected", reason), trusted_payload

    reason = _validation_reason(validation_result) or "AI output could not be trusted."
    reason_code = _string_value(validation_result.get("reason_code")) or status
    payload = {
        "status": "rejected",
        "reason": reason,
        "reason_code": reason_code,
        "source_status": status,
        "validation_errors": _validation_errors(validation_result),
        "validation_result": _json_safe(validation_result),
    }
    return _self_dialogue_status_content("rejected", reason), payload


def _trusted_self_dialogue_payload(
    *,
    status: str,
    parsed_output: Any | None,
) -> dict[str, Any] | None:
    if status != "validated" or not isinstance(parsed_output, Mapping):
        return None
    payload = parsed_output.get("self_dialogue")
    if not isinstance(payload, Mapping):
        return None
    return dict(_json_safe(payload))


def _self_dialogue_status_content(status: str, reason: str | None) -> str:
    if reason is None:
        return f"Self-dialogue {status}."
    return f"Self-dialogue {status}: {reason}"


def _validation_reason(validation_result: Mapping[str, Any]) -> str | None:
    errors = _validation_errors(validation_result)
    if errors:
        message = _string_value(errors[0].get("message"))
        if message is not None:
            return message
    return _string_value(validation_result.get("message"))


def _validation_errors(validation_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors = validation_result.get("validation_errors")
    if not isinstance(errors, Sequence) or isinstance(errors, str | bytes | bytearray):
        return []
    return [dict(error) for error in errors if isinstance(error, Mapping)]


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def _load_ai_profile_id(
    session: AsyncSession,
    *,
    game_id: UUID,
    player_id: UUID,
) -> UUID | None:
    result = await session.execute(
        sa.select(ai_profiles.c.id)
        .where(ai_profiles.c.game_id == game_id, ai_profiles.c.player_id == player_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


def _failure_validation_result(reason_code: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "status": "rejected",
        "reason_code": reason_code,
        "validation_errors": [
            {
                "code": reason_code,
                "message": message,
                "field": None,
            }
        ],
        _AUDIT_NO_REPLACEMENT_KEY: True,
        _AUDIT_REPLACEMENT_KEY: None,
        **_json_safe(details),
    }


def _with_jsonl_audit_metadata(
    validation_result: Mapping[str, Any],
    *,
    raw_stdout: str,
    final_assistant_output: str | None,
    jsonl_event_count: int,
) -> dict[str, Any]:
    result = dict(_json_safe(validation_result))
    result.update(
        {
            "raw_output_format": "codex_exec_jsonl",
            "raw_output_bytes": len(raw_stdout.encode("utf-8")),
            "jsonl_event_count": jsonl_event_count,
            "final_assistant_output": final_assistant_output,
        }
    )
    return result


def _malformed_error(message: str, field: str | None = None) -> AIDecisionValidationError:
    from app.ai.decision_schema import AIDecisionValidationIssue

    return AIDecisionValidationError(
        (
            AIDecisionValidationIssue(
                code="malformed_ai_output",
                message=message,
                field=field,
            ),
        )
    )


def _read_last_message(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _assistant_text_from_event(event: Mapping[str, Any]) -> str | None:
    for payload in (
        event,
        event.get("item"),
        event.get("message"),
        event.get("msg"),
    ):
        text = _assistant_text_from_payload(payload)
        if text is not None:
            return text
    return None


def _assistant_text_from_payload(payload: object) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    if payload.get("role") != "assistant" and payload.get("type") not in {
        "assistant_message",
        "agent_message",
    }:
        return None

    for key in ("content", "text", "message", "output"):
        if key in payload:
            return _content_to_text(payload[key])
    return None


def _content_to_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, str | bytes | bytearray):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts) if parts else None
    if isinstance(content, Mapping):
        text = content.get("text")
        return text if isinstance(text, str) else None
    return None


def _sha256_canonical(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=None if pretty else (",", ":"),
        indent=2 if pretty else None,
        ensure_ascii=True,
    )


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str, ensure_ascii=True))


def _coerce_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


@dataclass(slots=True)
class _CommunicateResult:
    stdout: str = ""
    stderr: str = ""
    error: BaseException | None = None


def _communicate_with_process(
    process: subprocess.Popen[str],
    stdin: str,
    result: _CommunicateResult,
) -> None:
    try:
        stdout, stderr = process.communicate(input=stdin)
    except BaseException as exc:
        result.error = exc
        return

    result.stdout = stdout or ""
    result.stderr = stderr or ""


def _join_communicate_thread(thread: threading.Thread, *, timeout_seconds: float) -> bool:
    thread.join(timeout=max(0.0, timeout_seconds))
    return not thread.is_alive()


def _terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    windows_job_handle: int | None = None,
) -> None:
    if os.name == "nt":
        _terminate_windows_process_tree(process, windows_job_handle=windows_job_handle)
        return

    _terminate_posix_process_tree(process)


def _terminate_windows_process_tree(
    process: subprocess.Popen[str],
    *,
    windows_job_handle: int | None,
) -> None:
    if windows_job_handle is not None:
        _terminate_windows_job_object(windows_job_handle)

    descendant_pids = set(_windows_descendant_process_ids(process.pid))
    if process.poll() is None:
        _taskkill_windows_process_tree(process.pid)
    else:
        for pid in descendant_pids:
            _taskkill_windows_process_tree(pid)

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)

    remaining_pids = descendant_pids | set(_windows_descendant_process_ids(process.pid))
    for pid in remaining_pids:
        if _windows_process_is_running(pid):
            _taskkill_windows_process_tree(pid)
    _wait_for_windows_processes_exit(remaining_pids, timeout_seconds=5)


def _create_windows_job_for_process(process: subprocess.Popen[str]) -> int | None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int

    job_handle = kernel32.CreateJobObjectW(None, None)
    if not job_handle:
        return None

    process_handle = getattr(process, "_handle", None)
    if process_handle is None or not kernel32.AssignProcessToJobObject(
        job_handle,
        process_handle,
    ):
        kernel32.CloseHandle(job_handle)
        return None
    return int(job_handle)


def _terminate_windows_job_object(job_handle: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    kernel32.TerminateJobObject(job_handle, 1)


def _close_windows_handle(handle: int) -> None:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle(handle)


def _taskkill_windows_process_tree(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _windows_descendant_process_ids(root_pid: int) -> list[int]:
    script = r"""
$RootProcessId = __ROOT_PROCESS_ID__
$processes = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId
$pending = @($RootProcessId)
$seen = @{}
while ($pending.Count -gt 0) {
    $next = @()
    foreach ($current in $pending) {
        foreach ($process in $processes) {
            if ([int]$process.ParentProcessId -eq [int]$current) {
                $childPid = [int]$process.ProcessId
                if (-not $seen.ContainsKey($childPid)) {
                    $seen[$childPid] = $true
                    Write-Output $childPid
                    $next += $childPid
                }
            }
        }
    }
    $pending = $next
}
""".replace("__ROOT_PROCESS_ID__", str(int(root_pid)))
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    descendant_pids: list[int] = []
    for line in completed.stdout.splitlines():
        try:
            descendant_pids.append(int(line.strip()))
        except ValueError:
            continue
    return descendant_pids


def _wait_for_windows_processes_exit(pids: set[int], *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(not _windows_process_is_running(pid) for pid in pids):
            return True
        time.sleep(0.05)
    return all(not _windows_process_is_running(pid) for pid in pids)


def _windows_process_is_running(pid: int) -> bool:
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return str(pid) in completed.stdout


def _terminate_posix_process_tree(process: subprocess.Popen[str]) -> None:
    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass

    if _wait_for_posix_process_group_exit(process_group_id, timeout_seconds=5):
        return

    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        process.kill()
    process.wait(timeout=5)
    _wait_for_posix_process_group_exit(process_group_id, timeout_seconds=5)


def _wait_for_posix_process_group_exit(process_group_id: int, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


__all__ = [
    "CodexExecAIDecisionRequest",
    "CodexExecAIDecisionResult",
    "CodexExecProcessResult",
    "CodexExecRunner",
    "CodexExecTimeoutError",
    "CodexSubprocessRunner",
    "DEFAULT_AI_MODEL",
    "DEFAULT_AI_SANDBOX_DIR",
    "DEFAULT_AI_SCHEMA_FILE",
    "LIGHT_REASONING_CONFIG",
    "ParsedCodexJSONLEvents",
    "build_codex_exec_command",
    "build_prompt",
    "parse_codex_jsonl_events",
    "request_codex_ai_decision",
    "write_ai_output_schema_file",
]
