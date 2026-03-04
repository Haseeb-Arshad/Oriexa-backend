"""FastAPI dependency for agent authentication with rate limiting.

Middleware chain order (must match TypeScript exactly):
  Request → Extract Bearer token → Validate format → SHA-256 hash →
    Rate limit check (BEFORE DB auth) →
      If exceeded: 429 WITH rate limit headers
    DB auth query →
      If auth fail: 401/403 WITHOUT rate limit headers
    Return AgentContext
"""

import time

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import (
    agent_paused_error,
    agent_suspended_error,
    invalid_api_key_error,
    rate_limited_error,
    unauthorized_error,
)
from app.auth.api_key import hash_api_key, is_valid_api_key_format
from app.db.engine import get_db
from app.db.models import Agent
from app.middleware.pipeline import AgentContext
from app.middleware.rate_limit import RateLimitResult, add_rate_limit_headers, check_rate_limit

# Short-lived in-memory cache for API key → agent (5s TTL)
_auth_cache: dict[str, dict] = {}  # key_hash -> {"agent": AgentContext-like, "expires_at": float}
_AUTH_CACHE_TTL = 5.0  # seconds


class AuthResponse(Exception):
    """Raised when auth middleware needs to return an error response directly."""

    def __init__(self, response: JSONResponse):
        self.response = response


async def get_current_agent(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> AgentContext:
    # Extract Bearer token
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
    valid_format = is_valid_api_key_format(token)
    key_hash = hash_api_key(token) if valid_format else ""

    # Rate limit check BEFORE DB auth (counter incremented synchronously)
    rl_result: RateLimitResult | None = None
    if valid_format:
        rl_result = check_rate_limit(key_hash)
        if not rl_result.allowed:
            retry_after = max(1, int((rl_result.reset_at * 1000 - time.time() * 1000) / 1000 + 0.999))
            resp = rate_limited_error(retry_after)
            add_rate_limit_headers(resp, rl_result)
            raise AuthResponse(resp)

    # Validate auth header presence and format
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthResponse(unauthorized_error())

    if not valid_format:
        raise AuthResponse(unauthorized_error("Invalid API key format"))

    # Check cache
    cached = _auth_cache.get(key_hash)
    if cached and time.time() < cached["expires_at"]:
        ctx = cached["agent"]
        return AgentContext(
            id=ctx["id"],
            operator_id=ctx["operator_id"],
            name=ctx["name"],
            status=ctx["status"],
            rate_limit=rl_result or check_rate_limit(key_hash),
        )

    # DB auth query
    result = await session.execute(
        select(Agent.id, Agent.operator_id, Agent.name, Agent.status).where(
            Agent.api_key_hash == key_hash
        ).limit(1)
    )
    agent = result.first()

    if not agent:
        raise AuthResponse(invalid_api_key_error())

    if agent.status == "suspended":
        raise AuthResponse(agent_suspended_error())

    if agent.status == "paused":
        raise AuthResponse(agent_paused_error())

    # Cache active agents only
    _auth_cache[key_hash] = {
        "agent": {
            "id": agent.id,
            "operator_id": agent.operator_id,
            "name": agent.name,
            "status": agent.status,
        },
        "expires_at": time.time() + _AUTH_CACHE_TTL,
    }

    return AgentContext(
        id=agent.id,
        operator_id=agent.operator_id,
        name=agent.name,
        status=agent.status,
        rate_limit=rl_result or check_rate_limit(key_hash),
    )


def clear_auth_cache() -> None:
    """For testing only."""
    _auth_cache.clear()
