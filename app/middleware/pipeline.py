"""AgentContext dataclass holding authenticated agent info + rate limit result."""

from dataclasses import dataclass

from app.middleware.rate_limit import RateLimitResult


@dataclass
class AgentContext:
    id: int
    operator_id: int
    name: str
    status: str
    rate_limit: RateLimitResult
