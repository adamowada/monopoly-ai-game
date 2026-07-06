# monopoly-ai-game

Local-only Monopoly-style AI research game with human hotseat play, real Codex AI players, auditable memory, negotiations, contracts, local RAG, and local MCP tooling.

The detailed operator runbook is [docs/runbook.md](docs/runbook.md).

## Project Control

`AGENTS.md` and `PLANS.md` define the development workflow. The project-control branch marker remains `feature/phase-0-project-control`, and current supervised work uses `codex-supervisor` state under `.codex-supervisor/`, which is local and not committed.

## Quick Start

Prerequisites:

- Docker Desktop with Docker Compose.
- Node.js `24.x` and pnpm `11.7.0`.
- uv with Python `3.14.6`.
- Codex CLI available as `codex` for live AI turns.

Install dependencies from Windows PowerShell:

```powershell
pnpm install
uv python install 3.14.6
uv sync --python 3.14.6
```

Start the local product stack:

```powershell
$env:CODEX_HOST_HOME = "$env:USERPROFILE\.codex"
docker compose up --build
```

Open the app:

```powershell
Start-Process http://localhost:3000
```

The API is available at `http://localhost:8000`, with health at `http://localhost:8000/health`.

Shut down the stack:

```powershell
docker compose down
```

To also remove the local Postgres volume:

```powershell
docker compose down -v
```

## Test And Review

Run the full test suite:

```powershell
pnpm run test
```

Run lint, typecheck, and tests:

```powershell
pnpm run review
```

Run the product smoke test:

```powershell
pnpm run test:smoke
```

The repository still keeps the phase 1 stage 1.1 scaffold gates as compatibility checks:

```powershell
pnpm run test:scaffold
pnpm run test:web
pnpm run test:api
pnpm --filter @monopoly-ai-game/web run dev
pnpm --filter @monopoly-ai-game/api run dev
```

Run the live Codex AI smoke. This launches real `codex exec --json` with schema validation and xhigh reasoning; the smoke is skipped unless `RUN_LIVE_CODEX_AI=1` is set.

```powershell
$env:RUN_LIVE_CODEX_AI = "1"
pnpm run test:smoke:live
Remove-Item Env:\RUN_LIVE_CODEX_AI
```

More test commands are listed in the [runbook test command reference](docs/runbook.md#test-command-reference).

## Architecture

The product is a local 3-tier app:

- `apps/web`: Next.js App Router frontend on `http://localhost:3000`.
- `services/api`: FastAPI backend on `http://localhost:8000`.
- `postgres`: Postgres with pgvector from `pgvector/pgvector:pg17`.

The frontend does not decide game legality. It renders backend state and backend-generated legal actions, then submits selected actions to FastAPI. All legal and illegal action decisions are enforced by the backend deterministic rules engine before events, rejections, contracts, obligations, AI audit rows, or state snapshots are persisted.

## AI Runtime

AI players use real Codex CLI subprocesses through `codex exec --json`. The backend builds the command with `--ephemeral`, `--output-schema services/api/app/ai/schemas/agent_decision.schema.json`, `-c 'model_reasoning_effort="xhigh"'`, `-a never`, and an isolated AI sandbox directory. Outputs are parsed from Codex JSON events, validated against the decision schema, and then audited.

There are no fallback moves. Malformed output, invalid schema output, invalid actions, process errors, and timeouts are rejected or can place the game in `AI_BLOCKED` when a mandatory AI action cannot proceed.

AI memory and AI audit records live in Postgres. Important tables include:

- `ai_decisions`: Codex attempts, prompt context, raw output, parsed output, validation results, status, and accepted/rejected links.
- `ai_self_dialogue`: schema-valid AI self-dialogue attached to decisions.
- `ai_memory_entries`: trusted memory updates, compacted summaries, and source links.
- `retrieval_records`: RAG and context retrieval audit rows linked to decisions where applicable.

The app exposes an AI audit panel, and the API exposes audit endpoints under game-specific routes.

## Negotiations And Contracts

Negotiations are server-owned deal flows. Players can create structured deals and exchange negotiation messages; backend validation checks participants, terms, payload shape, timing, and action legality.

Accepted eligible deals can create durable `contracts` and scheduled or pending `obligations`. Complex instruments include future payments, rent-share style promises, debt terms, property transfers, and other structured obligations implemented by the backend contracts and settlement engine. Obligations are settled through backend rules paths and produce auditable outcomes rather than frontend-side bookkeeping.

## RAG And MCP

Local RAG indexes rules, house rules, contract examples, visible AI memory, negotiation history, and past AI decisions. Retrieval combines Postgres full-text search with pgvector-backed deterministic local embeddings. Retrieval evidence is persisted in `retrieval_records`.

Local MCP is stdio-only and implemented in `services/api/app/mcp`. It exposes tools such as `get_game_state`, `get_legal_actions`, `search_rules`, `search_memory`, `inspect_contract`, `validate_deal_draft`, and `submit_action`. Only `submit_action` mutates state, and it still goes through the FastAPI action endpoint and backend rules validation.

## Troubleshooting

- Port conflict on `3000`, `8000`, or `5432`: stop the conflicting process or override `WEB_PORT`, `API_PORT`, or `POSTGRES_PORT` in `.env`.
- Database state looks stale: run `docker compose down -v`, then `docker compose up --build`.
- Live AI fails in Docker: confirm `CODEX_HOST_HOME` in `.env.example` points at a host Codex auth directory and `CODEX_AI_EXECUTABLE=codex`.
- Live AI smoke skips: set `RUN_LIVE_CODEX_AI=1` in the same PowerShell session before running `pnpm run test:smoke:live`.
- RAG/MCP search has no game-specific memory: run migrations, create/play a game, then refresh the RAG index as described in [docs/runbook.md](docs/runbook.md#rag-and-mcp).

## Useful Commands

```powershell
pnpm install
uv sync --python 3.14.6
docker compose up --build
pnpm run test
pnpm run review
pnpm run test:smoke
$env:RUN_LIVE_CODEX_AI = "1"; pnpm run test:smoke:live
docker compose down
```
