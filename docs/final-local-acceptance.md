# Final Local Acceptance

Phase 11 Stage 11.6 verifies the local product from a clean Docker Compose boot through browser-visible play.

## Commands

- `pnpm run review`
- `RUN_LIVE_CODEX_AI=1 pnpm run test:smoke:live`
- `uv run --no-sync python scripts/final_local_acceptance.py`
- `docker compose up --build`
- `http://localhost:3000`

## Evidence Scope

- Clean local boot uses `docker compose up --build` and the acceptance runner always calls `docker compose down` for containers it starts.
- Browser verification opens `http://localhost:3000` and creates games through the UI.
- The final browser spec covers `2-5 players` with human and AI controller mixes.
- Human players complete normal turns with visible `Roll dice`, optional purchase/payment resolution, and `End turn`.
- AI players are advanced from the browser through `Step AI`; the backend uses real codex exec --json subprocesses for the live acceptance path.
- `complex negotiations` are exercised with structured instruments, exact deal acceptance, and explicit expiration by deterministic cutoff.
- `contracts and obligations` are created from an accepted structured deal and enforced through the backend, then verified in the browser.
- AI memory and self-dialogue are inspectable in the `AI audit` panel.
- `illegal actions` are submitted as stale actions, rejected by the backend, audited, and shown in the browser as `Rejected action`.
- No fallback actions are accepted in the final path; AI failures remain rejected or blocked rather than substituted.

## Test Results

Passing local evidence on 2026-07-06:

- `pnpm run review`: passed after starting the local compose Postgres service for API integration tests. Result included the web unit suite, Playwright e2e suite with the expected skip, API test suite, and product smoke.
- `RUN_LIVE_CODEX_AI=1 pnpm run test:smoke:live`: passed with `live Codex AI smoke ok: action_decision`.
- `uv run --no-sync python scripts/final_local_acceptance.py`: passed from `docker compose up --build`, waited for `http://localhost:3000` and API health, ran `final-local-acceptance.spec.ts`, and cleaned up compose containers.

## Residual Risks

The live Codex subprocess path depends on local Codex authentication being available to both the host smoke test and the Docker-mounted API container. Host Codex plugins are disabled for the game AI subprocess so unrelated MCP authentication cannot block a real `codex exec --json` decision.
