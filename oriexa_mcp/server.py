"""Oriexa MCP Server — FastMCP instance, lifespan, and entry points."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from oriexa_mcp.client import OriexaClient
from oriexa_mcp.tools import (
    agent,
    claiming,
    delivery,
    discovery,
    execution,
    orchestrator,
    poster,
    review,
    webhooks,
)

# Shared client — initialized in lifespan, used by all tools via closure
_client = OriexaClient()


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Initialize the shared httpx client on startup, close on shutdown."""
    await _client.start()
    try:
        yield {}
    finally:
        await _client.close()


def create_mcp_server() -> FastMCP:
    """Build and return the configured FastMCP server instance."""
    mcp = FastMCP(
        "oriexa",
        instructions=(
            "Oriexa MCP Server — interact with the Oriexa freelancer marketplace. "
            "Browse tasks, submit claims, deliver work, manage agents, and monitor the orchestrator. "
            "All operations require a valid ORIEXA_API_KEY environment variable."
        ),
        lifespan=server_lifespan,
    )

    # Register all tool modules with the shared client
    discovery.register(mcp, _client)
    claiming.register(mcp, _client)
    execution.register(mcp, _client)
    delivery.register(mcp, _client)
    review.register(mcp, _client)
    agent.register(mcp, _client)
    poster.register(mcp, _client)
    webhooks.register(mcp, _client)
    orchestrator.register(mcp, _client)

    return mcp


# Global server instance
mcp = create_mcp_server()


def main() -> None:
    """CLI entry point — run with stdio or streamable-http transport."""
    transport = "stdio"
    if "--http" in sys.argv or "--streamable-http" in sys.argv:
        transport = "streamable-http"
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
