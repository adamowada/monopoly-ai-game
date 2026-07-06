# Performance And Reliability Hardening

Phase 11 Stage 11.4 focused on making long local games stay responsive, replayable, and safe when AI subprocesses fail.

## Query/index review

Reviewed the long-game read and audit paths in `services/api/app/db/metadata.py`.

- `game_events`: `(game_id, sequence)` supports ordered replay; `(game_id, created_at)` supports game log reads; `actor_player_id` supports actor filtering.
- `game_snapshots`: `(game_id, event_sequence)` supports latest-snapshot lookup and bounded replay; `last_event_id` supports snapshot/event traceability.
- `rejected_actions`: `(game_id, actor_player_id, created_at)` and `(game_id, phase)` support audit filtering.
- `ai_decisions`: `(game_id, player_id, created_at)`, `prompt_context_hash`, and outcome-link indexes support AI audit history and reconstruction.
- `ai_memory_entries` and `retrieval_records`: game/player/time and decision-link indexes support AI memory and RAG audit queries.

The Stage 11.4 backend test asserts these expected indexes remain present.

## Long-game simulation

Measured check: `run_random_legal_action_stress(seed="stage-11.4-doc-speed", player_count=5, action_limit=750)`.

- Result: 750 actions, no invariant failure.
- Measured wall time: 15.399 seconds.
- Threshold in test: less than 30 seconds for 750 actions.

This keeps deterministic simulation fast enough for local hardening while still covering repeated rolls, payments, purchases, turn changes, and invariant checks.

## Memory growth test

Measured check: `tracemalloc` around a 250-action, 5-player random legal-action simulation.

- Result: 250 actions, no invariant failure.
- Current traced memory after run: 0.295 MB.
- Peak traced memory: 0.453 MB.
- Thresholds in test: current less than 16 MB, peak less than 48 MB, and peak per action less than 96 KB.

The larger speed run is intentionally measured without `tracemalloc` because allocation tracing adds significant instrumentation overhead on this workload.

## Snapshot interval tuning

The default EventPersistence snapshot interval is tuned to 25 accepted events.

- Previous default: 2 events, which wrote many snapshots during long games.
- New default: 25 events, bounding latest-snapshot replay to fewer than 25 tail events while reducing snapshot write volume.
- Measured test case: 127 accepted events creates snapshots at event sequences 25, 50, 75, 100, and 125, leaving 2 tail events to replay.
- Snapshot verification confirms replay from event zero and replay from latest snapshot produce identical state hashes.

Stage-specific scripts that need denser snapshot evidence can still pass an explicit `snapshot_interval`.

## Frontend render performance check

The Playwright spec `apps/web/e2e/stage-11-4-render-reliability.spec.ts` creates a browser game, drives 24 real `/actions` submissions, and then verifies the board, active-player panel, property management, contracts panel, negotiation inbox, and AI audit remain visible/readable.

Measured browser signals and thresholds:

- Test runtime: 8.8 seconds in Chromium on the Stage 11.4 run.
- Average action/update loop threshold: less than 1,250 ms.
- DOM node threshold: fewer than 7,500 nodes after repeated updates.
- Long task threshold: fewer than 20 observed long tasks, with max long task less than 1,000 ms.
- Resource entry threshold: fewer than 350 resource entries.
- Chromium heap threshold when exposed: less than 140 MB.

## AI subprocess timeout and cleanup verification

`CodexSubprocessRunner` now owns an explicit `subprocess.Popen` lifecycle instead of delegating to `subprocess.run`.

- Normal completion still uses stdin, captured stdout/stderr, UTF-8 text mode, and the caller timeout.
- Windows subprocesses start with `CREATE_NEW_PROCESS_GROUP`.
- POSIX subprocesses start in a new session.
- Timeout cleanup terminates the process tree, waits for exit, and raises `CodexExecTimeoutError`.
- The Stage 11.4 backend test launches a parent process that creates a child worker and verifies the child exits after timeout cleanup.

This specifically guards against orphaned AI subprocesses after Codex timeout or failure.

## Residual risks

- Browser performance thresholds are calibrated for local Chromium/Next.js dev server runs. A very slow machine could need threshold adjustment, but the spec records multiple independent signals instead of relying on one timing value.
- The long-game simulation is deterministic stress coverage, not a full strategic end-to-end game with live Codex AI calls.
- Snapshot interval 25 balances write volume and replay tail length for local play; unusually heavy audit extensions may need another tuning pass.

