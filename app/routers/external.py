from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.envelope import error_response, success_response
from app.auth.external_actor import (
    ExternalActorContext,
    allowed_actions_for_scope,
    generate_external_token,
    get_external_actor,
)
from app.auth.password import hash_password, verify_password
from app.db.engine import get_db
from app.db.models import Agent, Task, User, Webhook
from app.middleware.rate_limit import add_rate_limit_headers
from app.schemas.claims import CreateClaimRequest
from app.schemas.deliverables import CreateDeliverableRequest
from app.schemas.tasks import CreateTaskRequest
from app.schemas.webhooks import CreateWebhookRequest
from app.services.credits import grant_welcome_bonus
from app.services.external_events import agent_channel, external_event_broadcaster, user_channel
from app.services.external_workflow import build_external_task_bundle
from app.services.marketplace import (
    MarketplaceError,
    accept_claim,
    accept_deliverable,
    answer_question,
    create_claim,
    create_task,
    get_task_with_access,
    list_task_ids_for_view,
    request_revision,
    send_message,
    submit_deliverable,
)
from app.services.webhooks import generate_webhook_secret

router = APIRouter(prefix="/api/v2/external", tags=["external-v2"])


class BootstrapActorRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    scope: Literal["poster", "worker", "hybrid"] = "hybrid"
    agent_name: str | None = Field(default=None, min_length=1, max_length=255)
    agent_description: str | None = Field(default=None, max_length=4000)
    capabilities: list[str] = Field(default_factory=list)
    category_ids: list[int] = Field(default_factory=list)


class SendExternalMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50000)
    message_type: str = "text"
    parent_id: int | None = None
    structured_data: dict | None = None


class AnswerQuestionRequest(BaseModel):
    response: str = Field(min_length=1, max_length=20000)
    option_index: int | None = None


class RevisionRequest(BaseModel):
    deliverable_id: int
    notes: str = ""


class AcceptClaimRequest(BaseModel):
    claim_id: int


class AcceptDeliverableRequest(BaseModel):
    deliverable_id: int


def _ok(
    actor: ExternalActorContext | None,
    data,
    *,
    status_code: int = 200,
    pagination: dict | None = None,
):
    response = success_response(data, status_code, pagination)
    if actor is not None:
        add_rate_limit_headers(response, actor.rate_limit)
    return response


def _fail(
    actor: ExternalActorContext | None,
    status_code: int,
    code: str,
    message: str,
    suggestion: str,
):
    response = error_response(status_code, code, message, suggestion)
    if actor is not None:
        add_rate_limit_headers(response, actor.rate_limit)
    return response


def _handle_marketplace_error(actor: ExternalActorContext, exc: MarketplaceError):
    return _fail(actor, exc.status_code, exc.code, exc.message, exc.suggestion)


def _forwarded_header(request: Request, name: str) -> str | None:
    raw = request.headers.get(name)
    if not raw:
        return None
    return raw.split(",")[0].strip() or None


def _base_origin(request: Request) -> str:
    forwarded_proto = _forwarded_header(request, "x-forwarded-proto") or request.url.scheme
    forwarded_host = _forwarded_header(request, "x-forwarded-host")

    if not forwarded_host:
        host = _forwarded_header(request, "host") or request.url.netloc
        port = _forwarded_header(request, "x-forwarded-port")
        default_port = "443" if forwarded_proto == "https" else "80"
        if host and port and ":" not in host and port != default_port:
            host = f"{host}:{port}"
        forwarded_host = host

    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    return str(request.base_url).rstrip("/")


async def _get_poster_name(session: AsyncSession, user_id: int) -> str:
    result = await session.execute(select(User.name).where(User.id == user_id).limit(1))
    return result.scalar_one_or_none() or "Poster"


def _bootstrap_next_step(scope: str) -> str:
    if scope == "poster":
        return "create_task"
    if scope == "worker":
        return "list_tasks(view='marketplace')"
    return "create_task or list_tasks(view='marketplace')"


@router.post("/sessions/bootstrap")
async def bootstrap_actor(
    payload: BootstrapActorRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(select(User).where(User.email == payload.email).limit(1))
    user = result.scalar_one_or_none()
    created_user = False

    if user is None:
        user = User(
            email=payload.email,
            password_hash=hash_password(payload.password),
            name=payload.name or payload.email.split("@")[0],
            role="both" if payload.scope == "hybrid" else "poster" if payload.scope == "poster" else "operator",
            credit_balance=0,
        )
        session.add(user)
        await session.flush()
        await grant_welcome_bonus(session, user.id)
        created_user = True
    else:
        if user.password_hash:
            if not verify_password(payload.password, user.password_hash):
                return _fail(
                    None,
                    401,
                    "INVALID_CREDENTIALS",
                    "Email or password is incorrect.",
                    "Use the same password you registered with or bootstrap with a new email.",
                )
        else:
            user.password_hash = hash_password(payload.password)

        if payload.name:
            user.name = payload.name
        if payload.scope == "hybrid":
            user.role = "both"
        elif payload.scope == "poster" and user.role == "operator":
            user.role = "both"
        elif payload.scope == "worker" and user.role == "poster":
            user.role = "both"

    agent_result = await session.execute(
        select(Agent).where(Agent.operator_id == user.id).order_by(Agent.id.asc()).limit(1)
    )
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        agent = Agent(
            operator_id=user.id,
            name=payload.agent_name or f"{user.name} External Agent",
            description=payload.agent_description or "External automation actor for Oriexa v2.",
            capabilities=payload.capabilities or ["general"],
            category_ids=payload.category_ids or [],
            status="active",
        )
        session.add(agent)
        await session.flush()
    else:
        if payload.agent_name:
            agent.name = payload.agent_name
        if payload.agent_description:
            agent.description = payload.agent_description
        if payload.capabilities:
            agent.capabilities = payload.capabilities
        if payload.category_ids:
            agent.category_ids = payload.category_ids
        if agent.status != "active":
            agent.status = "active"

    await session.commit()
    await session.refresh(user)
    await session.refresh(agent)

    token = generate_external_token(
        user_id=user.id,
        agent_id=agent.id,
        scope=payload.scope,
    )
    origin = _base_origin(request)
    return _ok(
        None,
        {
            "token": token,
            "token_type": "Bearer",
            "token_prefix": "th_ext_",
            "created_user": created_user,
            "actor": {
                "scope": payload.scope,
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "role": user.role,
                    "credit_balance": user.credit_balance,
                },
                "agent": {
                    "id": agent.id,
                    "name": agent.name,
                    "description": agent.description,
                    "status": agent.status,
                    "capabilities": agent.capabilities or [],
                    "category_ids": agent.category_ids or [],
                },
            },
            "allowed_actions": allowed_actions_for_scope(payload.scope),
            "recommended_next_step": _bootstrap_next_step(payload.scope),
            "discovery": {
                "rest_base_url": f"{origin}/api/v2/external",
                "events_stream_url": f"{origin}/api/v2/external/events/stream",
                "mcp_http_url": f"{origin}/mcp/v2",
                "legacy_mcp_http_url": f"{origin}/mcp",
            },
        },
        status_code=201,
    )


@router.get("/tasks")
async def list_tasks(
    request: Request,
    view: Literal["mine", "marketplace", "claimed", "inbox"] | None = Query(default=None),
    status: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    resolved_view = view or ("marketplace" if actor.can_work and not actor.can_post else "mine")
    if resolved_view == "mine" and not actor.can_post:
        return _fail(actor, 403, "POSTER_SCOPE_REQUIRED", "This view requires poster scope.", "Bootstrap with scope='poster' or scope='hybrid'.")
    if resolved_view in {"marketplace", "claimed"} and not actor.can_work:
        return _fail(actor, 403, "WORKER_SCOPE_REQUIRED", "This view requires worker scope.", "Bootstrap with scope='worker' or scope='hybrid'.")

    try:
        task_ids, next_cursor, has_more = await list_task_ids_for_view(
            session,
            user_id=actor.user_id,
            agent_id=actor.agent_id,
            view=resolved_view,
            status=status,
            cursor=cursor,
            limit=limit,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    items = []
    for task_id in task_ids:
        bundle = await build_external_task_bundle(
            session,
            task_id,
            viewer_user_id=actor.user_id,
            viewer_agent_id=actor.agent_id,
            include_claims=False,
            include_deliverables=False,
            include_messages=False,
            include_activity=False,
        )
        if bundle is not None:
            items.append(bundle)

    return _ok(
        actor,
        {
            "view": resolved_view,
            "items": items,
        },
        pagination={"cursor": next_cursor, "has_more": has_more, "count": len(items)},
    )


@router.post("/tasks")
async def external_create_task(
    payload: CreateTaskRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    if not actor.can_post:
        return _fail(actor, 403, "POSTER_SCOPE_REQUIRED", "Creating tasks requires poster scope.", "Bootstrap with scope='poster' or scope='hybrid'.")

    try:
        result = await create_task(
            session,
            poster_id=actor.user_id,
            title=payload.title,
            description=payload.description,
            budget_credits=payload.budget_credits,
            category_id=payload.category_id,
            requirements=payload.requirements,
            deadline=payload.deadline,
            max_revisions=payload.max_revisions,
            auto_review_enabled=payload.auto_review_enabled,
            poster_llm_key=payload.poster_llm_key,
            poster_llm_provider=payload.poster_llm_provider,
            poster_max_reviews=payload.poster_max_reviews,
            poster_notify_agent_id=actor.agent_id,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle, status_code=201)


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: int,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    try:
        await get_task_with_access(
            session,
            task_id=task_id,
            poster_id=actor.user_id if actor.can_post else None,
            worker_agent_id=actor.agent_id if actor.can_work else None,
            allow_open_marketplace=actor.can_work,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        task_id,
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    if bundle is None:
        return _fail(actor, 404, "TASK_NOT_FOUND", f"Task {task_id} was not found.", "List tasks first and use a valid task_id.")
    return _ok(actor, bundle)


@router.get("/tasks/{task_id}/state")
async def get_task_state(
    task_id: int,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    try:
        await get_task_with_access(
            session,
            task_id=task_id,
            poster_id=actor.user_id if actor.can_post else None,
            worker_agent_id=actor.agent_id if actor.can_work else None,
            allow_open_marketplace=actor.can_work,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        task_id,
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
        include_claims=False,
        include_deliverables=False,
        include_messages=False,
        include_activity=False,
    )
    if bundle is None:
        return _fail(actor, 404, "TASK_NOT_FOUND", f"Task {task_id} was not found.", "List tasks first and use a valid task_id.")
    return _ok(
        actor,
        {
            "task_id": task_id,
            "status": bundle["status"],
            "workflow": bundle["workflow"],
        },
    )


@router.post("/tasks/{task_id}/claim")
async def claim_task(
    task_id: int,
    payload: CreateClaimRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    if not actor.can_work:
        return _fail(actor, 403, "WORKER_SCOPE_REQUIRED", "Claiming tasks requires worker scope.", "Bootstrap with scope='worker' or scope='hybrid'.")

    try:
        result = await create_claim(
            session,
            task_id=task_id,
            agent_id=actor.agent_id,
            agent_name=actor.agent_name,
            proposed_credits=payload.proposed_credits,
            message=payload.message,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle, status_code=201)


@router.post("/tasks/{task_id}/accept-claim")
async def external_accept_claim(
    task_id: int,
    payload: AcceptClaimRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    if not actor.can_post:
        return _fail(actor, 403, "POSTER_SCOPE_REQUIRED", "Accepting claims requires poster scope.", "Bootstrap with scope='poster' or scope='hybrid'.")

    try:
        result = await accept_claim(
            session,
            task_id=task_id,
            claim_id=payload.claim_id,
            poster_id=actor.user_id,
            poster_notify_agent_id=actor.agent_id,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle)


@router.post("/tasks/{task_id}/deliverables")
async def external_submit_deliverable(
    task_id: int,
    payload: CreateDeliverableRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    if not actor.can_work:
        return _fail(actor, 403, "WORKER_SCOPE_REQUIRED", "Submitting deliverables requires worker scope.", "Bootstrap with scope='worker' or scope='hybrid'.")

    try:
        result = await submit_deliverable(
            session,
            task_id=task_id,
            agent_id=actor.agent_id,
            content=payload.content,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle, status_code=201)


@router.post("/tasks/{task_id}/accept-deliverable")
async def external_accept_deliverable(
    task_id: int,
    payload: AcceptDeliverableRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    if not actor.can_post:
        return _fail(actor, 403, "POSTER_SCOPE_REQUIRED", "Accepting deliverables requires poster scope.", "Bootstrap with scope='poster' or scope='hybrid'.")

    try:
        result = await accept_deliverable(
            session,
            task_id=task_id,
            deliverable_id=payload.deliverable_id,
            poster_id=actor.user_id,
            poster_notify_agent_id=actor.agent_id,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle)


@router.post("/tasks/{task_id}/request-revision")
async def external_request_revision(
    task_id: int,
    payload: RevisionRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    if not actor.can_post:
        return _fail(actor, 403, "POSTER_SCOPE_REQUIRED", "Requesting revisions requires poster scope.", "Bootstrap with scope='poster' or scope='hybrid'.")

    try:
        result = await request_revision(
            session,
            task_id=task_id,
            deliverable_id=payload.deliverable_id,
            poster_id=actor.user_id,
            notes=payload.notes,
            poster_notify_agent_id=actor.agent_id,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle)


@router.post("/tasks/{task_id}/messages")
async def external_send_message(
    task_id: int,
    payload: SendExternalMessageRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    try:
        task = await get_task_with_access(
            session,
            task_id=task_id,
            poster_id=actor.user_id if actor.can_post else None,
            worker_agent_id=actor.agent_id if actor.can_work else None,
            allow_open_marketplace=False,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    sender_type = "poster" if actor.can_post and task.poster_id == actor.user_id else "agent"
    sender_id = actor.user_id if sender_type == "poster" else actor.agent_id
    sender_name = actor.user_name if sender_type == "poster" else actor.agent_name

    try:
        result = await send_message(
            session,
            task_id=task_id,
            sender_type=sender_type,
            sender_id=sender_id,
            sender_name=sender_name,
            content=payload.content,
            message_type=payload.message_type,
            parent_id=payload.parent_id,
            structured_data=payload.structured_data,
            notify_agent_id=actor.agent_id,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle, status_code=201)


@router.patch("/tasks/{task_id}/questions/{message_id}")
async def external_answer_question(
    task_id: int,
    message_id: int,
    payload: AnswerQuestionRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    if not actor.can_post:
        return _fail(actor, 403, "POSTER_SCOPE_REQUIRED", "Answering questions requires poster scope.", "Bootstrap with scope='poster' or scope='hybrid'.")

    try:
        result = await answer_question(
            session,
            task_id=task_id,
            message_id=message_id,
            poster_id=actor.user_id,
            poster_name=actor.user_name,
            response=payload.response,
            option_index=payload.option_index,
            poster_notify_agent_id=actor.agent_id,
        )
    except MarketplaceError as exc:
        return _handle_marketplace_error(actor, exc)

    bundle = await build_external_task_bundle(
        session,
        result["task_id"],
        viewer_user_id=actor.user_id,
        viewer_agent_id=actor.agent_id,
    )
    return _ok(actor, bundle)


@router.get("/events/stream")
async def stream_events(actor: ExternalActorContext = Depends(get_external_actor)):
    channels = [user_channel(actor.user_id), agent_channel(actor.agent_id)]
    queue = external_event_broadcaster.subscribe(channels)

    async def event_generator():
        try:
            yield f"event: connected\ndata: {json.dumps({'channels': channels})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if event is None:
                        break
                    yield f"event: {event.event_type}\ndata: {json.dumps(event.data)}\n\n"
                except asyncio.TimeoutError:
                    yield f": heartbeat {int(time.time())}\n\n"
        finally:
            external_event_broadcaster.unsubscribe(channels, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/webhooks")
async def create_external_webhook(
    payload: CreateWebhookRequest,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    count_result = await session.execute(
        select(func.count()).select_from(Webhook).where(Webhook.agent_id == actor.agent_id)
    )
    webhook_count = count_result.scalar() or 0
    if webhook_count >= 5:
        return _fail(actor, 409, "MAX_WEBHOOKS_REACHED", "This actor already has the maximum number of webhooks.", "Delete an unused webhook before creating another.")

    secret_info = generate_webhook_secret()
    webhook = Webhook(
        agent_id=actor.agent_id,
        url=payload.url,
        secret=secret_info["raw_secret"],
        events=list(payload.events),
        is_active=True,
    )
    session.add(webhook)
    await session.flush()
    await session.commit()
    await session.refresh(webhook)

    return _ok(
        actor,
        {
            "id": webhook.id,
            "url": webhook.url,
            "events": webhook.events,
            "is_active": webhook.is_active,
            "secret": secret_info["raw_secret"],
            "secret_prefix": secret_info["prefix"],
            "created_at": webhook.created_at.isoformat().replace("+00:00", "Z"),
        },
        status_code=201,
    )


@router.get("/webhooks")
async def list_external_webhooks(
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(Webhook).where(Webhook.agent_id == actor.agent_id).order_by(Webhook.id.asc())
    )
    rows = result.scalars().all()
    return _ok(
        actor,
        [
            {
                "id": row.id,
                "url": row.url,
                "events": row.events,
                "is_active": row.is_active,
                "secret_prefix": row.secret[:8],
                "created_at": row.created_at.isoformat().replace("+00:00", "Z"),
            }
            for row in rows
        ],
    )


@router.delete("/webhooks/{webhook_id}")
async def delete_external_webhook(
    webhook_id: int,
    actor: ExternalActorContext = Depends(get_external_actor),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.agent_id == actor.agent_id).limit(1)
    )
    webhook = result.scalar_one_or_none()
    if webhook is None:
        return _fail(actor, 404, "WEBHOOK_NOT_FOUND", f"Webhook {webhook_id} was not found.", "List your webhooks first and use a valid webhook_id.")

    await session.execute(delete(Webhook).where(Webhook.id == webhook_id))
    await session.commit()
    return _ok(actor, {"id": webhook_id, "deleted": True})
