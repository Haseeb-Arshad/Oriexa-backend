"""Idempotency-Key handling — port of Oriexa/src/lib/api/idempotency.ts"""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from fastapi.responses import JSONResponse
from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import (
    idempotency_key_in_flight_error,
    idempotency_key_mismatch_error,
    idempotency_key_too_long_error,
)
from app.constants import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    IDEMPOTENCY_KEY_TTL_MS,
    IDEMPOTENCY_LOCK_TIMEOUT_MS,
)
from app.db.models import IdempotencyKey


@dataclass
class IdempotencyReplay:
    action: Literal["replay"] = "replay"
    response: JSONResponse | None = None


@dataclass
class IdempotencyProceed:
    action: Literal["proceed"] = "proceed"
    record_id: int = 0


@dataclass
class IdempotencyError:
    action: Literal["error"] = "error"
    response: JSONResponse | None = None


IdempotencyResult = IdempotencyReplay | IdempotencyProceed | IdempotencyError


def _hash_body(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


async def check_idempotency(
    session: AsyncSession,
    agent_id: int,
    key: str,
    path: str,
    body: str,
) -> IdempotencyResult:
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        return IdempotencyError(response=idempotency_key_too_long_error())

    body_hash = _hash_body(body)
    expires_at = datetime.fromtimestamp(
        (time.time() * 1000 + IDEMPOTENCY_KEY_TTL_MS) / 1000, tz=timezone.utc
    )

    # Clean up expired keys opportunistically (non-blocking best-effort)
    try:
        await session.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.expires_at < datetime.now(timezone.utc)
            )
        )
    except Exception:
        pass

    # Find existing record
    result = await session.execute(
        select(IdempotencyKey).where(
            and_(
                IdempotencyKey.agent_id == agent_id,
                IdempotencyKey.idempotency_key == key,
            )
        ).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Validate path + body match
        if existing.request_path != path or existing.request_body_hash != body_hash:
            return IdempotencyError(response=idempotency_key_mismatch_error())

        # If completed, replay the cached response
        if existing.completed_at is not None and existing.response_body is not None:
            cached_body = json.loads(existing.response_body)
            response = JSONResponse(
                content=cached_body,
                status_code=existing.response_status or 200,
            )
            response.headers["X-Idempotency-Replayed"] = "true"
            return IdempotencyReplay(response=response)

        # If locked but not completed, check if lock is stale
        lock_age_ms = (time.time() - existing.locked_at.timestamp()) * 1000
        if lock_age_ms < IDEMPOTENCY_LOCK_TIMEOUT_MS:
            return IdempotencyError(response=idempotency_key_in_flight_error())

        # Stale lock — reclaim it
        await session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == existing.id)
            .values(locked_at=datetime.now(timezone.utc))
        )
        await session.flush()
        return IdempotencyProceed(record_id=existing.id)

    # No existing record — insert a new lock
    new_record = IdempotencyKey(
        agent_id=agent_id,
        idempotency_key=key,
        request_path=path,
        request_body_hash=body_hash,
        expires_at=expires_at,
    )
    session.add(new_record)
    await session.flush()
    return IdempotencyProceed(record_id=new_record.id)


async def complete_idempotency(
    session: AsyncSession,
    record_id: int,
    response: JSONResponse,
) -> None:
    body = response.body.decode("utf-8")
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.id == record_id)
        .values(
            response_status=response.status_code,
            response_body=body,
            completed_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


async def fail_idempotency(session: AsyncSession, record_id: int) -> None:
    await session.execute(
        delete(IdempotencyKey).where(IdempotencyKey.id == record_id)
    )
    await session.flush()
