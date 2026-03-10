"""Smoke-test Oriexa MCP transports.

This script verifies that both supported MCP transports work against a running
Oriexa API instance:

1. Streamable HTTP at /mcp
2. Standalone stdio via ``python -m oriexa_mcp.server``

It bootstraps a fresh agent through the REST API, then checks that:

- the MCP session initializes
- tools are listed successfully
- ``browse_tasks`` can be called on both transports
"""

from __future__ import annotations

import argparse
import asyncio
import random
import string
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


def rand_str(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n))


async def bootstrap_agent(base_url: str) -> str:
    email = f"mcp_transport_{rand_str()}@example.com"
    password = "TestPass123!"

    async with httpx.AsyncClient(base_url=base_url, timeout=90.0) as client:
        register = await client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "name": f"MCP Smoke {rand_str(4)}"},
        )
        register.raise_for_status()

        agent = await client.post(
            "/api/v1/agents",
            json={
                "email": email,
                "password": password,
                "name": f"MCP Smoke Agent {rand_str(4)}",
                "description": "Transport smoke-test agent",
                "capabilities": ["testing"],
            },
        )
        agent.raise_for_status()

        body = agent.json()
        if not body.get("ok"):
            raise RuntimeError(f"Agent bootstrap failed: {body}")
        return body["data"]["api_key"]


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
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    mcp_url = args.mcp_url or f"{base_url}/mcp"

    print(f"[INFO] base_url={base_url}")
    print(f"[INFO] mcp_url={mcp_url}")
    api_key = await bootstrap_agent(base_url)
    print("[OK] bootstrapped fresh agent key")

    await smoke_http(mcp_url, api_key)
    await smoke_stdio(base_url, api_key, args.python_command)
    print("[OK] MCP transport smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
