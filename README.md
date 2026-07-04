# monopoly-ai-game

Local-only Monopoly-style AI research game. `PLANS.md` is the authoritative product plan, architecture plan, phase plan, acceptance checklist, and technical decision record. `AGENTS.md` contains the binding development and git workflow instructions.

## Phase 0 Status

This branch is `feature/phase-0-project-control`. Phase 0 establishes project control, the fixed command surface, local toolchain documentation, and repository hygiene before the application tiers are scaffolded in Phase 1.

The Phase 0 `pnpm` commands are scaffold verifiers. They prove the repository baseline is intact and will be expanded into real frontend, backend, end-to-end, smoke, and regression suites as later phases add product code.

## Local-Only Architecture

The finished product is a local 3-tier application:

- Frontend: Next.js App Router TypeScript app at `apps/web`, served locally at `http://localhost:3000`.
- Backend: FastAPI Python service at `services/api`, responsible for all legal action validation, event sourcing, AI orchestration, RAG, MCP, negotiations, contracts, persistence, and audit records.
- Database: Postgres using `pgvector/pgvector:pg17` for game state, immutable events, snapshots, AI decisions, memory, retrieval records, contracts, obligations, and test fixtures.

The frontend never decides legality. It displays backend state and legal actions, then submits selected actions to the FastAPI service. The backend deterministic rules engine is the only authority for accepted or rejected game mutations.

There is no deployed mode, public mode, online multiplayer, or controller support. Human play is hotseat only on this local machine. The final local boot target is:

```powershell
docker compose up --build
```

## Fixed Toolchain

`PLANS.md` fixes these versions and decisions:

| Tool | Fixed version or decision |
| --- | --- |
| Node.js | `v24.11.0` |
| npm | `9.8.1` |
| pnpm | `11.7.0` |
| Global Python | `3.12.10` |
| Project Python | `3.14.6` |
| uv | `0.11.7` |
| Docker | `29.5.3` |
| Docker Compose | `v5.1.4` |
| Git | `2.51.2.windows.1` |
| Codex CLI | `0.133.0` |

The repository pins the JavaScript package manager with `packageManager: pnpm@11.7.0`, configures pnpm workspaces in `pnpm-workspace.yaml`, and records the Python runtime in `.python-version` as exactly `3.14.6`.

Python setup is owned by uv:

```powershell
uv python install 3.14.6
uv sync --python 3.14.6
```

The backend Docker image must use `python:3.14.6-slim` once the backend is scaffolded.

## Fixed Technical Decisions

- Use pnpm workspaces for all JavaScript and TypeScript packages.
- Use uv for Python installation, dependency management, virtual environments, locking, and command execution.
- Use Next.js App Router, React, TypeScript, Tailwind CSS, Radix UI, lucide-react, TanStack Query, Zod, and openapi-typescript for the frontend.
- Use FastAPI, Pydantic v2, SQLAlchemy 2.x async ORM, Alembic, asyncpg, pytest, pytest-asyncio, Hypothesis, Ruff, and basedpyright for the backend.
- Use Playwright for browser end-to-end tests.
- Use Postgres full-text search plus pgvector-backed local embeddings for RAG.
- Use the Python MCP SDK from `services/api/app/mcp/server.py`.
- Use generated OpenAPI schemas as the frontend/backend contract.
- Use original local vector art only. Do not copy board scans, logos, illustrations, or other IP-protected assets from the internet.
- Use real `codex exec --json` subprocesses for AI players with xhigh reasoning and structured output.

The planned AI launch shape is:

```powershell
codex exec --json --ephemeral -a never `
  -c 'model_reasoning_effort="xhigh"' `
  --output-schema services/api/app/ai/schemas/agent_decision.schema.json `
  -C services/api/app/ai/sandbox -
```

No fallback actions are allowed for AI failures. Malformed output, invalid actions, invalid deals, timeouts, and rejected schemas must be audited and rejected rather than replaced with substitute moves.

## Command Surface

Install root JavaScript workspace metadata:

```powershell
pnpm install
```

Run the scaffolded quality gates:

```powershell
pnpm run test
pnpm run lint
pnpm run typecheck
```

Available root commands:

| Command | Phase 0 behavior |
| --- | --- |
| `pnpm run dev` | Runs the Phase 0 scaffold verifier for the future local dev surface. |
| `pnpm run test` | Runs unit, integration, e2e, and smoke scaffold gates. |
| `pnpm run test:unit` | Verifies repository baseline and command metadata. |
| `pnpm run test:integration` | Verifies repository baseline and command metadata. |
| `pnpm run test:e2e` | Verifies repository baseline and command metadata. |
| `pnpm run test:smoke` | Verifies repository baseline and command metadata. |
| `pnpm run lint` | Verifies required file, `.gitignore`, package, and README contracts. |
| `pnpm run format` | Runs the Phase 0 formatting gate placeholder. |
| `pnpm run typecheck` | Verifies package and repository metadata contracts. |
| `pnpm run review` | Runs lint, typecheck, and test. |

Equivalent Makefile targets delegate to pnpm:

```powershell
make test
make lint
make typecheck
make review
```

The Makefile also exposes uv setup targets:

```powershell
make python-install
make python-sync
```

## GitHub Branch And PR Workflow

The bootstrap commit seeds `main` with `AGENTS.md` and `PLANS.md`. After that, no direct commits go to `main`.

Each numbered phase uses the dedicated branch from `PLANS.md`. Phase 0 uses:

```powershell
feature/phase-0-project-control
```

Start a phase from updated `main`:

```powershell
git checkout main
git pull --ff-only origin main
git checkout -b <phase-branch-from-PLANS.md>
```

Commit and push coherent working increments:

```powershell
git add .
git commit -m "phase N stage M: imperative summary"
git push -u origin <phase-branch-from-PLANS.md>
```

After the first branch push:

```powershell
git push
```

At phase end, open a pull request into `main` with GitHub CLI. The supervisor will handle review, PR, and merge for this Phase 0 worker task.

```powershell
gh pr create --base main --head <phase-branch-from-PLANS.md> --title "Phase N: <phase title from PLANS.md>" --body "Summary, tests, and review notes"
```

No new phase starts from an unmerged phase branch.

## Supervisor Usage

Codex Supervisor owns durable task intent, attempts, evidence, acceptance, and auditability. Product files are every file outside `.codex-supervisor/`. Supervisor-owned files under `.codex-supervisor/` are local state and must not be committed.

This repository ignores `.codex-supervisor/` in `.gitignore`. Before committing, verify:

```powershell
git check-ignore -q -- .codex-supervisor/planning.sqlite3
git ls-files -- .codex-supervisor
git status --short
```

Expected results:

- `git check-ignore` exits successfully.
- `git ls-files -- .codex-supervisor` prints nothing.
- `git status --short` shows only intentional product-file changes.

Workers mutate product files. The supervisor records evidence and acceptance for those worker attempts.

## Phase 0 Verification

Required Phase 0 checks:

```powershell
pnpm run test
pnpm run lint
pnpm run typecheck
```

The verifier is `scripts/phase0_check.py`. It checks required files, `.python-version`, package scripts, `.gitignore` coverage, README markers, and Makefile delegation.
