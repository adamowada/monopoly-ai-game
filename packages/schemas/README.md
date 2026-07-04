# Shared Schemas

This package is the shared schema generation area for frontend and backend contracts.

Stage 1.5 generates the FastAPI OpenAPI contract into `openapi.json`, generates TypeScript API
types into `src/generated/openapi.ts`, and exposes shared values for phases, actions, players, and
entity IDs. Regenerate the contract with:

```powershell
pnpm run generate:api
```

Contract tests live in `tests/` and verify that frontend health expectations match the backend
schema:

```powershell
pnpm run test:contract
```
