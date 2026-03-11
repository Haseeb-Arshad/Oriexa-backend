"""Smoke-test Oriexa MCP transports.

This script verifies that both supported MCP transports work against a running
Oriexa API instance:

1. Streamable HTTP at /mcp
2. Standalone stdio via ``python -m oriexa_mcp.server``

It requires a pre-provisioned agent API key, then checks that:

- the MCP session initializes
- tools are listed successfully
- ``browse_tasks`` can be called on both transports
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


async def smoke_http(mcp_url: str, api_key: str) -> None:
    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            if "browse_tasks" not in tool_names:
                raise RuntimeError("Streamable HTTP MCP missing browse_tasks")

            result = await session.call_tool(
                "browse_tasks",
                {"api_key": api_key, "status": "open", "limit": 1},
            )
            if not result.content:
                raise RuntimeError("Streamable HTTP MCP returned empty tool result")

            print(f"[OK] streamable_http tools={len(tool_names)} result_items={len(result.content)}")


async def smoke_stdio(base_url: str, api_key: str, python_command: str) -> None:
    params = StdioServerParameters(
        command=python_command,
        args=["-m", "oriexa_mcp.server"],
        env={"ORIEXA_API_BASE_URL": f"{base_url.rstrip('/')}/api/v1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            if "browse_tasks" not in tool_names:
                raise RuntimeError("Stdio MCP missing browse_tasks")

            result = await session.call_tool(
                "browse_tasks",
                {"api_key": api_key, "status": "open", "limit": 1},
            )
            if not result.content:
                raise RuntimeError("Stdio MCP returned empty tool result")

            print(f"[OK] stdio tools={len(tool_names)} result_items={len(result.content)}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Oriexa MCP transports")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Oriexa API base URL without /api/v1 suffix (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--mcp-url",
        default=None,
        help="Explicit MCP URL override (default: <base-url>/mcp)",
    )
    parser.add_argument(
        "--python-command",
        default=sys.executable,
        help="Python executable to use for stdio MCP (default: current interpreter)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("ORIEXA_API_KEY"),
        help="Pre-provisioned th_agent_* key (defaults to ORIEXA_API_KEY env var)",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    mcp_url = args.mcp_url or f"{base_url}/mcp"
    api_key = args.api_key

    if not api_key:
        raise RuntimeError("Missing API key. Pass --api-key or set ORIEXA_API_KEY.")

    print(f"[INFO] base_url={base_url}")
    print(f"[INFO] mcp_url={mcp_url}")
    print("[INFO] using pre-provisioned API key")

    await smoke_http(mcp_url, api_key)
    await smoke_stdio(base_url, api_key, args.python_command)
    print("[OK] MCP transport smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
