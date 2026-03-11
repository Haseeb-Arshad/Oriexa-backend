"""All /api/v1/agents/* endpoints — port of Oriexa/src/app/api/v1/agents/"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.envelope import success_response
from app.api.errors import (
    internal_error,
    invalid_parameter_error,
    not_found_error,
    validation_error,
)
from app.api.pagination import decode_cursor, encode_cursor
from app.auth.dependencies import get_current_agent
from app.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MIN_PAGE_SIZE
from app.db.engine import get_db
from app.db.models import Agent, Category, CreditTransaction, Task, TaskClaim, User
from app.middleware.pipeline import AgentContext
from app.middleware.rate_limit import add_rate_limit_headers
from app.schemas.agents import UpdateAgentRequest

router = APIRouter()


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


@router.get("/me")
async def get_my_profile(
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    result = await session.execute(
        select(
            Agent.id,
            Agent.name,
            Agent.description,
            Agent.capabilities,
            Agent.category_ids,
            Agent.hourly_rate_credits,
            Agent.api_key_prefix,
            Agent.webhook_url,
            Agent.status,
            Agent.reputation_score,
            Agent.tasks_completed,
            Agent.avg_rating,
            Agent.created_at,
            Agent.updated_at,
            Agent.operator_id,
            User.name.label("operator_name"),
            User.email.label("operator_email"),
            User.credit_balance.label("operator_credits"),
        )
        .select_from(Agent)
        .join(User, Agent.operator_id == User.id)
        .where(Agent.id == agent.id)
        .limit(1)
    )
    agent_data = result.first()

    if not agent_data:
        resp = internal_error()
        return add_rate_limit_headers(resp, agent.rate_limit)

    resp = success_response({
        "id": agent_data.id,
        "name": agent_data.name,
        "description": agent_data.description,
        "capabilities": agent_data.capabilities,
        "category_ids": agent_data.category_ids,
        "hourly_rate_credits": agent_data.hourly_rate_credits,
        "api_key_prefix": agent_data.api_key_prefix,
        "webhook_url": agent_data.webhook_url,
        "status": agent_data.status,
        "reputation_score": agent_data.reputation_score,
        "tasks_completed": agent_data.tasks_completed,
        "avg_rating": agent_data.avg_rating,
        "created_at": _isoformat(agent_data.created_at),
        "updated_at": _isoformat(agent_data.updated_at),
        "operator": {
            "id": agent_data.operator_id,
            "name": agent_data.operator_name,
            "credit_balance": agent_data.operator_credits,
        },
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── PATCH /api/v1/agents/me — Update profile ────────────────────────────────

@router.patch("/me")
async def update_my_profile(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            "Send a valid JSON object with fields to update",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        data = UpdateAgentRequest(**body)
    except Exception as e:
        resp = validation_error(
            str(e),
            "Valid fields: name (1-100 chars), description (max 2000), capabilities (array of strings, max 20), webhook_url (valid URL), hourly_rate_credits (non-negative integer)",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    updates: dict = {}
    if data.name is not None:
        updates["name"] = data.name
    if data.description is not None:
        updates["description"] = data.description
    if data.capabilities is not None:
        updates["capabilities"] = data.capabilities
    if data.hourly_rate_credits is not None:
        updates["hourly_rate_credits"] = data.hourly_rate_credits
    if data.webhook_url is not None:
        updates["webhook_url"] = data.webhook_url

    if not updates:
        resp = success_response({"message": "No fields to update"})
        return add_rate_limit_headers(resp, agent.rate_limit)

    updates["updated_at"] = datetime.now(timezone.utc)

    result = await session.execute(
        update(Agent).where(Agent.id == agent.id).values(**updates).returning(
            Agent.id, Agent.name, Agent.description, Agent.capabilities,
            Agent.hourly_rate_credits, Agent.webhook_url, Agent.status, Agent.updated_at,
        )
    )
    updated = result.first()
    await session.commit()

    resp = success_response({
        "id": updated.id,
        "name": updated.name,
        "description": updated.description,
        "capabilities": updated.capabilities,
        "hourly_rate_credits": updated.hourly_rate_credits,
        "webhook_url": updated.webhook_url,
        "status": updated.status,
        "updated_at": _isoformat(updated.updated_at),
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/agents/{agent_id} — Public profile ──────────────────────────

@router.get("/{agent_id}")
async def get_agent_profile(
    agent_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if agent_id < 1:
        resp = invalid_parameter_error(
            f"Invalid agent ID: {agent_id}",
            "Agent IDs are positive integers.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    result = await session.execute(
        select(
            Agent.id, Agent.name, Agent.description, Agent.capabilities,
            Agent.status, Agent.reputation_score, Agent.tasks_completed,
            Agent.avg_rating, Agent.created_at,
        ).where(Agent.id == agent_id).limit(1)
    )
    agent_data = result.first()

    if not agent_data:
        resp = not_found_error("Agent", agent_id, "Use a valid agent ID.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    resp = success_response({
        "id": agent_data.id,
        "name": agent_data.name,
        "description": agent_data.description,
        "capabilities": agent_data.capabilities,
        "status": agent_data.status,
        "reputation_score": agent_data.reputation_score,
        "tasks_completed": agent_data.tasks_completed,
        "avg_rating": agent_data.avg_rating,
        "created_at": _isoformat(agent_data.created_at),
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/agents/me/claims — Paginated claims ─────────────────────────

@router.get("/me/claims")
async def get_my_claims(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    params = dict(request.query_params)
    limit_str = params.get("limit")
    cursor_str = params.get("cursor")
    status_param = params.get("status")

    limit = DEFAULT_PAGE_SIZE
    if limit_str:
        try:
            limit = int(limit_str)
            if limit < MIN_PAGE_SIZE or limit > MAX_PAGE_SIZE:
                raise ValueError
        except ValueError:
            resp = invalid_parameter_error(
                f"limit must be between {MIN_PAGE_SIZE} and {MAX_PAGE_SIZE}",
                f"Use limit={DEFAULT_PAGE_SIZE} (default) or any value 1-{MAX_PAGE_SIZE}",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)

    valid_statuses = ["pending", "accepted", "rejected", "withdrawn"]
    if status_param and status_param not in valid_statuses:
        resp = invalid_parameter_error(
            f"Invalid status: {status_param}",
            f"Valid values: {', '.join(valid_statuses)}",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    conditions = [TaskClaim.agent_id == agent.id]
    if status_param:
        conditions.append(TaskClaim.status == status_param)

    if cursor_str:
        decoded = decode_cursor(cursor_str)
        if not decoded:
            resp = invalid_parameter_error(
                "Invalid cursor value",
                "Use the cursor value from a previous response's meta.cursor field",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)
        conditions.append(TaskClaim.id < decoded["id"])

    result = await session.execute(
        select(
            TaskClaim.id,
            TaskClaim.task_id,
            Task.title.label("task_title"),
            Task.status.label("task_status"),
            TaskClaim.proposed_credits,
            TaskClaim.message,
            TaskClaim.status,
            TaskClaim.created_at,
        )
        .select_from(TaskClaim)
        .join(Task, TaskClaim.task_id == Task.id)
        .where(and_(*conditions))
        .order_by(TaskClaim.id)
        .limit(limit + 1)
    )
    rows = result.all()

    has_more = len(rows) > limit
    page_rows = rows[:limit] if has_more else rows

    data = [
        {
            "id": c.id,
            "task_id": c.task_id,
            "task_title": c.task_title,
            "task_status": c.task_status,
            "proposed_credits": c.proposed_credits,
            "message": c.message,
            "status": c.status,
            "created_at": _isoformat(c.created_at),
        }
        for c in page_rows
    ]

    next_cursor = None
    if has_more and page_rows:
        next_cursor = encode_cursor(page_rows[-1].id)

    resp = success_response(data, 200, {
        "cursor": next_cursor,
        "has_more": has_more,
        "count": len(data),
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/agents/me/tasks — Paginated tasks claimed by agent ──────────

@router.get("/me/tasks")
async def get_my_tasks(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    params = dict(request.query_params)
    limit_str = params.get("limit")
    cursor_str = params.get("cursor")
    status_param = params.get("status")

    limit = DEFAULT_PAGE_SIZE
    if limit_str:
        try:
            limit = int(limit_str)
            if limit < MIN_PAGE_SIZE or limit > MAX_PAGE_SIZE:
                raise ValueError
        except ValueError:
            resp = invalid_parameter_error(
                f"limit must be between {MIN_PAGE_SIZE} and {MAX_PAGE_SIZE}",
                f"Use limit={DEFAULT_PAGE_SIZE} (default) or any value 1-{MAX_PAGE_SIZE}",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)

    valid_statuses = [
        "open", "claimed", "in_progress", "delivered", "completed", "cancelled", "disputed"
    ]
    if status_param and status_param not in valid_statuses:
        resp = invalid_parameter_error(
            f"Invalid status: {status_param}",
            f"Valid values: {', '.join(valid_statuses)}",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    conditions = [Task.claimed_by_agent_id == agent.id]
    if status_param:
        conditions.append(Task.status == status_param)

    if cursor_str:
        decoded = decode_cursor(cursor_str)
        if not decoded:
            resp = invalid_parameter_error(
                "Invalid cursor value",
                "Use the cursor value from a previous response's meta.cursor field",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)
        conditions.append(Task.id < decoded["id"])

    result = await session.execute(
        select(
            Task.id,
            Task.title,
            Task.description,
            Task.budget_credits,
            Category.name.label("category_name"),
            Task.status,
            User.name.label("poster_name"),
            Task.deadline,
            Task.max_revisions,
            Task.created_at,
        )
        .select_from(Task)
        .outerjoin(Category, Task.category_id == Category.id)
        .join(User, Task.poster_id == User.id)
        .where(and_(*conditions))
        .order_by(Task.id)
        .limit(limit + 1)
    )
    rows = result.all()

    has_more = len(rows) > limit
    page_rows = rows[:limit] if has_more else rows

    data = [
        {
            "id": t.id,
            "title": t.title,
            "description": t.description,
            "budget_credits": t.budget_credits,
            "category": t.category_name,
            "status": t.status,
            "poster_name": t.poster_name,
            "deadline": _isoformat(t.deadline),
            "max_revisions": t.max_revisions,
            "created_at": _isoformat(t.created_at),
        }
        for t in page_rows
    ]

    next_cursor = None
    if has_more and page_rows:
        next_cursor = encode_cursor(page_rows[-1].id)

    resp = success_response(data, 200, {
        "cursor": next_cursor,
        "has_more": has_more,
        "count": len(data),
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/agents/me/credits — Balance + paginated ledger ──────────────

@router.get("/me/credits")
async def get_my_credits(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    params = dict(request.query_params)
    limit_str = params.get("limit")
    cursor_str = params.get("cursor")

    limit = DEFAULT_PAGE_SIZE
    if limit_str:
        try:
            limit = int(limit_str)
            if limit < MIN_PAGE_SIZE or limit > MAX_PAGE_SIZE:
                raise ValueError
        except ValueError:
            resp = invalid_parameter_error(
                f"limit must be between {MIN_PAGE_SIZE} and {MAX_PAGE_SIZE}",
                f"Use limit={DEFAULT_PAGE_SIZE} (default) or any value 1-{MAX_PAGE_SIZE}",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)

    # Get operator's credit balance
    user_result = await session.execute(
        select(User.credit_balance).where(User.id == agent.operator_id).limit(1)
    )
    credit_balance = user_result.scalar_one()

    # Build conditions
    conditions = [CreditTransaction.user_id == agent.operator_id]

    if cursor_str:
        decoded = decode_cursor(cursor_str)
        if not decoded:
            resp = invalid_parameter_error(
                "Invalid cursor value",
                "Use the cursor value from a previous response's meta.cursor field",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)
        conditions.append(CreditTransaction.id < decoded["id"])

    result = await session.execute(
        select(
            CreditTransaction.id,
            CreditTransaction.amount,
            CreditTransaction.type,
            CreditTransaction.task_id,
            CreditTransaction.description,
            CreditTransaction.balance_after,
            CreditTransaction.created_at,
        )
        .where(and_(*conditions))
        .order_by(desc(CreditTransaction.id))
        .limit(limit + 1)
    )
    rows = result.all()

    has_more = len(rows) > limit
    page_rows = rows[:limit] if has_more else rows

    transactions = [
        {
            "id": t.id,
            "amount": t.amount,
            "type": t.type,
            "task_id": t.task_id,
            "description": t.description,
            "balance_after": t.balance_after,
            "created_at": _isoformat(t.created_at),
        }
        for t in page_rows
    ]

    next_cursor = None
    if has_more and page_rows:
        next_cursor = encode_cursor(page_rows[-1].id)

    resp = success_response(
        {"credit_balance": credit_balance, "transactions": transactions},
        200,
        {"cursor": next_cursor, "has_more": has_more, "count": len(transactions)},
    )
    return add_rate_limit_headers(resp, agent.rate_limit)

