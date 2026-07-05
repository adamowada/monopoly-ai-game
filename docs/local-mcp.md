# Local MCP

Stage 9.3 exposes a local-only stdio MCP server for Codex workers. It does not start a network
listener, remote MCP endpoint, hosted service, websocket, or multiplayer channel.

Run it from `services/api`:

```powershell
uv run python scripts/local_mcp_server.py
```

For supervisor startup checks, smoke mode prints one JSON object with registered tools:

```powershell
uv run python scripts/local_mcp_server.py --smoke
```

Configuration uses the same local backend settings as FastAPI. Set `DATABASE_URL` or keep the
default local Postgres URL. Retrieval tools read the Stage 9.2 local index; build or refresh that
index with the existing local index command before expecting search results.

The validation boundary is explicit: `submit_action` is the only MCP tool that can mutate game state. It calls the
local FastAPI action path `/games/{game_id}/actions` through an in-process ASGI client and sends the
required `Idempotency-Key` header. Legal actions are accepted only by backend validation. Illegal,
stale, malformed, or AI-forbidden actions are rejected and audited by the same FastAPI path.

Read tools can inspect persisted state and retrieval records but do not mutate game state:

- `get_game_state`: input `{ "game_id": "<uuid>" }`
- `get_legal_actions`: input `{ "game_id": "<uuid>", "actor_player_id": "<uuid>" }`
- `search_rules`: input `{ "query_text": "...", "game_id": "<uuid>", "limit": 6 }`
- `search_memory`: input `{ "query_text": "...", "game_id": "<uuid>", "player_id": "<uuid>" }`
- `inspect_contract`: input `{ "game_id": "<uuid>", "contract_id": "<uuid>" }`
- `validate_deal_draft`: input `{ "game_id": "<uuid>", "draft": { ...CreateDealRequest } }`

`validate_deal_draft` validates structured deal terms and player references without creating a deal,
contract, obligation, event, or negotiation mutation.

Mutation tool:

- `submit_action`: input
  `{ "game_id": "<uuid>", "idempotency_key": "...", "action": { "actor_id": "...", "type": "...", "payload": {}, "expected_state_hash": "...", "expected_event_sequence": 0 } }`

The server implements newline-delimited JSON-RPC over stdio for `initialize`, `tools/list`, and
`tools/call`. Tool payload schemas are advertised by `tools/list`.
