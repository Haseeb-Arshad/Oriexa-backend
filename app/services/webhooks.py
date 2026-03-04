"""Webhook dispatch service — port of Oriexa/src/lib/webhooks/dispatch.ts"""

import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import WEBHOOK_DELIVERY_TIMEOUT_MS
from app.db.engine import async_session
from app.db.models import Agent, Webhook, WebhookDelivery


def generate_webhook_secret() -> dict[str, str]:
    """Generate 32 random bytes → 64 hex chars."""
    raw_secret = os.urandom(32).hex()
    prefix = raw_secret[:8]
    return {"raw_secret": raw_secret, "prefix": prefix}


def sign_payload(secret: str, body: str) -> str:
    """HMAC-SHA256 sign a payload string. Returns 'sha256=<hex>'."""
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def build_payload(event: str, data: dict) -> str:
    return json.dumps({
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": data,
    })


async def _deliver_webhook(
    webhook: dict,
    event: str,
    payload: str,
) -> None:
    signature = sign_payload(webhook["secret"], payload)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    start = time.time()

    response_status = None
    response_body = None
    success = False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                webhook["url"],
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Oriexa-Signature": signature,
                    "X-Oriexa-Event": event,
                    "X-Oriexa-Timestamp": timestamp,
                },
                timeout=WEBHOOK_DELIVERY_TIMEOUT_MS / 1000,
            )
            response_status = resp.status_code
            response_body = resp.text[:1000]
            success = resp.is_success
    except Exception:
        pass

    duration_ms = int((time.time() - start) * 1000)

    # Log delivery
    async with async_session() as session:
        delivery = WebhookDelivery(
            webhook_id=webhook["id"],
            event=event,
            payload=payload,
            response_status=response_status,
            response_body=response_body,
            success=success,
            duration_ms=duration_ms,
        )
        session.add(delivery)
        await session.commit()


def dispatch_webhook_event(
    agent_id: int,
    event: str,
    data: dict,
) -> None:
    """Fire-and-forget webhook dispatch to all matching agent webhooks."""
    payload = build_payload(event, data)

    async def run():
        async with async_session() as session:
            result = await session.execute(
                select(Webhook.id, Webhook.url, Webhook.secret).where(
                    and_(
                        Webhook.agent_id == agent_id,
                        Webhook.is_active.is_(True),
                        Webhook.events.any(event),
                    )
                )
            )
            webhooks = [{"id": r.id, "url": r.url, "secret": r.secret} for r in result.all()]

        await asyncio.gather(
            *[_deliver_webhook(wh, event, payload) for wh in webhooks],
            return_exceptions=True,
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(run())
    except RuntimeError:
        pass


def dispatch_new_task_match(
    task_id: int,
    category_id: int | None,
    task_data: dict,
) -> None:
    """Dispatch task.new_match to all agents whose category_ids overlap."""
    if not category_id:
        return

    event = "task.new_match"
    payload = build_payload(event, task_data)

    async def run():
        async with async_session() as session:
            from sqlalchemy import text

            result = await session.execute(
                select(Webhook.id, Webhook.url, Webhook.secret)
                .join(Agent, Webhook.agent_id == Agent.id)
                .where(
                    and_(
                        Webhook.is_active.is_(True),
                        Webhook.events.any(event),
                        Agent.category_ids.any(category_id),
                    )
                )
            )
            webhooks = [{"id": r.id, "url": r.url, "secret": r.secret} for r in result.all()]

        await asyncio.gather(
            *[_deliver_webhook(wh, event, payload) for wh in webhooks],
            return_exceptions=True,
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(run())
    except RuntimeError:
        pass
