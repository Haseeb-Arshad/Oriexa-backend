"""Platform tools — agent registry access and specialist delegation."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import tool

from app.oriexa_client.client import OriexaClient

logger = logging.getLogger(__name__)

_client: OriexaClient | None = None


def _get_client() -> OriexaClient:
    """Lazy-initialise a shared OriexaClient singleton."""
    global _client
    if _client is None:
        _client = OriexaClient()
    return _client


@tool
async def list_available_agents(
    capabilities_filter: Annotated[
        str | None,
        "Optional comma-separated capabilities to filter by (e.g. 'python,api,testing')",
    ] = None,
) -> dict[str, Any]:
    """List agents registered on the Oriexa platform with their capabilities and reputation.

    Use this during planning to understand what specialist agents are available
    on the platform, their track records, and what they can do.

    Returns {ok: True, agents: [{id, name, capabilities, reputation_score, tasks_completed, avg_rating}]}.
    """
    client = _get_client()

    result = await client._request("GET", "/agents")
    if result is None:
        return {"ok": False, "error": "Failed to fetch agents from platform."}

    agents: list[dict[str, Any]] = result if isinstance(result, list) else []

    # Filter by capabilities if requested
    if capabilities_filter:
        filter_caps = {c.strip().lower() for c in capabilities_filter.split(",") if c.strip()}
        filtered = []
        for a in agents:
            agent_caps = {c.lower() for c in (a.get("capabilities") or [])}
            if filter_caps & agent_caps:
                filtered.append(a)
        agents = filtered

    # Return a condensed view
    return {
        "ok": True,
        "agents": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "capabilities": a.get("capabilities", []),
                "reputation_score": a.get("reputation_score"),
                "tasks_completed": a.get("tasks_completed", 0),
                "avg_rating": a.get("avg_rating"),
            }
            for a in agents
        ],
    }


@tool
async def consult_specialist(
    specialist_type: Annotated[
        str,
        "Type of specialist: 'research', 'code_review', or 'test_generation'",
    ],
    query: Annotated[str, "The question or task for the specialist"],
    context: Annotated[str, "Relevant context (code snippets, file contents, requirements)"],
    workspace_path: Annotated[
        str | None,
        "Optional workspace path if the specialist needs file access",
    ] = None,
) -> dict[str, Any]:
    """Delegate a sub-problem to a lightweight specialist agent in-process.

    Available specialists:
    - research: Analyses codebase patterns, finds relevant files, suggests approaches
    - code_review: Reviews code for quality, security, and correctness
    - test_generation: Suggests test cases and test strategies

    The specialist runs a quick LLM call with appropriate context and returns analysis.
    This does NOT create a platform task — it runs in your current process.

    Returns {ok: True, specialist_type, analysis: "..."}.
    """
    valid_types = ("research", "code_review", "test_generation")
    if specialist_type not in valid_types:
        return {"ok": False, "error": f"Invalid specialist_type: {specialist_type}. Use one of: {', '.join(valid_types)}"}

    if not query.strip():
        return {"ok": False, "error": "Query cannot be empty."}

    logger.info("consult_specialist: type=%s query_length=%d", specialist_type, len(query))

    try:
        from app.agents.research import ResearchAgent

        agent = ResearchAgent(specialist_type=specialist_type)
        result = await agent.run({
            "query": query,
            "context": context,
            "workspace_path": workspace_path,
        })

        return {
            "ok": True,
            "specialist_type": specialist_type,
            "analysis": result.get("analysis", "No analysis produced."),
        }
    except Exception as exc:
        logger.error("consult_specialist failed: %s", exc)
        return {"ok": False, "error": f"Specialist consultation failed: {exc}"}
