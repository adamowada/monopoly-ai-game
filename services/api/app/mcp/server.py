"""Small local stdio JSON-RPC MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

from app.mcp.tools import LocalMCPContext, LocalMCPToolError, call_local_tool, list_local_tools


JsonDict = dict[str, Any]


class LocalMCPServer:
    def __init__(self, context: LocalMCPContext | None = None) -> None:
        self.context = context or LocalMCPContext()

    async def handle_request(self, request: Mapping[str, Any]) -> JsonDict | None:
        method = request.get("method")
        request_id = request.get("id")
        if not isinstance(method, str):
            return _jsonrpc_error(request_id, -32600, "invalid JSON-RPC request")

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "monopoly-ai-game-local-mcp",
                        "version": "0.0.0",
                    },
                }
            elif method == "tools/list":
                result = {"tools": list_local_tools()}
            elif method == "tools/call":
                result = await self._handle_tool_call(request)
            elif method == "notifications/initialized":
                return None
            else:
                return _jsonrpc_error(request_id, -32601, f"unknown method: {method}")
        except LocalMCPToolError as exc:
            return _jsonrpc_error(request_id, -32602, str(exc))
        except Exception as exc:  # pragma: no cover - defensive stdio boundary
            return _jsonrpc_error(request_id, -32603, f"tool execution failed: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def _handle_tool_call(self, request: Mapping[str, Any]) -> JsonDict:
        params = request.get("params")
        if not isinstance(params, Mapping):
            raise LocalMCPToolError("tools/call params must be an object")
        raw_name = params.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise LocalMCPToolError("tools/call params.name must be a non-empty string")
        raw_arguments = params.get("arguments", {})
        if not isinstance(raw_arguments, Mapping):
            raise LocalMCPToolError("tools/call params.arguments must be an object")

        tool_payload = await call_local_tool(raw_name, raw_arguments, context=self.context)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(tool_payload, sort_keys=True, ensure_ascii=True),
                }
            ],
            "isError": False,
        }

    async def close(self) -> None:
        await self.context.close()


def smoke_payload() -> JsonDict:
    return {
        "server": "monopoly-ai-game-local-mcp",
        "transport": "stdio",
        "local_only": True,
        "tools": list_local_tools(),
    }


async def serve_stdio(
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    server: LocalMCPServer | None = None,
) -> int:
    resolved_server = server or LocalMCPServer()
    try:
        for line in input_stream:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                request = json.loads(stripped)
            except json.JSONDecodeError:
                response = _jsonrpc_error(None, -32700, "parse error")
            else:
                if not isinstance(request, Mapping):
                    response = _jsonrpc_error(None, -32600, "invalid JSON-RPC request")
                else:
                    response = await resolved_server.handle_request(request)
            if response is not None:
                output_stream.write(json.dumps(response, sort_keys=True, ensure_ascii=True) + "\n")
                output_stream.flush()
    finally:
        await resolved_server.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local stdio MCP server.")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Print a single JSON object listing registered local MCP tools.",
    )
    args = parser.parse_args(argv)
    if args.smoke:
        print(json.dumps(smoke_payload(), sort_keys=True, ensure_ascii=True))
        return 0
    return asyncio.run(serve_stdio())


def _jsonrpc_error(request_id: object, code: int, message: str) -> JsonDict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


__all__ = ["LocalMCPServer", "main", "serve_stdio", "smoke_payload"]
