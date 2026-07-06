# Local Operator Runbook

This runbook explains how to install, run, test, inspect, and troubleshoot the finished local Monopoly-style AI research game.

## Architecture overview

The app is local-only and uses three tiers:

- `apps/web`: Next.js App Router frontend. Docker exposes it at `http://localhost:3000`.
- `services/api`: FastAPI backend. Docker exposes it at `http://localhost:8000`.
- `postgres`: Postgres with pgvector using `pgvector/pgvector:pg17`. Docker exposes it at `localhost:5432` by default.

The backend is the only rules authority. The frontend displays state, available actions, negotiations, contracts, obligations, AI audit records, and error/rejection details, but it does not decide whether an action is legal. Every submitted human, AI, negotiation, MCP, contract, and settlement mutation is checked by the FastAPI backend and deterministic rules engine before any event or audit record is committed.

Important backend areas:

- `services/api/app/rules`: deterministic game state, legal actions, mechanics, timing, debt, RNG, and reducers.
- `services/api/app/api/games.py`: game API routes, action submission, negotiation endpoints, AI step endpoints, contract views, and audit views.
- `services/api/app/contracts`: contract creation, settlement, default handling, triggers, and outcome explanations.
- `services/api/app/ai`: Codex AI subprocess orchestration, schema validation, context packing, profiles, enforcement, and AI memory.
- `services/api/app/rag`: local retrieval corpus, lexical search, deterministic local embeddings, and RAG query/audit helpers.
- `services/api/app/mcp`: local stdio MCP server and local tool definitions.
- `services/api/app/db/metadata.py`: SQLAlchemy table definitions for game, audit, contract, AI, memory, retrieval, and RAG records.

Important persistent tables for inspection:

- `ai_decisions`: every Codex decision attempt, prompt hash/context, raw output, parsed output, validation result, status, and accepted/rejected links.
- `ai_self_dialogue`: self-dialogue entries emitted by schema-valid AI output and linked to decisions.
- `ai_memory_entries`: trusted AI memory updates, compacted summaries, visibility, importance, and source links.
- `retrieval_records`: RAG/context snippets retrieved for prompts or MCP searches, with query context and decision links.
- `contracts`: accepted durable agreements created from eligible structured deals.
- `obligations`: scheduled, pending, settled, or defaulted obligations produced by contracts and settlement rules.

## Install

Run these commands from the repository root in Windows PowerShell:

```powershell
pnpm install
uv python install 3.14.6
uv sync --python 3.14.6
```

Optional but useful checks:

```powershell
codex exec --help
codex exec --json -c 'model_reasoning_effort="xhigh"' --help
docker compose version
```

If live AI turns will run inside Docker, make sure Docker Compose can mount the host Codex auth directory. From this workspace the example file points at `C:/Users/adams/.codex`; on another Windows account, override it for the current shell:

```powershell
$env:CODEX_HOST_HOME = "$env:USERPROFILE\.codex"
```

You can also copy `.env.example` to `.env` and edit `CODEX_HOST_HOME`.

## Run

Boot all three tiers:

```powershell
$env:CODEX_HOST_HOME = "$env:USERPROFILE\.codex"
docker compose up --build
```

Open the frontend:

```powershell
Start-Process http://localhost:3000
```

Check the API:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Shutdown without deleting database state:

```powershell
docker compose down
```

Shutdown and delete the local Postgres volume:

```powershell
docker compose down -v
```

### Run tiers without Docker

The product target is Docker Compose, but these workspace scripts are useful while inspecting code:

```powershell
pnpm --filter @monopoly-ai-game/api run migrate
pnpm --filter @monopoly-ai-game/api run dev
```

In another PowerShell session:

```powershell
pnpm --filter @monopoly-ai-game/web run dev
```

The root convenience command starts the web and API package dev scripts in parallel:

```powershell
pnpm run dev
```

## Smoke, test, and review

Run the product smoke gate:

```powershell
pnpm run test:smoke
```

Run the full test suite:

```powershell
pnpm run test
```

Run lint, typecheck, and tests:

```powershell
pnpm run review
```

Run the live Codex AI smoke. This launches a real `codex exec --json` process and validates the output against the AI schema. The script intentionally skips unless `RUN_LIVE_CODEX_AI=1` is present.

```powershell
$env:RUN_LIVE_CODEX_AI = "1"
pnpm run test:smoke:live
Remove-Item Env:\RUN_LIVE_CODEX_AI
```

## AI runtime

AI players are driven by the backend through real Codex CLI subprocesses. The command is built in `services/api/app/ai/orchestrator.py` with:

- `codex exec --json`
- `--ephemeral`
- `-a never`
- `-c 'model_reasoning_effort="xhigh"'`
- `--output-schema services/api/app/ai/schemas/agent_decision.schema.json`
- `-C services/api/app/ai/sandbox`
- stdin prompt input

The backend writes or refreshes the output schema before execution, sends a prompt context containing game state, legal actions, memory, retrieval snippets, and decision instructions, then parses Codex JSON event output for the final assistant message.

The parsed message must validate against the schema in `services/api/app/ai/schemas/agent_decision.schema.json`. Validation happens before mutation. The backend then applies AI enforcement to the selected action or decision result. Legal actions may become normal accepted events. Invalid actions, malformed JSON, schema errors, process errors, timeouts, and impossible mandatory AI actions are rejected and audited. There is no fallback, random, default, coerced, or substitute move path.

If a mandatory AI turn cannot produce a valid action, the game can enter `AI_BLOCKED`. Once blocked, mutating actions and AI step requests are rejected until the state is repaired through the intended backend paths.

AI audit and AI memory live in Postgres:

- `ai_decisions`: reconstructs the full attempt from prompt context, hashes, raw output, parsed output, validation result, status, and linked accepted/rejected rows.
- `ai_self_dialogue`: stores schema-valid self-dialogue linked to one decision.
- `ai_memory_entries`: stores trusted memory updates only after a decision reaches a trusted final status, plus compaction summaries and source links.
- `retrieval_records`: stores context snippets that were retrieved for prompts or local tooling.

The frontend AI audit panel reads the backend audit endpoints and shows profiles, decisions, validation errors, self-dialogue, memory, retrieval records, and rejected AI output.

## Negotiation

Negotiations are server-owned flows for structured deals and messages. The backend validates:

- game and participant identities,
- turn and phase boundaries,
- deal payload shape,
- participant approvals,
- player references,
- legal transfer terms,
- AI restrictions,
- idempotency and stale state expectations.

Frontend negotiation controls submit API requests; they do not directly alter state. Accepted messages, deal changes, and deal outcomes are persisted through backend endpoints and are available to audit, RAG, and MCP paths where appropriate.

## Contracts

Contracts are durable server records created from accepted structured deals that are eligible for contract execution. `contracts` records describe the agreement, parties, terms, status, source deal, and effective event. `obligations` records describe scheduled or pending duties created by those contracts.

The contract engine supports complex instruments such as:

- future payments,
- installment-like payment duties,
- rent-share or percent-based promises,
- property transfer terms,
- debt and forgiveness terms,
- conditional or trigger-linked settlement work,
- default detection and default outcome handling.

The backend owns contract execution and obligation settlement. When an obligation is due, settled, failed, or defaulted, the outcome is persisted and explainable through API and UI records. The frontend contracts panel reads `contracts`, `obligations`, and outcome explanations; it does not keep a separate source of truth.

## RAG and MCP

RAG is local retrieval over repository and game-derived sources. Static sources come from `content/rules`, including classic rules, house rules and deviations, and contract examples. Game-specific sources can include AI memory, negotiation history, and past AI decisions.

Retrieval uses Postgres full-text search and pgvector-backed deterministic local embeddings. The durable index table is `rag_index_entries`; query/audit evidence is stored in `retrieval_records`.

Refresh the database-backed RAG index from the repository root:

```powershell
uv run --project services/api --python 3.14.6 python services/api/scripts/refresh_rag_index.py
```

Build reproducible JSONL corpus artifacts without populating the database:

```powershell
uv run --project services/api --python 3.14.6 python services/api/scripts/build_rag_index.py --output .\tmp\rag-corpus.jsonl
```

Local MCP is stdio-only. It does not create a network listener, remote endpoint, websocket, hosted service, or multiplayer channel.

Run the MCP server smoke:

```powershell
uv run --project services/api --python 3.14.6 python services/api/scripts/local_mcp_server.py --smoke
```

Run the MCP server normally:

```powershell
uv run --project services/api --python 3.14.6 python services/api/scripts/local_mcp_server.py
```

Registered tools:

- `get_game_state`: read current replayed game state.
- `get_legal_actions`: read backend-generated legal actions for one actor.
- `search_rules`: search indexed local rules, house rules, and contract examples.
- `search_memory`: search visible game-scoped memory and history.
- `inspect_contract`: read one persisted contract and its obligations.
- `validate_deal_draft`: validate a draft deal without creating rows.
- `submit_action`: submit one action through FastAPI with an idempotency key.

Only `submit_action` mutates state. It calls the local FastAPI action path and is still subject to the backend rules engine, validation, idempotency checks, AI restrictions, and audit persistence.

## Inspect local records

Use Docker Compose to open `psql` inside the Postgres container:

```powershell
docker compose exec postgres psql -U monopoly -d monopoly_ai_game
```

Useful read-only SQL examples:

```sql
select id, status, decision_type, created_at from ai_decisions order by created_at desc limit 10;
select ai_decision_id, role, content from ai_self_dialogue order by created_at desc limit 10;
select id, player_id, category, visibility, importance, created_at from ai_memory_entries order by created_at desc limit 10;
select id, ai_decision_id, source_type, source_id, score, created_at from retrieval_records order by created_at desc limit 10;
select id, status, created_at from contracts order by created_at desc limit 10;
select id, contract_id, status, owed_by_player_id, owed_to_player_id, due_phase from obligations order by created_at desc limit 10;
```

If the table is empty, create or continue a game with AI players, submit negotiation/contract actions, or run retrieval/MCP flows first.

## Test command reference

Root commands:

| Command | Purpose |
| --- | --- |
| `pnpm run generate:api` | Generate OpenAPI JSON and TypeScript types. |
| `pnpm run test` | Run contract, unit, integration, e2e, and smoke gates. |
| `pnpm run test:contract` | Run shared schema contract tests. |
| `pnpm run test:unit` | Run phase/scaffold checks plus web and API tests. |
| `pnpm run test:integration` | Run integration gate scripts. |
| `pnpm run test:e2e` | Run e2e gate scripts. |
| `pnpm run test:smoke` | Run smoke gates, including product smoke. |
| `pnpm run test:smoke:live` | Run gated live Codex AI smoke when `RUN_LIVE_CODEX_AI=1`. |
| `pnpm run test:web` | Run web unit and Playwright tests. |
| `pnpm run test:api` | Run API pytest suite. |
| `pnpm run lint` | Run backend/web/schema lint checks. |
| `pnpm run typecheck` | Run backend/web/schema type checks. |
| `pnpm run review` | Run lint, typecheck, and full tests. |

Focused package commands:

```powershell
pnpm --filter @monopoly-ai-game/api run test
pnpm --filter @monopoly-ai-game/api run lint
pnpm --filter @monopoly-ai-game/api run typecheck
pnpm --filter @monopoly-ai-game/web run test:unit
pnpm --filter @monopoly-ai-game/web run test:e2e
pnpm --filter @monopoly-ai-game/schemas run test
```

Regression and simulation helpers:

```powershell
pnpm --filter @monopoly-ai-game/api run simulate
uv run --project services/api --python 3.14.6 pytest services/api/tests/regression
uv run --project services/api --python 3.14.6 python services/api/scripts/verify_game_api.py
uv run --project services/api --python 3.14.6 python services/api/scripts/verify_concurrency.py
uv run --project services/api --python 3.14.6 python services/api/scripts/verify_rejected_actions.py
uv run --project services/api --python 3.14.6 python services/api/scripts/verify_snapshots.py
```

## Troubleshooting

### Docker cannot mount Codex auth

Set `CODEX_HOST_HOME` before booting:

```powershell
$env:CODEX_HOST_HOME = "$env:USERPROFILE\.codex"
docker compose up --build
```

If the path does not exist, run Codex locally and authenticate first. Live AI turns need the mounted Codex auth directory because the API container executes `codex`.

### Port is already in use

Stop the conflicting process or override ports:

```powershell
$env:WEB_PORT = "3001"
$env:API_PORT = "8001"
$env:POSTGRES_PORT = "5433"
docker compose up --build
```

When changing ports, keep `NEXT_PUBLIC_API_BASE_URL` and CORS settings aligned if the browser-facing API port changes.

### Database state is stale or migrations look inconsistent

For a clean local database:

```powershell
docker compose down -v
docker compose up --build
```

For a non-Docker API run, apply migrations:

```powershell
pnpm --filter @monopoly-ai-game/api run migrate
```

### Live Codex AI smoke skips

Set the gate variable in the same shell:

```powershell
$env:RUN_LIVE_CODEX_AI = "1"
pnpm run test:smoke:live
```

### Live Codex AI fails schema or process validation

Check:

- `codex exec --json -c 'model_reasoning_effort="xhigh"' --help` succeeds locally.
- `CODEX_HOST_HOME` points at the authenticated host `.codex` directory for Docker.
- The API container can find `CODEX_AI_EXECUTABLE=codex`.
- The decision is reconstructable in `ai_decisions` with validation errors and raw output.

AI failures should produce audit evidence. They should not produce fallback moves.

### RAG search returns no results

Refresh the database-backed index after migrations:

```powershell
uv run --project services/api --python 3.14.6 python services/api/scripts/refresh_rag_index.py
```

Game-specific memory and history only appear after a game has produced AI memory, negotiations, decisions, or retrieval records.

### MCP tool mutation is rejected

`submit_action` goes through the same backend action endpoint as the product UI. Inspect the rejected action or AI audit records, then retry with a fresh legal action, current `expected_state_hash`, and current `expected_event_sequence`.

### Tests fail after schema changes

Regenerate OpenAPI artifacts first:

```powershell
pnpm run generate:api
pnpm run test:contract
```

Then rerun the focused failing test or `pnpm run review`.
