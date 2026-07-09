# PLANS.md

## Objective

Build a fully functional, fully playable, local-only Monopoly-style AI research game using a 3-tier architecture:

- Frontend: Next.js web application.
- Backend: FastAPI service.
- Database: Postgres.

The product must run locally on this machine, support 2-5 hotseat players, allow any player to be human or AI-controlled, and provide a deterministic rules engine that validates and enforces all game actions, negotiations, trades, contracts, and financial instruments.

The project is an educational and research playground for strategic, competitive AI agents. It must be live, usable, playable, testable, inspectable, and operational end to end.

## Non-Negotiable Requirements

- The finished app must run locally with `docker compose up --build`.
- The finished app must open at `http://localhost:3000`.
- The app must use Next.js, FastAPI, and Postgres.
- The game must support 2-5 players.
- Players are configured as human or AI-controlled.
- Human play is hotseat only with mouse and keyboard on the same computer.
- There is no online multiplayer, no controller support, and no deployed/public mode.
- The game must implement classic Monopoly rules, card effects, property data, rents, houses, hotels, auctions, mortgages, jail, bankruptcy, and game-over behavior.
- Visual assets must be original local vector art. Do not copy board scans, logos, illustrations, or other IP-protected visual assets from the internet.
- AI players must be controlled by real `codex exec --json` subprocesses using `gpt-5.4-mini` with light reasoning.
- No fallback actions are allowed, ever.
- Legal actions are accepted and committed.
- Illegal actions are rejected with structured validation errors.
- Malformed AI output is rejected.
- Invalid AI deal proposals are rejected.
- Negotiations that take too long are closed by deterministic cutoff rules.
- The system must never invent, substitute, or silently coerce a move for a player.
- All players, human and AI, must be able to negotiate, trade, counteroffer, accept, reject, and execute complex agreements.
- The game must support complex financial instruments and deviations from classic Monopoly trading rules.
- A deterministic rules engine must validate legality and enforce agreements.
- AI players must have persistent, auditable self-dialogue and memory across the game.
- RAG and MCP are mandatory local support systems for AI decisions, and they must not bypass backend validation.
- Development must use TDD and maintain unit, integration, end-to-end, smoke, and regression tests.
- Codex has authority to install, download, configure, and use required libraries, packages, tools, Docker images, and dependencies without asking for permission.
- Codex must not pause to ask for clarification during the long-running process. It must make best informed judgments and continue toward the finished product.

## Architecture Overview

### Frontend

The frontend is a Next.js TypeScript application that renders the board, player panels, turn controls, deal builder, negotiation inbox, contract/obligation views, AI audit views, and game logs. It does not decide legality. It only displays legal actions returned by the backend and submits selected actions for validation.

### Backend

The backend is a FastAPI Python service that owns game state, deterministic rules, action validation, event commitment, contracts, negotiations, AI process orchestration, memory, retrieval, and audit records.

### Database

Postgres stores immutable game events, snapshots, players, games, negotiations, deals, contracts, obligations, AI decisions, rejected action records, self-dialogue, memory entries, retrieval records, and test fixtures.

### Rules Engine

The rules engine is event-sourced. Every accepted action becomes an immutable event. Current game state is produced by replaying events from an initial seed plus deterministic RNG state. The engine exposes legal actions by phase and rejects anything outside the current rules window.

### AI Runtime

AI players are persistent identities backed by memory and audit history. Each decision is made by a real subprocess using:

```powershell
codex -a never exec --json --ephemeral `
  --model gpt-5.4-mini `
  -c 'model_reasoning_effort="light"' `
  --output-schema services/api/app/ai/schemas/agent_decision.schema.json `
  -C services/api/app/ai/sandbox -
```

The implementation must create the referenced schema and sandbox paths. The AI runtime must use `codex exec --json`, `gpt-5.4-mini` with light reasoning, structured output, backend validation, and no fallback moves.

## Fixed Technical Decisions

- Local toolchain verified on this machine:
  - Node.js `v24.11.0`.
  - npm `9.8.1`.
  - pnpm `11.7.0`.
  - Global Python currently installed: `3.12.10`.
  - Project Python runtime: `3.14.6`.
  - uv `0.11.7`.
  - Docker `29.5.3`.
  - Docker Compose `v5.1.4`.
  - Git `2.51.2.windows.1`.
  - Codex CLI `0.133.0`.
- Use pnpm workspaces for all JavaScript and TypeScript packages.
- Use uv for Python `3.14.6` installation, dependency management, virtual environments, locking, and command execution.
- Add `.python-version` containing `3.14.6`.
- Set backend `requires-python` to `==3.14.6`.
- Use `uv python install 3.14.6` and `uv sync --python 3.14.6` for local backend setup.
- Use `python:3.14.6-slim` as the backend Docker base image.
- Use Next.js App Router, React, TypeScript, Tailwind CSS, Radix UI, lucide-react, TanStack Query, Zod, and openapi-typescript for the frontend.
- Use FastAPI, Pydantic v2, SQLAlchemy 2.x async ORM, Alembic, asyncpg, pytest, pytest-asyncio, Hypothesis, Ruff, and basedpyright for the backend.
- Use Playwright for end-to-end browser testing.
- Use `pgvector/pgvector:pg17` as the Postgres Docker image.
- Use Postgres full-text search plus pgvector-backed local embeddings for RAG.
- Use the Python MCP SDK to expose the local MCP server from `services/api/app/mcp/server.py`.
- Use generated OpenAPI schemas as the frontend/backend contract.

## Development Operating Rules

### TDD Loop

For every meaningful behavior, write or update tests before implementation, watch them fail for the expected reason, implement the behavior, then make the tests pass. UI-heavy work uses component tests and end-to-end tests around the intended behavior before implementation.

### Test Types

The final suite must include:

- Unit tests for pure rules, validators, serializers, reducers, utility functions, and contract math.
- Integration tests for FastAPI endpoints, database persistence, transactions, event replay, AI process wrappers, and RAG/MCP boundaries.
- End-to-end tests for full browser play flows using Playwright.
- Smoke tests that boot the local stack and prove a playable game creation and advancement flow.
- Regression tests for fixed bugs and edge cases discovered during development.

### Review Process

Run these review passes at the end of each phase:

- Rules review for game correctness and deterministic state.
- Backend review for persistence, API contracts, and event-sourcing integrity.
- Frontend review for playability, timing, and UI correctness.
- AI review for process isolation, schema validity, memory, and auditability.
- Final product review for local boot, tests, and playability.

### Git Process

Use `git add`, `git commit`, and `git push` after every completed stage and after every bug-fix cluster. Commits must represent coherent working increments. Do not wait until the end to commit a large unreviewable batch.

One repository bootstrap commit seeds `main` with `AGENTS.md` and `PLANS.md`. This bootstrap commit is not a numbered phase. After that bootstrap commit, all numbered phases use feature branches and pull requests.

Each phase uses a dedicated feature branch created from `main`.

One worker process may perform at most one stage of a given phase. After completing a stage, that worker must stop instead of continuing into another stage of the same phase.

Phase branch map:

- Phase 0: `feature/phase-0-project-control`
- Phase 1: `feature/phase-1-local-3-tier-foundation`
- Phase 2: `feature/phase-2-deterministic-rules-engine`
- Phase 3: `feature/phase-3-turn-timing-model`
- Phase 4: `feature/phase-4-persistence-api-auditability`
- Phase 5: `feature/phase-5-playable-nextjs-frontend`
- Phase 6: `feature/phase-6-negotiation-financial-instruments`
- Phase 7: `feature/phase-7-codex-ai-runtime`
- Phase 8: `feature/phase-8-ai-memory-audit`
- Phase 9: `feature/phase-9-local-rag-mcp`
- Phase 10: `feature/phase-10-testing-suite`
- Phase 11: `feature/phase-11-review-hardening-finish`

Start every phase with:

```powershell
git checkout main
git pull --ff-only origin main
git checkout -b <phase-branch-from-map>
```

During every phase, stage, commit, and push coherent increments:

```powershell
git add .
git commit -m "phase N stage M: imperative summary"
git push -u origin <phase-branch-from-map>
```

After the first push for a branch, use:

```powershell
git push
```

End every phase with a GitHub pull request into `main`. Codex must complete the phase review process, merge the pull request into `main`, update local `main`, and create the next phase branch from the updated `main`.

Use GitHub CLI for pull requests and merges:

```powershell
gh pr create --base main --head <phase-branch-from-map> --title "Phase N: <phase title from this file>" --body "Summary, tests, and review notes"
gh pr merge --squash --delete-branch
git checkout main
git pull --ff-only origin main
```

No new phase starts from an unmerged phase branch.

No direct commits to `main` occur after the repository bootstrap commit.

## Phase 0: Project Control And Baseline

### Stage 0.1: Repository Baseline

Establish the repository as the source of truth for the build.

Deliverables:

- Confirm Git repository is initialized.
- Preserve existing `AGENTS.md`.
- Preserve existing `PLANS.md`.
- Add a root `README.md`.
- Add a root `.gitignore` for Node, Python, Postgres, Docker, logs, local env files, test artifacts, and generated caches.
- Record the intended local-only architecture in README.

Done when:

- `git status` clearly shows only intentional files.
- The repository root contains `AGENTS.md`, `PLANS.md`, `.gitignore`, and README documentation.
- Make Phase 0 Stage 0.1 commits without generated dependencies or secrets.
- Push the Phase 0 branch to GitHub.
- Open, review, merge, and delete the Phase 0 pull request before Phase 1 starts.

### Stage 0.2: Development Toolchain Discovery

Validate and document the fixed local toolchain.

Deliverables:

- Record the verified versions of Node.js, npm, pnpm, global Python, project Python, uv, Docker, Docker Compose, Git, and Codex CLI.
- Configure pnpm workspaces as the JavaScript and TypeScript package manager.
- Configure uv as the Python package manager and task runner.
- Install project Python `3.14.6` through uv.
- Verify `codex exec --json` support.
- Verify the local Codex config supports `gpt-5.4-mini` with light reasoning through `--model gpt-5.4-mini -c model_reasoning_effort="light"`.
- Document required commands in README.

Done when:

- The fixed package managers and runtime versions are documented.
- `pnpm install` installs JavaScript and TypeScript dependencies.
- `uv sync --python 3.14.6` installs Python dependencies.
- The Codex AI launch approach is documented and testable.

### Stage 0.3: Quality Gates And Command Surface

Create a stable command surface for long-running autonomous work.

Deliverables:

- Add root commands for `dev`, `test`, `test:unit`, `test:integration`, `test:e2e`, `test:smoke`, `lint`, `format`, `typecheck`, and `review`.
- Add root `package.json` scripts for every command.
- Add a root `Makefile` that delegates to the package and uv commands.
- Ensure every command runs from PowerShell and through Docker.
- Add a smoke command that verifies service health during scaffolding and expands into gameplay smoke tests during later phases.

Done when:

- Running the root test command succeeds on the scaffolded suite.
- Running the root lint/typecheck commands succeeds.
- Command names are documented in README.

## Phase 1: Local 3-Tier Foundation

### Stage 1.1: Monorepo Scaffold

Create the project layout for independent frontend, backend, shared schema, content, and asset work.

Deliverables:

- `apps/web` Next.js TypeScript application.
- `services/api` FastAPI Python application.
- `packages/schemas` shared schema generation area.
- `content/rules` for local rule/card/property source data.
- `assets/vector` for original SVG artwork.
- Root dependency and workspace configuration.

Done when:

- The frontend app can start independently.
- The backend app can start independently.
- Shared schema/content/asset directories exist and have documented purpose.
- Initial scaffold tests pass.

### Stage 1.2: Docker Compose Stack

Create the local runtime stack.

Deliverables:

- `docker-compose.yml` with Postgres, FastAPI, and Next.js services.
- Backend Dockerfile based on `python:3.14.6-slim`.
- Named Postgres volume for local persistence.
- Health checks for Postgres and API.
- Environment file template with non-secret local defaults.
- Dockerfiles for frontend and backend.

Done when:

- `docker compose up --build` starts all services.
- Postgres accepts connections from the API.
- The API health endpoint responds from inside Docker.
- The frontend can call the API through the configured local URL.

### Stage 1.3: FastAPI App Skeleton

Build the API foundation before game behavior.

Deliverables:

- FastAPI application factory.
- `/health` endpoint.
- Structured logging.
- CORS configured for the local Next.js origin.
- Pydantic settings model.
- Database connection setup.
- Alembic migrations configured.
- Pytest configured.

Done when:

- `pytest` passes for API health and configuration tests.
- Alembic can create and inspect the database schema.
- API startup fails loudly on invalid configuration.

### Stage 1.4: Next.js App Skeleton

Build the frontend foundation before game UI.

Deliverables:

- Next.js App Router structure.
- TypeScript configuration.
- Tailwind CSS, Radix UI, and lucide-react installed and configured.
- API client layer.
- Basic app shell.
- Frontend test setup.
- Playwright setup.

Done when:

- The browser shows a local app shell.
- The app shell displays backend health status.
- Frontend unit/component tests pass.
- A first Playwright test opens the app and verifies health connectivity.

### Stage 1.5: Shared API Contract

Create a reliable schema boundary between frontend and backend.

Deliverables:

- OpenAPI generation from FastAPI.
- TypeScript API client generation with openapi-typescript.
- Shared enum/value definitions for phases, action types, player types, and IDs.
- Contract tests verifying frontend expectations match backend schemas.

Done when:

- Frontend code consumes generated or validated API types.
- API schema generation is part of the normal build/test flow.
- Contract tests fail on backend response shape drift.

## Phase 2: Deterministic Rules Engine

### Stage 2.1: Domain Data Model

Encode the game board and static game data.

Deliverables:

- Board spaces for a 40-space classic Monopoly layout.
- Property groups, prices, rents, mortgage values, house costs, hotel costs, and group metadata.
- Railroads, utilities, taxes, jail, go, free parking, go-to-jail, chance, and community chest spaces.
- Chance and Community Chest deck definitions.
- Bank inventory constants, including 32 houses and 12 hotels.
- Static data validation tests.

Done when:

- Static data loads without runtime errors.
- Tests verify board length, unique positions, unique IDs, complete card decks, and valid property group definitions.
- Property and card data serialize for frontend display.

### Stage 2.2: Core State Model

Define the complete deterministic game state.

Deliverables:

- Game state model.
- Player state model.
- Property ownership and improvement model.
- Deck and discard state.
- Bank inventory state.
- Jail state.
- Turn and phase state.
- Active payment, auction, negotiation, and bankruptcy state slots.
- State hashing function.

Done when:

- New game state creation from seed and player setup works.
- State hash is stable for identical state.
- Unit tests cover serialization, deserialization, equality, and hash stability.

### Stage 2.3: Event Model And Reducer

Make all accepted changes flow through immutable events.

Deliverables:

- Event type definitions for every accepted game mutation.
- Pure reducer that applies an event to a state.
- Event validation boundary.
- Replay function from initial seed plus event list.
- Tests for reducer purity and deterministic replay.

Done when:

- No game mutation path bypasses events.
- Replay of a committed event list recreates the same state hash.
- Tests catch out-of-order, duplicate, and invalid event application.

### Stage 2.4: Deterministic RNG

Implement reproducible randomness for dice and card order.

Deliverables:

- Seeded RNG utility.
- Dice roll event generation.
- Deterministic card deck shuffling.
- RNG seed and draw counters stored in state.
- Dice outcomes and card draw outcomes stored in accepted events.
- Tests proving repeatability for same seed and divergence for different seeds.

Done when:

- Two games with the same seed and same actions produce the same dice/card sequence.
- Dice and card draws are replayable from the event log.
- RNG usage is isolated and never called directly from UI or persistence code.

### Stage 2.5: Classic Rule Mechanics

Implement the core Monopoly rules.

Deliverables:

- Start cash and player initialization.
- Passing GO.
- Rolling, doubles, and triple-doubles jail behavior.
- Buying unowned properties.
- Auctions.
- Rent calculation for properties, railroads, and utilities.
- Taxes.
- Chance and Community Chest effects.
- Jail payment/card/doubles choices.
- Mortgages and unmortgages.
- Even building rule.
- Selling houses/hotels.
- House/hotel scarcity.
- Bankruptcy and asset transfer/liquidation.
- Game-over detection.

Done when:

- Unit tests cover normal and edge cases for every listed mechanic.
- A deterministic scripted game can progress from setup to game over.
- Every required classic rule mechanic has implementation and tests.

### Stage 2.6: Legal Action Generator

Expose only actions that are legal in the current state.

Deliverables:

- Legal action enumeration by state and phase.
- Action schemas for every legal action.
- Validator that rejects illegal, malformed, stale, or mistimed actions.
- Structured validation errors.
- Tests for positive and negative action cases.

Done when:

- The frontend and AI can request legal actions without knowing rule internals.
- Illegal actions are rejected without mutating game state.
- Tests prove rejected actions do not appear in the committed event log.

### Stage 2.7: Simulation Harness

Create a non-UI path to exercise entire games.

Deliverables:

- Scripted player harness.
- Random legal-action player for stress tests.
- Deterministic simulation command.
- Invariant checks after every action.

Done when:

- Hundreds of simulated turns can run without invariant failures.
- A full game can complete with simple scripted players.
- Simulation failures print enough state/action detail to reproduce the bug.

## Phase 3: Explicit Turn And Timing Model

### Stage 3.1: Phase State Machine

Encode the authoritative action timing model.

Deliverables:

- Phase enum including:
  - `START_TURN`
  - `PRE_ROLL_MANAGEMENT`
  - `ROLL_REQUIRED`
  - `MOVEMENT_RESOLUTION`
  - `SPACE_RESOLUTION`
  - `PURCHASE_OR_AUCTION`
  - `PAYMENT_RESOLUTION`
  - `JAIL_RESOLUTION`
  - `POST_ROLL_MANAGEMENT`
  - `NEGOTIATION_WINDOW`
  - `END_TURN`
  - `BANKRUPTCY_RESOLUTION`
  - `GAME_OVER`
- Phase transition table.
- Tests for valid and invalid phase transitions.

Done when:

- Every accepted event either preserves phase or moves to a valid next phase.
- No player action can skip an unresolved mandatory phase.
- State always contains exactly one current phase.

### Stage 3.2: Action Timing Windows

Define when players are allowed to propose deals, trade, manage assets, bid, or respond.

Deliverables:

- Timing rules for deal proposal windows.
- Timing rules for trade acceptance and rejection.
- Timing rules for auctions.
- Timing rules for house/hotel purchase and sale.
- Timing rules for mortgage and unmortgage.
- Timing rules for debt and bankruptcy resolution.
- Tests for legal and illegal timing.

Done when:

- Deals are proposed during legal management and negotiation windows.
- Deals cannot be proposed during atomic movement, card resolution, rent/tax resolution, or unresolved mandatory payment effects.
- House/hotel changes are allowed only during legal management or liquidation windows.

### Stage 3.3: Atomic Resolution Sections

Prevent interleaving of actions that would corrupt rules.

Deliverables:

- Atomic resolution markers for dice, movement, card draws, card effects, payment creation, and forced movement.
- Backend protections against concurrent or stale actions.
- Tests for submitting actions during atomic sections.

Done when:

- A player cannot trade halfway through movement or before a card effect fully resolves.
- Concurrent requests cannot double-commit or bypass phase locks.
- Rejected timing attempts are audited.

### Stage 3.4: Debt And Bankruptcy Timing

Define what a player can do when they owe money.

Deliverables:

- Debt resolution state model.
- Legal liquidation actions.
- Rules for selling improvements, mortgaging, using cash, and bankruptcy declaration.
- Rules for whether negotiations are allowed during debt resolution.
- Tests for solvable and insolvent debt cases.

Done when:

- A player with debt cannot end turn until the debt is settled or bankruptcy resolves.
- Asset management allowed during debt resolution is limited to legal liquidation actions.
- Bankruptcy produces deterministic asset/cash outcomes.

## Phase 4: Persistence, API, And Auditability

### Stage 4.1: Database Schema

Create durable storage for games and all audit records.

Deliverables:

- Alembic migrations for:
  - `games`
  - `players`
  - `game_events`
  - `game_snapshots`
  - `rejected_actions`
  - `negotiations`
  - `negotiation_messages`
  - `deals`
  - `contracts`
  - `obligations`
  - `ai_profiles`
  - `ai_decisions`
  - `ai_self_dialogue`
  - `ai_memory_entries`
  - `retrieval_records`
- Indexes for game ID, player ID, event sequence, and audit lookup.
- Migration tests.

Done when:

- A fresh database can migrate from zero to current schema.
- Tests can create and tear down isolated database state.
- Schema supports accepted events and rejected audit records separately.

### Stage 4.2: Event Persistence And Snapshotting

Persist game state changes safely.

Deliverables:

- Transactional event append.
- Monotonic event sequence per game.
- Periodic snapshot creation.
- Replay from latest snapshot plus subsequent events.
- Snapshot verification command.

Done when:

- Accepted actions commit exactly one ordered event or fail without mutation.
- Replay from event zero and replay from latest snapshot produce the same state hash.
- Snapshot corruption is detected by tests or verification command.

### Stage 4.3: Rejected Action Audit

Make invalid attempts visible and inspectable.

Deliverables:

- Rejected action persistence.
- Structured reason codes.
- Actor, phase, legal-action context, payload, validation errors, and timestamp.
- API endpoint for rejected actions.
- Frontend audit view for rejected actions.
- Tests proving rejected actions never mutate state.

Done when:

- Every illegal or malformed action submitted through the API creates an audit record.
- The game event stream contains only accepted legal events.
- Rejection records are queryable by game and actor.

### Stage 4.4: Game API

Expose the backend functions needed by the frontend and AI runtime.

Deliverables:

- `POST /games`
- `GET /games/{game_id}`
- `GET /games/{game_id}/state`
- `GET /games/{game_id}/legal-actions`
- `POST /games/{game_id}/actions`
- `GET /games/{game_id}/events`
- `GET /games/{game_id}/rejected-actions`
- `POST /games/{game_id}/negotiations`
- `POST /games/{game_id}/deals`
- `POST /games/{game_id}/ai/step`
- SSE endpoint for game events.

Done when:

- API integration tests cover success and failure paths for each endpoint.
- Frontend can create and load a game using API calls only.
- OpenAPI accurately describes request and response schemas.

### Stage 4.5: Transaction And Concurrency Safety

Ensure local play cannot corrupt game state through repeated clicks or simultaneous AI calls.

Deliverables:

- Optimistic event sequence precondition.
- Database transaction boundaries around validation and event append.
- Idempotency keys for every submitted action.
- Tests for stale state submission and duplicate requests.

Done when:

- Repeated browser clicks cannot commit duplicate actions.
- Stale AI output is rejected with a structured error.
- Concurrent submissions leave the game in a valid state.

## Phase 5: Playable Next.js Frontend

### Stage 5.1: Game Setup UI

Allow creation of local games.

Deliverables:

- Setup screen for 2-5 players.
- Human/AI player type selector.
- Player names and colors.
- Seed input with generated default seed and manual override.
- Basic game settings, including negotiation cutoff settings.
- Create-game API integration.

Done when:

- A user can create a 2-5 player game from the browser.
- Invalid setup choices are blocked client-side and server-side.
- A Playwright test creates a game and lands on the game board.

### Stage 5.2: SVG Board And Original Vector Assets

Create the visual board and game pieces without copied art.

Deliverables:

- Original SVG board layout.
- Original property color bands.
- Original card backs/fronts.
- Original token markers.
- House and hotel vector icons.
- Ownership and mortgage visual markers.
- Asset usage documentation.

Done when:

- The board displays all 40 spaces.
- Player positions are visible and update after movement.
- Assets are local vector files or inline SVG components.
- No downloaded/copy-protected board scans or official artwork are used.

### Stage 5.3: Turn Controls From Legal Actions

Build the main play interface around backend legal actions.

Deliverables:

- Active player panel.
- Roll dice control.
- Buy/pass/auction controls.
- Rent/tax payment controls.
- Jail action controls.
- End turn control.
- Disabled/loading/rejected states.
- Legal action refresh through TanStack Query plus SSE invalidation.

Done when:

- The UI shows only actions returned by the backend as currently legal.
- Submitting an action updates the board and logs.
- Rejected actions are shown clearly without mutating the visible game state.

### Stage 5.4: Property And Asset Management UI

Support all core asset operations.

Deliverables:

- Property list by owner.
- Property detail cards.
- Mortgage/unmortgage controls.
- Build/sell house controls.
- Hotel conversion controls.
- Bank inventory display.
- Monopoly group status display.

Done when:

- Human players can manage properties during legal timing windows.
- Building controls enforce even-building and scarcity through backend validation.
- UI reflects mortgage and improvement state correctly.

### Stage 5.5: Auction UI

Implement playable auctions.

Deliverables:

- Auction state panel.
- Bid controls.
- Pass controls.
- Current high bid display.
- Remaining bidders display.
- Auction result event display.

Done when:

- An unpurchased property can enter auction.
- All eligible players can bid or pass.
- The backend commits the auction result and transfers ownership/cash.

### Stage 5.6: Negotiation And Deal Builder UI

Give humans a rich interface for trades and financial instruments.

Deliverables:

- Negotiation inbox.
- Negotiation thread view.
- Freeform message composer.
- Structured deal builder.
- Counteroffer workflow.
- Accept/reject/expire status display.
- Contract preview before acceptance.

Done when:

- A human can propose, counter, accept, and reject deals through the UI.
- Complex deal terms are visible before acceptance.
- Expired negotiations are visibly closed and cannot execute.

### Stage 5.7: Contracts, Obligations, And Game Log UI

Make the economic state understandable.

Deliverables:

- Active contracts panel.
- Upcoming obligations panel.
- Past obligation settlement history.
- Full game log.
- Filters for actions, deals, AI decisions, and rejections.

Done when:

- Players can inspect why money or property moved.
- Contract-triggered transfers are linked to their source agreement.
- The game log is understandable during a live game.

### Stage 5.8: AI Audit UI

Expose AI reasoning records for research and education.

Deliverables:

- AI profile view.
- Decision history.
- Self-dialogue timeline.
- Memory entries.
- Retrieved context records.
- Rejected AI outputs and validation errors.

Done when:

- A user can inspect what each AI considered and remembered across turns.
- Each AI decision traces to state, legal actions, retrieved context, and output.
- Private AI audit information is available locally for research inspection.

## Phase 6: Negotiation And Complex Financial Instruments

### Stage 6.1: Negotiation State Machine

Model negotiation as deterministic game-adjacent state.

Deliverables:

- Negotiation lifecycle:
  - opened
  - active
  - countered
  - accepted
  - rejected
  - expired
  - executed
- Participant model.
- Round counter.
- Pending proposal tracking.
- Expiration rules.
- Tests for lifecycle transitions.

Done when:

- Negotiations execute only after all required parties accept the same final structured terms.
- Expired negotiations do nothing.
- Lifecycle transitions are deterministic and audited.

### Stage 6.2: Negotiation Cutoff Rules

Prevent infinite loops without fallback moves.

Deliverables:

- Max rounds per negotiation window.
- Max proposals per player per window.
- Max active wall-clock duration for human-facing waits.
- Max AI decision attempts per negotiation message.
- Max pending offers per player.
- Configurable game settings for negotiation intensity.
- Tests for each cutoff.

Done when:

- Long negotiations close as `expired`.
- Expiration never causes a substitute action.
- The game can continue only through a legal next action after expiration.

### Stage 6.3: Freeform Messages Plus Structured Deals

Separate conversation from executable commitments.

Deliverables:

- Freeform negotiation messages.
- Structured deal proposal schema.
- Deal versioning.
- Counteroffer linkage.
- Exact-term acceptance requirement.
- Deal validation errors.

Done when:

- Players can chat freely without changing game state.
- Only structured accepted deals can become contracts.
- A changed counteroffer invalidates previous acceptances as required.

### Stage 6.4: Financial Instrument Primitives

Implement the expanded transaction system.

Deliverables:

- Immediate cash transfer.
- Immediate property transfer.
- Deferred cash payment.
- Installment loan.
- Interest-bearing debt.
- Collateralized loan.
- Property purchase option.
- Rent share.
- Insurance-style payout.
- Conditional obligation.
- Guarantee.
- Default penalty.

Done when:

- Each primitive has unit tests for creation, validation, settlement, and failure cases.
- One deal represents combinations of primitives.
- Invalid instruments are rejected with clear reasons.

### Stage 6.5: Contract Execution And Enforcement

Make accepted deals durable and enforceable.

Deliverables:

- Contract creation from accepted deal.
- Obligation schedule generation.
- Trigger system for rent, turn start/end, property transfer, bankruptcy, and time/round conditions.
- Settlement engine.
- Default handling.
- Audit events for contract settlements.

Done when:

- Accepted contracts automatically enforce future obligations.
- Contract settlement creates accepted game events for cash/property mutations and contract audit records for bookkeeping.
- Defaults are deterministic and tested.

### Stage 6.6: Contract Interaction With Classic Rules

Define how custom instruments interact with the normal game.

Deliverables:

- Rules for whether contract obligations affect bankruptcy.
- Rules for collateral seizure.
- Rules for options on mortgaged or improved properties.
- Rules for rent sharing when rent is reduced, waived, or unpaid.
- Rules for obligations during jail, auction, and bankruptcy.
- Regression tests for edge cases.

Done when:

- Contract obligations do not leave money/property in impossible states.
- Bankruptcy resolves both classic debts and custom contract obligations deterministically.
- The UI and API can explain contract outcomes.

## Phase 7: Codex AI Player Runtime

### Stage 7.1: AI Profile And Personality System

Create persistent AI player identities.

Deliverables:

- AI profile model.
- Seeded personality generator.
- Strategy trait fields:
  - risk tolerance
  - liquidity preference
  - debt appetite
  - aggressiveness
  - cooperation
  - negotiation creativity
  - trust
  - monopoly focus
- Persona summary visible in audit UI.
- Tests for deterministic profile generation by seed.

Done when:

- AI players in the same seeded game get stable profiles.
- Different AI players have meaningfully varied traits.
- Profiles persist in Postgres.

### Stage 7.2: AI Decision Schema

Define the exact outputs Codex is allowed to return.

Deliverables:

- JSON schema for AI decisions.
- Action decision shape.
- Negotiation message shape.
- Deal proposal shape.
- Counteroffer shape.
- Accept/reject shape.
- Self-dialogue shape.
- Memory update shape.
- Confidence/rationale metadata.

Done when:

- AI output validates mechanically before every game mutation.
- Malformed or incomplete output is rejected and audited.
- The schema is used by `codex exec --json --output-schema`.

### Stage 7.3: Codex Exec Orchestrator

Launch and manage real Codex subprocesses.

Deliverables:

- Python subprocess wrapper.
- Prompt construction.
- stdin/stdout handling.
- JSONL event parsing.
- Timeout handling.
- Process error handling.
- Storage of raw AI output and parsed output.
- Tests using fake subprocess output.

Done when:

- Backend integration tests request AI decisions through a fake subprocess harness.
- Live smoke and final acceptance request AI decisions through a real `codex exec --json` process.
- Invalid process output is rejected without mutating game state.
- Timeouts produce audit records and do not create fallback moves.

### Stage 7.4: AI Context Pack Builder

Give AI enough context without surrendering authority.

Deliverables:

- Current public game state summary.
- Active phase and timing window.
- Legal actions only.
- Negotiation context.
- Active contracts and obligations.
- Relevant memory snippets.
- Relevant rule snippets.
- Personality profile.
- Required output schema and instruction contract.

Done when:

- Each AI prompt contains legal actions generated by the backend.
- Prompt context is recorded for audit.
- Context packs expose no other player's private memory. Public negotiation messages, public deals, public contracts, and public game state remain visible.

### Stage 7.5: AI Output Validation And Rejection

Enforce the no-fallback rule.

Deliverables:

- Schema validation.
- Legal action validation.
- Deal validation.
- Phase/timing validation.
- Rejected AI output records.
- One Codex subprocess attempt per AI decision request.
- Invalid, malformed, or timed-out mandatory AI decision marks the game `AI_BLOCKED` after storing the rejection audit record.
- Invalid, malformed, or timed-out non-mandatory AI negotiation response stores a rejection audit record and consumes that AI's response opportunity for the current negotiation round.

Done when:

- Legal AI actions commit exactly like legal human actions.
- Illegal AI actions are rejected exactly like illegal human actions.
- The system never substitutes a safe move, random move, or default move.

### Stage 7.6: AI Turn And Negotiation Flow

Integrate AI players into live gameplay.

Deliverables:

- AI turn stepping endpoint.
- Automatic AI step control.
- Manual AI step control.
- AI participation in negotiation windows.
- AI response to offers.
- AI ability to propose complex deals.
- UI indication when AI is thinking, rejected, blocked, or done.

Done when:

- A game with human and AI players can progress through normal turns.
- AIs can initiate and respond to negotiations.
- AI stalls are visible and auditable rather than silently resolved.

## Phase 8: AI Self-Dialogue, Memory, And Audit Trail

### Stage 8.1: Self-Dialogue Storage

Record AI internal reasoning artifacts for research inspection.

Deliverables:

- `ai_self_dialogue` table.
- Decision-linked self-dialogue entries.
- Timestamp, game state hash, phase, and actor linkage.
- UI display in AI audit view.
- Tests for persistence and retrieval.

Done when:

- Every AI decision stores self-dialogue or an explicit empty/rejected self-dialogue record.
- Self-dialogue is linked to the AI decision and game context.
- A user can inspect an AI's self-dialogue timeline locally.

### Stage 8.2: Memory System

Give each AI persistent memory that grows over the game.

Deliverables:

- Memory entry model.
- Memory categories:
  - strategic beliefs
  - player trust models
  - deal history
  - promises made
  - promises received
  - threats
  - grudges
  - opportunities
  - long-term plans
  - mistakes and lessons
- Evidence links to game events or negotiation messages.
- Memory update validation.

Done when:

- AI memory persists across turns and subprocess invocations.
- New decisions receive relevant prior memory.
- Memory entries audit back to evidence links.

### Stage 8.3: Memory Summarization And Compaction

Keep memory useful as games grow.

Deliverables:

- Memory importance scoring.
- On-demand memory compaction during AI context-pack construction.
- Scheduled memory compaction after every 25 AI decisions per player.
- Retention of raw source entries.
- Compacted summary entries linked to source entries.
- Tests for retrieval after compaction.

Done when:

- Long games do not require passing every historical memory to Codex.
- Important strategic memories remain retrievable.
- Compaction does not destroy auditability.

### Stage 8.4: AI Decision Audit Records

Make every AI output traceable.

Deliverables:

- `ai_decisions` table.
- Prompt/context hash.
- Raw JSONL output stored in Postgres.
- Parsed structured output.
- Validation result.
- Linked accepted event or rejected action.
- Retrieved context references.

Done when:

- Stored audit records reconstruct every AI decision.
- Accepted and rejected AI decisions are both visible.
- Researchers can inspect why an AI made or failed to make a move.

## Phase 9: Local RAG And MCP

### Stage 9.1: Local Retrieval Corpus

Build local-only retrieval sources.

Deliverables:

- Rules corpus from `content/rules`.
- House-rule/deviation corpus.
- Contract example corpus.
- AI memory corpus.
- Negotiation history corpus.
- Past decision corpus.
- Index build command.

Done when:

- Retrieval sources are local and reproducible.
- Indexing runs without external services.
- Tests can retrieve expected snippets for known queries.

### Stage 9.2: Retrieval Implementation

Implement RAG for AI context packs.

Deliverables:

- Postgres full-text search.
- pgvector local vector search.
- Retrieval API inside backend.
- Ranking and filtering by game, player, phase, and source type.
- Retrieval record storage.
- Tests for relevance, filtering, and audit storage.

Done when:

- AI context packs include relevant retrieved rules and memories.
- Retrieval records show what was included and why.
- Retrieval exposes no other AI player's private memory.

### Stage 9.3: Local MCP Tooling

Expose local tools to Codex without bypassing validation.

Deliverables:

- Local MCP server implementation.
- Read/search tools:
  - `get_game_state`
  - `get_legal_actions`
  - `search_rules`
  - `search_memory`
  - `inspect_contract`
  - `validate_deal_draft`
- `submit_action` MCP tool that routes through FastAPI validation.
- MCP configuration documentation.
- Unit tests for MCP tool payloads.
- Smoke checks for MCP server startup and tool calls.

Done when:

- MCP tools are local-only.
- MCP cannot directly mutate state except through validated backend action submission.
- Codex AI decisions remain auditable and schema validated.

### Stage 9.4: RAG/MCP Boundary Tests

Prove retrieval and tools support reasoning without authority leakage.

Deliverables:

- Tests for private memory isolation.
- Tests for stale retrieved state rejection.
- Tests for invalid deal drafts.
- Tests for legal-action consistency between MCP and FastAPI.

Done when:

- MCP and RAG never become alternate rules engines.
- The FastAPI rules engine remains the only legal authority.
- Any stale or invalid tool-informed AI output is rejected.

## Phase 10: Testing Suite Expansion

### Stage 10.1: Backend Unit Tests

Cover pure backend logic.

Deliverables:

- Rules reducer tests.
- Legal action tests.
- Phase transition tests.
- Rent and payment tests.
- Card effect tests.
- House/hotel tests.
- Auction tests.
- Jail tests.
- Bankruptcy tests.
- Contract primitive tests.

Done when:

- Unit tests cover all core rule modules.
- Critical edge cases have named tests.
- Coverage reports identify no untested core rule files without justification.

### Stage 10.2: Backend Integration Tests

Cover database and API behavior.

Deliverables:

- API endpoint tests.
- Database transaction tests.
- Event persistence tests.
- Snapshot/replay tests.
- Rejected action audit tests.
- AI subprocess wrapper tests with fake process.
- Contract settlement integration tests.

Done when:

- Tests run against an isolated test database.
- Every major API endpoint has success and failure tests.
- Accepted/rejected action separation is proven.

### Stage 10.3: Property-Based And Invariant Tests

Stress the rules engine with generated scenarios.

Deliverables:

- Cash ledger reconciliation tests for every accepted event type.
- Ownership uniqueness tests.
- No negative bank inventory tests.
- House/hotel scarcity tests.
- Phase validity tests.
- Replay determinism tests.
- Random legal-action simulation tests.

Done when:

- Generated simulations run in CI/local test command.
- Invariant failures produce reproducible seeds.
- The rules engine survives long random legal-action sequences.

### Stage 10.4: Frontend Component Tests

Cover UI behavior in isolation.

Deliverables:

- Setup form tests.
- Board rendering tests.
- Legal action control tests.
- Property management tests.
- Negotiation/deal builder tests.
- Contract panel tests.
- AI audit view tests.

Done when:

- Components render expected state from fixture data.
- Controls submit expected API payloads.
- Rejected/loading/disabled states are tested.

### Stage 10.5: End-To-End Tests

Prove browser-level playability.

Deliverables:

- Create-game flow.
- Human turn flow.
- Buy property flow.
- Auction flow.
- Rent payment flow.
- Mortgage/build flow.
- Negotiation and accepted deal flow.
- Contract enforcement flow.
- Rejected action display flow.
- AI audit display flow with fake AI.

Done when:

- Playwright can run end-to-end tests against the local stack.
- Tests cover one complete full-table round in a 2-human-player game.
- Tests cover one complete full-table round in a 5-player mixed human/fake-AI game.
- Deal execution and later enforcement are verified in browser.

### Stage 10.6: Smoke Tests

Create fast confidence checks for the finished local product.

Deliverables:

- Docker stack smoke test.
- API health smoke test.
- Database migration smoke test.
- Game creation smoke test.
- Several-turn scripted smoke test.
- Live Codex AI smoke test command `test:smoke:live`, gated behind `RUN_LIVE_CODEX_AI=1` for routine local runs and required during final acceptance.

Done when:

- `test:smoke` can verify the app is live and playable enough to create and advance a game.
- Default smoke tests use fake AI for speed.
- `test:smoke:live` uses real `codex exec --json` AI and is required for final acceptance.
- Smoke failures clearly identify which tier is broken.

### Stage 10.7: Regression Tests

Capture bugs as permanent tests.

Deliverables:

- Regression test directory.
- Regression test naming convention.
- Reproduction fixture format.
- Seeds and event logs for known failures.
- Documentation requiring every fixed bug to add or update a regression test.

Done when:

- Fixed defects are represented by tests.
- Regression tests run in the normal suite.
- Every known bug regression fixture replays from seed and event log.

## Phase 11: Review, Hardening, And Product Finish

### Stage 11.1: Rules Correctness Review

Perform a focused review of game mechanics.

Deliverables:

- Checklist of classic mechanics implemented.
- Known deviations documented.
- Edge-case review for jail, auctions, mortgages, bankruptcy, card effects, and house scarcity.
- Fixes and regression tests for discovered issues.

Done when:

- The checklist has no unimplemented required mechanics.
- Known deviations are intentional and documented.
- Review findings are fixed before the next phase starts.
- Every review fix includes a regression test.

### Stage 11.2: AI And Audit Review

Review AI runtime, memory, and no-fallback behavior.

Deliverables:

- Verify Codex subprocess command.
- Verify `gpt-5.4-mini` light reasoning config.
- Verify schema validation.
- Verify invalid output rejection.
- Verify no fallback action path exists.
- Verify memory and self-dialogue persistence.
- Verify AI audit UI.

Done when:

- Code search confirms no fallback move generator exists for AI failures.
- AI invalid output produces rejection or `AI_BLOCKED`, not a substitute move.
- AI decisions are reconstructable from audit records.

### Stage 11.3: Frontend Playability Review

Review the app as a user-facing local game.

Deliverables:

- Manual playthrough with 2 human players.
- Manual playthrough with mixed human/AI players using real Codex AI.
- UI pass for layout, readability, and action clarity.
- Check that all necessary player actions are reachable.
- Fixes for friction and missing controls.

Done when:

- A user can play without using API tools directly.
- Every legal action category has a usable UI path.
- The board, panels, negotiations, and audit views remain readable during active play.

### Stage 11.4: Performance And Reliability Hardening

Make the local app robust enough for long games.

Deliverables:

- Query/index review.
- Long-game simulation.
- Memory growth test.
- Snapshot interval tuning.
- Frontend render performance check.
- AI subprocess timeout and cleanup verification.

Done when:

- Long simulated games do not show unacceptable slowdown or memory growth.
- Orphaned AI subprocesses are not left running after timeout/failure.
- Snapshotting keeps load/replay performance acceptable.

### Stage 11.5: Documentation And Runbook

Document how to run, test, and inspect the product.

Deliverables:

- README with install/run/test commands.
- Architecture overview.
- AI runtime explanation.
- Negotiation/contract explanation.
- RAG/MCP explanation.
- Troubleshooting section.
- Test command reference.

Done when:

- A local user can start the app from README instructions.
- A local user can run the test suite.
- A local user can understand where AI memory/audit records live.

### Stage 11.6: Final Local Acceptance

Verify the end-to-end product.

Deliverables:

- Clean local boot from `docker compose up --build`.
- Browser verification at `http://localhost:3000`.
- Passing unit tests.
- Passing integration tests.
- Passing end-to-end tests.
- Passing smoke tests.
- Passing regression tests.
- Final commit with working product.

Done when:

- A new local game is created through the browser.
- 2-5 players can play, with any mix of human and AI players.
- Human players can complete normal turns.
- AI players act through real `codex exec --json` subprocesses.
- Complex negotiations can occur and either execute by exact acceptance or expire by deterministic cutoff.
- Contracts and obligations are enforced by the backend.
- AI memory and self-dialogue are inspectable.
- Illegal actions are rejected and audited.
- No fallback actions exist.
- The final test suite passes.

## Final Product Description

After all phases are complete, the product is a local research game table:

- A custom vector Monopoly-style board rendered in a Next.js app.
- A FastAPI deterministic game referee.
- A Postgres event and audit ledger.
- Human hotseat play.
- Codex-powered AI players with persistent personality, self-dialogue, and memory.
- Complex negotiations and enforceable financial instruments.
- Full local auditability for actions, rejected actions, AI reasoning, retrieved context, contracts, and event replay.

The final product is not a prototype shell. It is a live, playable, inspectable local application.
