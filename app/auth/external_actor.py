from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.envelope import error_response
from app.api.errors import rate_limited_error
from app.auth.dependencies import AuthResponse
from app.config import settings
from app.constants import EXTERNAL_TOKEN_PREFIX, EXTERNAL_TOKEN_TTL_DAYS
from app.db.engine import get_db
from app.db.models import Agent, User
from app.middleware.rate_limit import RateLimitResult, add_rate_limit_headers, check_rate_limit

ExternalScope = Literal["poster", "worker", "hybrid"]


@dataclass(frozen=True)
class ExternalActorContext:
    user_id: int
    agent_id: int
    operator_id: int
    scope: ExternalScope
    token: str
    user_name: str
    user_email: str
    agent_name: str
    rate_limit: RateLimitResult

    @property
    def can_post(self) -> bool:
        return self.scope in ("poster", "hybrid")

    @property
    def can_work(self) -> bool:
        return self.scope in ("worker", "hybrid")

    @property
    def allowed_actions(self) -> list[str]:
        return allowed_actions_for_scope(self.scope)


def allowed_actions_for_scope(scope: ExternalScope) -> list[str]:
    actions = [
        "list_tasks",
        "get_task",
        "get_task_state",
        "stream_events",
        "register_webhook",
        "list_webhooks",
        "delete_webhook",
    ]
    if scope in ("poster", "hybrid"):
        actions.extend([
            "create_task",
            "accept_claim",
            "request_revision",
            "accept_deliverable",
            "send_message",
            "answer_question",
        ])
    if scope in ("worker", "hybrid"):
        actions.extend([
            "claim_task",
            "submit_deliverable",
            "send_message",
        ])
    return sorted(set(actions))


def _token_secret() -> str:
    return settings.EXTERNAL_TOKEN_SECRET or settings.NEXTAUTH_SECRET


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_external_token(
    *,
    user_id: int,
    agent_id: int,
    scope: ExternalScope,
    ttl_days: int = EXTERNAL_TOKEN_TTL_DAYS,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "ver": 2,
        "kind": "external_actor",
        "scope": scope,
        "user_id": user_id,
        "agent_id": agent_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=ttl_days)).timestamp()),
    }
    encoded = jwt.encode(payload, _token_secret(), algorithm="HS256")
    return f"{EXTERNAL_TOKEN_PREFIX}{encoded}"


def decode_external_token(raw_token: str) -> dict:
    if not raw_token.startswith(EXTERNAL_TOKEN_PREFIX):
        raise ValueError("Missing th_ext_ prefix")
    token = raw_token[len(EXTERNAL_TOKEN_PREFIX):]
    try:
        payload = jwt.decode(token, _token_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:  # pragma: no cover - library error mapping
        raise ValueError("Invalid or expired external token") from exc

    if payload.get("kind") != "external_actor":
        raise ValueError("Unsupported external token kind")
    scope = payload.get("scope")
    if scope not in ("poster", "worker", "hybrid"):
        raise ValueError("Unsupported external token scope")
    user_id = payload.get("user_id")
    agent_id = payload.get("agent_id")
    if not isinstance(user_id, int) or user_id < 1:
        raise ValueError("Missing user_id in external token")
    if not isinstance(agent_id, int) or agent_id < 1:
        raise ValueError("Missing agent_id in external token")
    return payload


def _auth_error(message: str, suggestion: str, status_code: int = 401) -> AuthResponse:
    return AuthResponse(error_response(status_code, "UNAUTHORIZED", message, suggestion))


async def get_external_actor(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> ExternalActorContext:
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    if not auth_header or not auth_header.startswith("Bearer "):
        raise _auth_error(
            "Missing or invalid Authorization header",
            "Include header: Authorization: Bearer th_ext_<automation-token>",
        )

    try:
        payload = decode_external_token(token)
    except ValueError as exc:
        raise _auth_error(
            str(exc),
            "Call POST /api/v2/external/sessions/bootstrap to obtain a fresh th_ext_ automation token.",
        )

    rate_limit = check_rate_limit(_hash_token(token))
    if not rate_limit.allowed:
        retry_after = max(
            1,
            int((rate_limit.reset_at * 1000 - time.time() * 1000) / 1000 + 0.999),
        )
        resp = rate_limited_error(retry_after)
        add_rate_limit_headers(resp, rate_limit)
        raise AuthResponse(resp)

    result = await session.execute(
        select(
            User.id.label("user_id"),
            User.name.label("user_name"),
            User.email.label("user_email"),
            Agent.id.label("agent_id"),
            Agent.operator_id.label("operator_id"),
            Agent.name.label("agent_name"),
            Agent.status.label("agent_status"),
        )
        .select_from(Agent)
        .join(User, Agent.operator_id == User.id)
        .where(
            Agent.id == int(payload["agent_id"]),
            User.id == int(payload["user_id"]),
        )
        .limit(1)
    )
    actor = result.first()
    if not actor:
        raise _auth_error(
            "External actor no longer exists",
            "Call POST /api/v2/external/sessions/bootstrap to mint a fresh automation token.",
        )

    if actor.agent_status != "active":
        raise _auth_error(
            f"External actor agent is {actor.agent_status}",
            "Reactivate the backing agent before using this automation token.",
            status_code=403,
        )

    return ExternalActorContext(
        user_id=actor.user_id,
        agent_id=actor.agent_id,
        operator_id=actor.operator_id,
        scope=payload["scope"],
        token=token,
        user_name=actor.user_name,
        user_email=actor.user_email,
        agent_name=actor.agent_name,
        rate_limit=rate_limit,
    )
