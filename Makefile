.PHONY: dev generate-api test test-contract test-unit test-integration test-e2e test-smoke test-scaffold test-web test-api lint format typecheck review python-install python-sync

dev:
	pnpm run dev

generate-api:
	pnpm run generate:api

test:
	pnpm run test

test-contract:
	pnpm run test:contract

test-unit:
	pnpm run test:unit

test-integration:
	pnpm run test:integration

test-e2e:
	pnpm run test:e2e

test-smoke:
	pnpm run test:smoke

test-scaffold:
	pnpm run test:scaffold

test-web:
	pnpm run test:web

test-api:
	pnpm run test:api

lint:
	pnpm run lint

format:
	pnpm run format

typecheck:
	pnpm run typecheck

review:
	pnpm run review

python-install:
	uv python install 3.14.6

python-sync:
	uv sync --python 3.14.6
