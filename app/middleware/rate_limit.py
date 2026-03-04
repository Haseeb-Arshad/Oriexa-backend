"""In-memory rate limiter — 100 requests/minute per API key hash."""

import time
from dataclasses import dataclass

from fastapi.responses import JSONResponse

from app.constants import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW_MS


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # unix timestamp in seconds


_store: dict[str, dict] = {}  # key_hash -> {"count": int, "reset_at_ms": int}


def check_rate_limit(key: str) -> RateLimitResult:
    now_ms = int(time.time() * 1000)
    entry = _store.get(key)

    if entry is None or now_ms > entry["reset_at_ms"]:
        entry = {"count": 0, "reset_at_ms": now_ms + RATE_LIMIT_WINDOW_MS}
        _store[key] = entry

    entry["count"] += 1
    remaining = max(0, RATE_LIMIT_MAX - entry["count"])
    reset_at_seconds = -(-entry["reset_at_ms"] // 1000)  # ceil division

    return RateLimitResult(
        allowed=entry["count"] <= RATE_LIMIT_MAX,
        limit=RATE_LIMIT_MAX,
        remaining=remaining,
        reset_at=reset_at_seconds,
    )


def add_rate_limit_headers(response: JSONResponse, result: RateLimitResult) -> JSONResponse:
    response.headers["X-RateLimit-Limit"] = str(result.limit)
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset_at)
    return response


def cleanup_expired() -> None:
    """Remove expired windows. Called periodically."""
    now_ms = int(time.time() * 1000)
    expired = [k for k, v in _store.items() if now_ms > v["reset_at_ms"]]
    for k in expired:
        del _store[k]


def reset_store() -> None:
    """For testing only."""
    _store.clear()
