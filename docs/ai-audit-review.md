# AI and Audit Review - Phase 11 Stage 11.2 (2026-07-05)

## Summary

Stage 11.2 confirms that AI runtime and audit paths are deterministic, enforceable, and reconstructable.

## Verification findings

- The Codex subprocess command path uses `codex exec --json` with `--model gpt-5.4-mini`, `model_reasoning_effort="light"`, and writes the `--output-schema` artifact, so AI decisions are schema-checked before mutation.
- Schema validation is enforced for every Codex attempt, and malformed/non-JSON outputs are rejected.
- invalid output rejection is recorded as a rejected AI output; mandatory invalid output results in `AI_BLOCKED` game status and `no_substitute` behavior.
- no fallback exists for any AI output handling path (`fallback`, `substitute_move`, and `default` substitutions were searched for and remain absent from AI runtime implementation).
- Memory and self-dialogue are persisted in the audit lineage for accepted/validated outputs and remain queryable across multiple AI decisions.
- AI audit UI endpoints expose all required artifacts: profiles, decisions, self-dialogue, memory, retrieval records, and rejected outputs.
- decisions are reconstructable from audit records by comparing decision `status`, `raw_output`, `prompt_context_hash`, `prompt_context`, and linked `memory_entry_ids`/`retrieval_record_ids`.

## Test coverage added

- `services/api/tests/test_stage_11_2_ai_audit_review.py`
  - verifies `codex exec --json` command usage and `gpt-5.4-mini` light reasoning config in command construction.
  - verifies schema validation and rejected-output behavior.
  - verifies AI_BLOCKED and non-fallback rejection metadata (`no_substitute_move`, `substitute_move`).
  - verifies AI audit API exposure for profiles, decisions, memory, self-dialogue, retrievals, and rejected outputs.
  - verifies decisions are reconstructable from persisted audit rows.
