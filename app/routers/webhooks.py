"""All /api/v1/webhooks/* endpoints — port of Oriexa/src/app/api/v1/webhooks/"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.envelope import success_response
from app.api.errors import (
    invalid_parameter_error,
    max_webhooks_error,
    validation_error,
    webhook_forbidden_error,
    webhook_not_found_error,
)
from app.auth.dependencies import get_current_agent
from app.constants import MAX_WEBHOOKS_PER_AGENT
from app.db.engine import get_db
from app.db.models import Webhook
from app.middleware.pipeline import AgentContext
from app.middleware.rate_limit import add_rate_limit_headers
from app.schemas.webhooks import CreateWebhookRequest
from app.services.webhooks import generate_webhook_secret

router = APIRouter()


def _isoformat(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ─── POST /api/v1/webhooks — Create webhook ──────────────────────────────────

@router.post("")
async def create_webhook(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            'Send { "url": "https://...", "events": ["task.new_match"] }',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        data = CreateWebhookRequest(**body)
    except Exception as e:
        resp = validation_error(str(e), "Check field requirements and try again")
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Check max webhooks per agent
    count_result = await session.execute(
        select(func.count()).select_from(Webhook).where(Webhook.agent_id == agent.id)
    )
    webhook_count = count_result.scalar() or 0

    if webhook_count >= MAX_WEBHOOKS_PER_AGENT:
        resp = max_webhooks_error()
        return add_rate_limit_headers(resp, agent.rate_limit)

    secret_info = generate_webhook_secret()

    webhook = Webhook(
        agent_id=agent.id,
        url=data.url,
        secret=secret_info["raw_secret"],
        events=list(data.events),
        is_active=True,
    )
    session.add(webhook)
    await session.flush()
    await session.commit()
    await session.refresh(webhook)

    resp = success_response(
        {
            "id": webhook.id,
            "url": webhook.url,
            "events": webhook.events,
            "is_active": webhook.is_active,
            "secret": secret_info["raw_secret"],
            "secret_prefix": secret_info["prefix"],
            "created_at": _isoformat(webhook.created_at),
            "warning": "Store this secret securely — it will not be shown again. Use it to verify webhook signatures via HMAC-SHA256.",
        },
        201,
    )
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/webhooks — List webhooks ────────────────────────────────────

@router.get("")
async def list_webhooks(
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(
            Webhook.id, Webhook.url, Webhook.events,
            Webhook.is_active, Webhook.secret, Webhook.created_at,
        ).where(Webhook.agent_id == agent.id)
    )
    rows = result.all()

    data = [
        {
            "id": row.id,
            "url": row.url,
            "events": row.events,
            "is_active": row.is_active,
            "secret_prefix": row.secret[:8],
            "created_at": _isoformat(row.created_at),
        }
        for row in rows
    ]

    resp = success_response(data)
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── DELETE /api/v1/webhooks/{webhook_id} — Delete webhook ───────────────────

@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if webhook_id < 1:
        resp = invalid_parameter_error(
            "Invalid webhook ID",
            "Webhook IDs are positive integers.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    result = await session.execute(
        select(Webhook.id, Webhook.agent_id).where(Webhook.id == webhook_id).limit(1)
    )
    webhook = result.first()

    if not webhook:
        resp = webhook_not_found_error(webhook_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if webhook.agent_id != agent.id:
        resp = webhook_forbidden_error()
        return add_rate_limit_headers(resp, agent.rate_limit)

    await session.execute(delete(Webhook).where(Webhook.id == webhook_id))
    await session.commit()

    resp = success_response({"id": webhook_id, "deleted": True})
    return add_rate_limit_headers(resp, agent.rate_limit)
