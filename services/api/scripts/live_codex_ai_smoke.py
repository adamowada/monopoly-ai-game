"""Gated live smoke for real `codex exec --json`.

Set RUN_LIVE_CODEX_AI=1 to request a real Codex AI decision. The command uses
model_reasoning_effort="xhigh" and --output-schema through the shared orchestrator builder.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.ai.decision_schema import validate_ai_decision_output  # noqa: E402
from app.ai.orchestrator import (  # noqa: E402
    DEFAULT_AI_SANDBOX_DIR,
    DEFAULT_AI_SCHEMA_FILE,
    CodexExecAIDecisionRequest,
    CodexExecTimeoutError,
    CodexSubprocessRunner,
    build_codex_exec_command,
    build_prompt,
    parse_codex_jsonl_events,
    write_ai_output_schema_file,
)


GAME_ID = UUID("00000000-0000-0000-0000-0000000073f1")
PLAYER_ID = UUID("00000000-0000-0000-0000-0000000073f2")


def main() -> int:
    if os.getenv("RUN_LIVE_CODEX_AI") != "1":
        print("live Codex AI smoke skipped; set RUN_LIVE_CODEX_AI=1 to enable")
        return 0

    schema_file = write_ai_output_schema_file(DEFAULT_AI_SCHEMA_FILE)
    DEFAULT_AI_SANDBOX_DIR.mkdir(parents=True, exist_ok=True)

    request = CodexExecAIDecisionRequest(
        game_id=GAME_ID,
        player_id=PLAYER_ID,
        decision_type="action_decision",
        phase="START_TURN",
        state_hash="live-smoke-state-hash",
        timeout_seconds=300,
        prompt_context={
            "smoke": "live codex exec schema validation",
            "required_output": {
                "decision_type": "action_decision",
                "game_id": str(GAME_ID),
                "player_id": str(PLAYER_ID),
                "expected_state_hash": "live-smoke-state-hash",
                "expected_event_sequence": 0,
                "action": {"type": "ROLL_DICE", "payload": {}},
                "self_dialogue": {
                    "status": "empty",
                    "reason": "Live smoke only; no private reasoning is needed.",
                },
                "memory_updates": [],
                "confidence": 0.5,
                "rationale": "This smoke test only proves schema-shaped Codex output.",
            },
        },
    )

    with tempfile.TemporaryDirectory(prefix="monopoly-live-codex-ai-") as temp_dir:
        output_last_message_path = Path(temp_dir) / "last-message.json"
        command = build_codex_exec_command(
            schema_file=schema_file,
            sandbox_dir=DEFAULT_AI_SANDBOX_DIR,
            output_last_message_path=output_last_message_path,
        )
        try:
            process = CodexSubprocessRunner().run(
                command,
                stdin=build_prompt(request),
                timeout_seconds=request.timeout_seconds,
                output_last_message_path=output_last_message_path,
            )
        except CodexExecTimeoutError as exc:
            print(str(exc))
            return 1

        if process.returncode != 0:
            print(f"codex exec failed with status {process.returncode}")
            if process.stderr:
                print(process.stderr)
            return process.returncode

        parsed_events = parse_codex_jsonl_events(process.stdout)
        final_output = _read_last_message(output_last_message_path) or parsed_events.final_assistant_output
        if final_output is None:
            print("codex exec did not produce a final assistant output")
            return 1

        parsed = validate_ai_decision_output(final_output)
        print(f"live Codex AI smoke ok: {parsed.root.decision_type}")
        return 0


def _read_last_message(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
