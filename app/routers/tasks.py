"""All /api/v1/tasks/* endpoints — port of Oriexa/src/app/api/v1/tasks/"""

from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import and_, asc, desc, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.envelope import success_response
from app.api.errors import (
    conflict_error,
    duplicate_claim_error,
    forbidden_error,
    invalid_credits_error,
    invalid_parameter_error,
    invalid_status_error,
    max_revisions_error,
    rollback_forbidden_error,
    task_not_claimed_error,
    task_not_found_error,
    validation_error,
)
from app.api.pagination import decode_cursor, encode_cursor
from app.auth.dependencies import get_current_agent
from app.db.engine import get_db
from app.db.models import (
    Agent,
    Category,
    CreditTransaction,
    Deliverable,
    SubmissionAttempt,
    Task,
    TaskClaim,
    TaskMessage,
    User,
)
from app.middleware.pipeline import AgentContext
from app.middleware.rate_limit import add_rate_limit_headers
from app.schemas.claims import BulkClaimsRequest, CreateClaimRequest
from app.schemas.deliverables import CreateDeliverableRequest
from app.schemas.tasks import BrowseTasksParams, CreateTaskRequest
from app.services.credits import process_task_completion
from app.services.crypto import encrypt_key
from app.services.webhooks import dispatch_new_task_match, dispatch_webhook_event
from app.api.events import event_broadcaster

router = APIRouter()


def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


# ─── GET /api/v1/tasks — Browse tasks ────────────────────────────────────────

@router.get("")
async def browse_tasks(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    params = dict(request.query_params)

    # Parse and validate query params
    try:
        p = BrowseTasksParams(
            status=params.get("status", "open"),
            category=int(params["category"]) if "category" in params else None,
            min_budget=int(params["min_budget"]) if "min_budget" in params else None,
            max_budget=int(params["max_budget"]) if "max_budget" in params else None,
            sort=params.get("sort", "newest"),
            cursor=params.get("cursor"),
            limit=int(params["limit"]) if "limit" in params else 20,
        )
    except Exception as e:
        resp = invalid_parameter_error(
            str(e),
            "Valid parameters: status, category, min_budget, max_budget, sort (newest|oldest|budget_high|budget_low), cursor, limit (1-100)",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Build WHERE conditions
    conditions = [Task.status == p.status]

    if p.category:
        conditions.append(Task.category_id == p.category)
    if p.min_budget is not None:
        conditions.append(Task.budget_credits >= p.min_budget)
    if p.max_budget is not None:
        conditions.append(Task.budget_credits <= p.max_budget)

    # Handle cursor
    if p.cursor:
        decoded = decode_cursor(p.cursor)
        if not decoded:
            resp = invalid_parameter_error(
                "Invalid cursor value",
                "Use the cursor value from a previous response's meta.cursor field",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)

        if p.sort in ("budget_high", "budget_low") and decoded.get("v") is None:
            resp = invalid_parameter_error(
                "Cursor is not compatible with this sort order",
                "Use the cursor value from a response with the same sort parameter",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)

        if p.sort == "newest":
            conditions.append(Task.id < decoded["id"])
        elif p.sort == "oldest":
            conditions.append(Task.id > decoded["id"])
        elif p.sort == "budget_high":
            v = int(decoded["v"])
            conditions.append(
                text(
                    f"(tasks.budget_credits < {v} OR (tasks.budget_credits = {v} AND tasks.id < {decoded['id']}))"
                )
            )
        elif p.sort == "budget_low":
            v = int(decoded["v"])
            conditions.append(
                text(
                    f"(tasks.budget_credits > {v} OR (tasks.budget_credits = {v} AND tasks.id > {decoded['id']}))"
                )
            )

    # Determine sort order
    if p.sort == "newest":
        order_by = [desc(Task.id)]
    elif p.sort == "oldest":
        order_by = [asc(Task.id)]
    elif p.sort == "budget_high":
        order_by = [desc(Task.budget_credits), desc(Task.id)]
    else:  # budget_low
        order_by = [asc(Task.budget_credits), asc(Task.id)]

    # Fetch one extra to determine has_more
    query = (
        select(
            Task.id,
            Task.title,
            Task.description,
            Task.budget_credits,
            Task.category_id,
            Category.name.label("category_name"),
            Category.slug.label("category_slug"),
            Task.status,
            User.id.label("poster_id"),
            User.name.label("poster_name"),
            Task.deadline,
            Task.max_revisions,
            Task.created_at,
            Task.updated_at,
        )
        .select_from(Task)
        .outerjoin(Category, Task.category_id == Category.id)
        .join(User, Task.poster_id == User.id)
        .where(and_(*conditions))
        .order_by(*order_by)
        .limit(p.limit + 1)
    )

    result = await session.execute(query)
    rows = result.all()

    has_more = len(rows) > p.limit
    page_rows = rows[: p.limit] if has_more else rows

    # Get claims counts for tasks in this page
    task_ids = [r.id for r in page_rows]
    claims_counts: dict[int, int] = {}
    if task_ids:
        counts_q = (
            select(TaskClaim.task_id, func.count().label("cnt"))
            .where(TaskClaim.task_id.in_(task_ids))
            .group_by(TaskClaim.task_id)
        )
        counts_result = await session.execute(counts_q)
        claims_counts = {r.task_id: r.cnt for r in counts_result.all()}

    # Format response
    data = []
    for row in page_rows:
        data.append({
            "id": row.id,
            "title": row.title,
            "description": row.description,
            "budget_credits": row.budget_credits,
            "category": (
                {"id": row.category_id, "name": row.category_name, "slug": row.category_slug}
                if row.category_id
                else None
            ),
            "status": row.status,
            "poster": {"id": row.poster_id, "name": row.poster_name},
            "claims_count": claims_counts.get(row.id, 0),
            "deadline": _isoformat(row.deadline),
            "max_revisions": row.max_revisions,
            "created_at": _isoformat(row.created_at),
        })

    # Build cursor for next page
    next_cursor = None
    if has_more and page_rows:
        last_row = page_rows[-1]
        sort_value = (
            str(last_row.budget_credits)
            if p.sort in ("budget_high", "budget_low")
            else None
        )
        next_cursor = encode_cursor(last_row.id, sort_value)

    resp = success_response(
        data,
        200,
        {"cursor": next_cursor, "has_more": has_more, "count": len(data)},
    )
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks — Create task ────────────────────────────────────────

@router.post("")
async def create_task(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            'Send a JSON body with title, description, and budget_credits',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        data = CreateTaskRequest(**body)
    except Exception as e:
        resp = validation_error(str(e), "Check field requirements and try again")
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Encrypt poster LLM key if provided
    poster_llm_key_encrypted = None
    if data.poster_llm_key:
        try:
            poster_llm_key_encrypted = encrypt_key(data.poster_llm_key)
        except Exception:
            pass

    task = Task(
        poster_id=agent.operator_id,
        title=data.title,
        description=data.description,
        requirements=data.requirements,
        budget_credits=data.budget_credits,
        category_id=data.category_id,
        deadline=datetime.fromisoformat(data.deadline) if data.deadline else None,
        max_revisions=data.max_revisions if data.max_revisions is not None else 2,
        status="open",
        auto_review_enabled=data.auto_review_enabled,
        poster_llm_key_encrypted=poster_llm_key_encrypted,
        poster_llm_provider=data.poster_llm_provider,
        poster_max_reviews=data.poster_max_reviews,
    )
    session.add(task)
    await session.flush()
    await session.commit()
    await session.refresh(task)

    # Dispatch webhook for new task matching agents' categories
    dispatch_new_task_match(task.id, task.category_id, {
        "task_id": task.id,
        "title": task.title,
        "budget_credits": task.budget_credits,
        "category_id": task.category_id,
    })

    resp = success_response(
        {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "budget_credits": task.budget_credits,
            "category_id": task.category_id,
            "status": task.status,
            "poster_id": task.poster_id,
            "auto_review_enabled": task.auto_review_enabled,
            "deadline": _isoformat(task.deadline),
            "max_revisions": task.max_revisions,
            "created_at": _isoformat(task.created_at),
        },
        201,
    )
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/tasks/{task_id} — Task detail ───────────────────────────────

@router.get("/{task_id}")
async def get_task(
    task_id: str,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    # Validate: must be a positive integer (catches "abc", "-5", "0", etc.)
    try:
        task_id_int = int(task_id)
        if task_id_int < 1:
            raise ValueError("non-positive")
    except (ValueError, OverflowError):
        resp = invalid_parameter_error(
            f"Invalid task ID: {task_id}",
            "Task IDs are positive integers. Use GET /api/v1/tasks to browse available tasks.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    result = await session.execute(
        select(
            Task.id,
            Task.title,
            Task.description,
            Task.requirements,
            Task.budget_credits,
            Task.category_id,
            Category.name.label("category_name"),
            Category.slug.label("category_slug"),
            Task.status,
            Task.claimed_by_agent_id,
            User.id.label("poster_id"),
            User.name.label("poster_name"),
            Task.deadline,
            Task.max_revisions,
            Task.auto_review_enabled,
            Task.created_at,
            Task.created_at,
            Task.updated_at,
            Task.agent_remarks,
        )
        .select_from(Task)
        .outerjoin(Category, Task.category_id == Category.id)
        .join(User, Task.poster_id == User.id)
        .where(Task.id == task_id_int)
        .limit(1)
    )
    task = result.first()

    if not task:
        resp = task_not_found_error(task_id_int)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Claims count
    claims_result = await session.execute(
        select(func.count()).select_from(TaskClaim).where(TaskClaim.task_id == task_id_int)
    )
    claims_count = claims_result.scalar() or 0

    # Deliverables list
    dels_result = await session.execute(
        select(
            Deliverable.id,
            Deliverable.agent_id,
            Deliverable.content,
            Deliverable.status,
            Deliverable.revision_number,
            Deliverable.revision_notes,
            Deliverable.submitted_at,
        ).where(Deliverable.task_id == task_id_int)
    )
    dels_list = dels_result.all()

    resp = success_response({
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "requirements": task.requirements,
        "budget_credits": task.budget_credits,
        "category": (
            {"id": task.category_id, "name": task.category_name, "slug": task.category_slug}
            if task.category_id
            else None
        ),
        "status": task.status,
        "claimed_by_agent_id": task.claimed_by_agent_id,
        "poster": {"id": task.poster_id, "name": task.poster_name},
        "agent_remarks": task.agent_remarks,
        "claims_count": claims_count,
        "deliverables_count": len(dels_list),
        "deliverables": [
            {
                "id": d.id,
                "agent_id": d.agent_id,
                "content": d.content,
                "status": d.status,
                "revision_number": d.revision_number,
                "revision_notes": d.revision_notes,
                "submitted_at": _isoformat(d.submitted_at),
            }
            for d in dels_list
        ],
        "auto_review_enabled": task.auto_review_enabled,
        "deadline": _isoformat(task.deadline),
        "max_revisions": task.max_revisions,
        "created_at": _isoformat(task.created_at),
        "updated_at": _isoformat(task.updated_at),
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/claims — Create claim ──────────────────────

@router.post("/{task_id:int}/claims")
async def create_claim(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error(
            "Invalid task ID",
            "Task IDs are positive integers. Use GET /api/v1/tasks to browse available tasks.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            'Send a JSON body with { "proposed_credits": <integer> }',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        data = CreateClaimRequest(**body)
    except Exception as e:
        resp = validation_error(
            str(e),
            "Include proposed_credits in request body (integer, min 1)",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate task exists and is open
    result = await session.execute(
        select(Task.id, Task.status, Task.budget_credits).where(Task.id == task_id).limit(1)
    )
    task = result.first()

    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status != "open":
        resp = task_not_found_error(task_id) if task.status == "cancelled" else \
            conflict_error(
                "TASK_NOT_OPEN",
                f"Task {task_id} is not open (current status: {task.status})",
                "This task has already been claimed. Browse open tasks with GET /api/v1/tasks?status=open",
            )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if data.proposed_credits > task.budget_credits:
        resp = invalid_credits_error(data.proposed_credits, task.budget_credits)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Check for duplicate claim (pending or accepted)
    existing = await session.execute(
        select(TaskClaim.id).where(
            and_(
                TaskClaim.task_id == task_id,
                TaskClaim.agent_id == agent.id,
                TaskClaim.status.in_(["pending", "accepted"]),
            )
        ).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        resp = duplicate_claim_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Create the claim
    claim = TaskClaim(
        task_id=task_id,
        agent_id=agent.id,
        proposed_credits=data.proposed_credits,
        message=data.message,
        status="pending",
    )
    session.add(claim)
    await session.flush()
    await session.commit()
    await session.refresh(claim)

    # Dual-write: insert claim_proposal message
    msg = TaskMessage(
        task_id=task_id,
        sender_type="agent",
        sender_id=agent.id,
        sender_name=agent.name,
        content=data.message or f"Proposed {data.proposed_credits} credits",
        message_type="claim_proposal",
        claim_id=claim.id,
        structured_data={
            "proposed_credits": data.proposed_credits,
            "message": data.message,
        },
    )
    session.add(msg)
    await session.flush()
    await session.commit()

    # Broadcast real-time event to task poster
    poster_result = await session.execute(
        select(Task.poster_id).where(Task.id == task_id).limit(1)
    )
    poster_row = poster_result.first()
    if poster_row:
        event_broadcaster.broadcast(poster_row.poster_id, "claim_created", {
            "task_id": task_id,
            "claim_id": claim.id,
            "agent_id": claim.agent_id,
            "proposed_credits": claim.proposed_credits,
        })
        event_broadcaster.broadcast(poster_row.poster_id, "message_created", {
            "task_id": task_id,
            "message_id": msg.id,
            "sender_type": "agent",
            "message_type": "claim_proposal",
        })

    resp = success_response(
        {
            "id": claim.id,
            "task_id": claim.task_id,
            "agent_id": claim.agent_id,
            "proposed_credits": claim.proposed_credits,
            "message": claim.message,
            "status": claim.status,
            "created_at": _isoformat(claim.created_at),
        },
        201,
    )
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/tasks/{task_id}/claims — List claims ────────────────────────

@router.get("/{task_id:int}/claims")
async def list_claims(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """List all claims for a task. Poster sees all claims; others see only their own."""
    if task_id < 1:
        resp = invalid_parameter_error(
            "Invalid task ID",
            "Task IDs are positive integers. Use GET /api/v1/tasks to browse available tasks.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate task exists
    task_result = await session.execute(
        select(Task.id, Task.poster_id).where(Task.id == task_id).limit(1)
    )
    task = task_result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    params = dict(request.query_params)
    limit = 20
    try:
        limit = int(params.get("limit", 20))
        if limit < 1 or limit > 100:
            raise ValueError
    except ValueError:
        resp = invalid_parameter_error("limit must be between 1 and 100", "Use limit=20 for default page size")
        return add_rate_limit_headers(resp, agent.rate_limit)

    cursor_str = params.get("cursor")
    conditions = [TaskClaim.task_id == task_id]

    # Poster sees all claims; agents see only their own
    if task.poster_id != agent.operator_id:
        conditions.append(TaskClaim.agent_id == agent.id)

    if cursor_str:
        from app.api.pagination import decode_cursor
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
            TaskClaim.agent_id,
            Agent.name.label("agent_name"),
            TaskClaim.proposed_credits,
            TaskClaim.message,
            TaskClaim.status,
            TaskClaim.created_at,
        )
        .select_from(TaskClaim)
        .join(Agent, TaskClaim.agent_id == Agent.id)
        .where(and_(*conditions))
        .order_by(desc(TaskClaim.id))
        .limit(limit + 1)
    )
    rows = result.all()

    has_more = len(rows) > limit
    page_rows = rows[:limit] if has_more else rows

    data = [
        {
            "id": c.id,
            "task_id": c.task_id,
            "agent_id": c.agent_id,
            "agent_name": c.agent_name,
            "proposed_credits": c.proposed_credits,
            "message": c.message,
            "status": c.status,
            "created_at": _isoformat(c.created_at),
        }
        for c in page_rows
    ]

    next_cursor = None
    if has_more and page_rows:
        from app.api.pagination import encode_cursor
        next_cursor = encode_cursor(page_rows[-1].id)

    resp = success_response(data, 200, {
        "cursor": next_cursor,
        "has_more": has_more,
        "count": len(data),
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/claims/accept ──────────────────────────────

@router.post("/{task_id:int}/claims/accept")
async def accept_claim(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error("Invalid task ID", "Task IDs are positive integers.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        body = await request.json()
    except Exception:
        resp = validation_error("Invalid JSON body", 'Send { "claim_id": <integer> }')
        return add_rate_limit_headers(resp, agent.rate_limit)

    claim_id = body.get("claim_id")
    if not isinstance(claim_id, int) or claim_id < 1:
        resp = validation_error(
            "claim_id is required and must be a positive integer",
            "Include claim_id in request body",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate task
    result = await session.execute(
        select(Task.id, Task.status, Task.poster_id).where(Task.id == task_id).limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.poster_id != agent.operator_id:
        resp = forbidden_error(
            "Only the task poster can accept claims",
            "You must be the poster of this task to accept claims",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status != "open":
        resp = conflict_error(
            "TASK_NOT_OPEN",
            f"Task {task_id} is not open (status: {task.status})",
            "Only open tasks can have claims accepted",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate claim
    claim_result = await session.execute(
        select(TaskClaim).where(
            and_(
                TaskClaim.id == claim_id,
                TaskClaim.task_id == task_id,
                TaskClaim.status == "pending",
            )
        ).limit(1)
    )
    claim = claim_result.scalar_one_or_none()
    if not claim:
        resp = conflict_error(
            "CLAIM_NOT_FOUND",
            f"Claim {claim_id} not found or not pending on task {task_id}",
            "Check pending claims with GET /api/v1/tasks/:id/claims",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Accept claim, reject others, update task (optimistic lock)
    updated = await session.execute(
        update(Task)
        .where(and_(Task.id == task_id, Task.status == "open"))
        .values(
            status="claimed",
            claimed_by_agent_id=claim.agent_id,
            updated_at=datetime.now(timezone.utc),
        )
        .returning(Task.id)
    )
    if not updated.first():
        await session.rollback()
        resp = conflict_error(
            "TASK_NOT_OPEN",
            f"Task {task_id} is no longer open",
            "Another claim was accepted concurrently",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    await session.execute(
        update(TaskClaim).where(TaskClaim.id == claim_id).values(status="accepted")
    )
    await session.execute(
        update(TaskClaim)
        .where(
            and_(
                TaskClaim.task_id == task_id,
                TaskClaim.id != claim_id,
                TaskClaim.status == "pending",
            )
        )
        .values(status="rejected")
    )
    await session.commit()

    # Dispatch webhooks
    dispatch_webhook_event(claim.agent_id, "claim.accepted", {
        "task_id": task_id,
        "claim_id": claim_id,
        "agent_id": claim.agent_id,
    })

    resp = success_response({
        "task_id": task_id,
        "claim_id": claim_id,
        "agent_id": claim.agent_id,
        "status": "accepted",
        "message": f"Claim {claim_id} accepted. Task {task_id} is now claimed.",
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/bulk/claims ──────────────────────────────────────────

@router.post("/bulk/claims")
async def bulk_claims(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            'Send { "claims": [{ "task_id": <int>, "proposed_credits": <int> }, ...] } (max 10)',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        data = BulkClaimsRequest(**body)
    except Exception as e:
        resp = validation_error(str(e), "Provide 1-10 claims, each with task_id and proposed_credits")
        return add_rate_limit_headers(resp, agent.rate_limit)

    results = []
    succeeded = 0
    failed = 0

    for claim_req in data.claims:
        try:
            task_result = await session.execute(
                select(Task.id, Task.status, Task.budget_credits)
                .where(Task.id == claim_req.task_id)
                .limit(1)
            )
            task = task_result.first()

            if not task:
                results.append({
                    "task_id": claim_req.task_id,
                    "ok": False,
                    "error": {"code": "TASK_NOT_FOUND", "message": f"Task {claim_req.task_id} does not exist"},
                })
                failed += 1
                continue

            if task.status != "open":
                results.append({
                    "task_id": claim_req.task_id,
                    "ok": False,
                    "error": {"code": "TASK_NOT_OPEN", "message": f"Task {claim_req.task_id} is not open (status: {task.status})"},
                })
                failed += 1
                continue

            if claim_req.proposed_credits > task.budget_credits:
                results.append({
                    "task_id": claim_req.task_id,
                    "ok": False,
                    "error": {"code": "INVALID_CREDITS", "message": f"proposed_credits ({claim_req.proposed_credits}) exceeds budget ({task.budget_credits})"},
                })
                failed += 1
                continue

            # Check duplicate
            existing = await session.execute(
                select(TaskClaim.id).where(
                    and_(
                        TaskClaim.task_id == claim_req.task_id,
                        TaskClaim.agent_id == agent.id,
                        TaskClaim.status == "pending",
                    )
                ).limit(1)
            )
            if existing.scalar_one_or_none() is not None:
                results.append({
                    "task_id": claim_req.task_id,
                    "ok": False,
                    "error": {"code": "DUPLICATE_CLAIM", "message": f"Already have a pending claim on task {claim_req.task_id}"},
                })
                failed += 1
                continue

            claim = TaskClaim(
                task_id=claim_req.task_id,
                agent_id=agent.id,
                proposed_credits=claim_req.proposed_credits,
                message=claim_req.message,
                status="pending",
            )
            session.add(claim)
            await session.flush()

            results.append({"task_id": claim_req.task_id, "ok": True, "claim_id": claim.id})
            succeeded += 1

        except Exception:
            results.append({
                "task_id": claim_req.task_id,
                "ok": False,
                "error": {"code": "INTERNAL_ERROR", "message": f"Failed to process claim for task {claim_req.task_id}"},
            })
            failed += 1

    await session.commit()

    resp = success_response({
        "results": results,
        "summary": {"succeeded": succeeded, "failed": failed, "total": len(data.claims)},
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/tasks/{task_id}/deliverables — List deliverables ────────────

@router.get("/{task_id:int}/deliverables")
async def list_task_deliverables(
    task_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error(
            "Invalid task ID",
            "Task IDs are positive integers. Use GET /api/v1/tasks to browse available tasks.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Verify task exists
    task_result = await session.execute(
        select(Task.id).where(Task.id == task_id).limit(1)
    )
    if not task_result.first():
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    result = await session.execute(
        select(
            Deliverable.id,
            Deliverable.task_id,
            Deliverable.agent_id,
            Deliverable.content,
            Deliverable.status,
            Deliverable.revision_number,
            Deliverable.revision_notes,
            Deliverable.submitted_at,
        )
        .where(Deliverable.task_id == task_id)
        .order_by(desc(Deliverable.revision_number))
    )
    rows = result.all()

    data = [
        {
            "id": r.id,
            "task_id": r.task_id,
            "agent_id": r.agent_id,
            "content": r.content,
            "status": r.status,
            "revision_number": r.revision_number,
            "revision_notes": r.revision_notes,
            "submitted_at": _isoformat(r.submitted_at),
        }
        for r in rows
    ]

    resp = success_response(data, 200, {"cursor": None, "has_more": False, "count": len(data)})
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/deliverables — Submit deliverable ──────────

@router.post("/{task_id:int}/deliverables")
async def submit_deliverable(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error(
            "Invalid task ID",
            "Task IDs are positive integers. Use GET /api/v1/tasks to browse available tasks.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            'Send a JSON body with { "content": "<your deliverable>" }',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        data = CreateDeliverableRequest(**body)
    except Exception as e:
        resp = validation_error(str(e), "Include content in request body (string, max 50000 chars)")
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate task
    result = await session.execute(
        select(Task.id, Task.status, Task.claimed_by_agent_id, Task.max_revisions)
        .where(Task.id == task_id)
        .limit(1)
    )
    task = result.first()

    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status not in ("claimed", "in_progress"):
        suggestion = (
            f"Claim the task first with POST /api/v1/tasks/{task_id}/claims"
            if task.status == "open"
            else f"Task {task_id} cannot accept deliverables in status: {task.status}"
        )
        resp = invalid_status_error(task_id, task.status, suggestion)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.claimed_by_agent_id != agent.id:
        resp = forbidden_error(
            f"Task {task_id} is not claimed by your agent",
            "You can only deliver to tasks you have claimed",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Get current revision number
    latest = await session.execute(
        select(Deliverable.revision_number)
        .where(
            and_(
                Deliverable.task_id == task_id,
                Deliverable.agent_id == agent.id
            )
        )
        .order_by(desc(Deliverable.revision_number))
        .limit(1)
    )
    latest_rev = latest.scalar_one_or_none()
    next_revision = (latest_rev + 1) if latest_rev is not None else 1

    # Check max revisions (revision 1 = original, 2..max_revisions+1 = revisions)
    if next_revision > task.max_revisions + 1:
        resp = max_revisions_error(task_id, next_revision - 1, task.max_revisions)
        return add_rate_limit_headers(resp, agent.rate_limit)

    deliverable = Deliverable(
        task_id=task_id,
        agent_id=agent.id,
        content=data.content,
        status="submitted",
        revision_number=next_revision,
    )
    session.add(deliverable)

    await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(status="delivered", updated_at=datetime.now(timezone.utc))
    )
    await session.flush()
    await session.commit()
    await session.refresh(deliverable)

    # Broadcast real-time event to task poster
    poster_result = await session.execute(
        select(Task.poster_id).where(Task.id == task_id).limit(1)
    )
    poster_row = poster_result.first()
    if poster_row:
        event_broadcaster.broadcast(poster_row.poster_id, "deliverable_submitted", {
            "task_id": task_id,
            "deliverable_id": deliverable.id,
            "revision_number": deliverable.revision_number,
        })
        event_broadcaster.broadcast(poster_row.poster_id, "task_updated", {
            "task_id": task_id,
            "status": "delivered",
        })

    resp = success_response(
        {
            "id": deliverable.id,
            "task_id": deliverable.task_id,
            "agent_id": deliverable.agent_id,
            "content": deliverable.content,
            "status": deliverable.status,
            "revision_number": deliverable.revision_number,
            "submitted_at": _isoformat(deliverable.submitted_at),
        },
        201,
    )
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/deliverables/accept ────────────────────────

@router.post("/{task_id:int}/deliverables/accept")
async def accept_deliverable(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error("Invalid task ID", "Task IDs are positive integers.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        body = await request.json()
    except Exception:
        resp = validation_error("Invalid JSON body", 'Send { "deliverable_id": <integer> }')
        return add_rate_limit_headers(resp, agent.rate_limit)

    deliverable_id = body.get("deliverable_id")
    if not isinstance(deliverable_id, int) or deliverable_id < 1:
        resp = validation_error(
            "deliverable_id is required and must be a positive integer",
            "Include deliverable_id in request body",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate task
    result = await session.execute(
        select(Task.id, Task.status, Task.poster_id, Task.budget_credits, Task.claimed_by_agent_id)
        .where(Task.id == task_id)
        .limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.poster_id != agent.operator_id:
        resp = forbidden_error(
            "Only the task poster can accept deliverables",
            "You must be the poster of this task to accept deliverables",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status != "delivered":
        resp = conflict_error(
            "INVALID_STATUS",
            f"Task {task_id} is not in delivered state (status: {task.status})",
            "Wait for the agent to submit a deliverable",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate deliverable
    del_result = await session.execute(
        select(Deliverable).where(Deliverable.id == deliverable_id).limit(1)
    )
    deliverable = del_result.scalar_one_or_none()
    if not deliverable or deliverable.task_id != task_id:
        resp = conflict_error(
            "DELIVERABLE_NOT_FOUND",
            f"Deliverable {deliverable_id} not found on task {task_id}",
            "Check deliverables for this task",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Optimistic lock
    updated = await session.execute(
        update(Task)
        .where(and_(Task.id == task_id, Task.status == "delivered"))
        .values(status="completed", updated_at=datetime.now(timezone.utc))
        .returning(Task.id)
    )
    if not updated.first():
        await session.rollback()
        resp = conflict_error(
            "INVALID_STATUS",
            f"Task {task_id} is no longer in delivered state",
            "The deliverable may have already been accepted",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    await session.execute(
        update(Deliverable).where(Deliverable.id == deliverable_id).values(status="accepted")
    )
    await session.commit()

    # Process credits
    credit_result = None
    if task.claimed_by_agent_id:
        agent_data = await session.execute(
            select(Agent.operator_id).where(Agent.id == task.claimed_by_agent_id).limit(1)
        )
        agent_row = agent_data.first()
        if agent_row:
            credit_result = await process_task_completion(
                session, agent_row.operator_id, task.budget_credits, task_id
            )
            await session.execute(
                update(Agent)
                .where(Agent.id == task.claimed_by_agent_id)
                .values(
                    tasks_completed=Agent.tasks_completed + 1,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    # Dispatch webhook
    if task.claimed_by_agent_id:
        dispatch_webhook_event(task.claimed_by_agent_id, "deliverable.accepted", {
            "task_id": task_id,
            "deliverable_id": deliverable_id,
            "credits_paid": credit_result["payment"] if credit_result else 0,
            "platform_fee": credit_result["fee"] if credit_result else 0,
        })

    resp = success_response({
        "task_id": task_id,
        "deliverable_id": deliverable_id,
        "status": "completed",
        "credits_paid": credit_result["payment"] if credit_result else 0,
        "platform_fee": credit_result["fee"] if credit_result else 0,
        "message": f"Deliverable accepted. Task {task_id} completed.",
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/deliverables/revision ──────────────────────

@router.post("/{task_id:int}/deliverables/revision")
async def request_revision(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error("Invalid task ID", "Task IDs are positive integers.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            'Send { "deliverable_id": <int>, "revision_notes": "<feedback>" }',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    deliverable_id = body.get("deliverable_id")
    revision_notes = body.get("revision_notes", "")

    if not isinstance(deliverable_id, int) or deliverable_id < 1:
        resp = validation_error("deliverable_id is required", "Include deliverable_id in request body")
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate task
    result = await session.execute(
        select(Task.id, Task.status, Task.poster_id, Task.max_revisions, Task.claimed_by_agent_id)
        .where(Task.id == task_id)
        .limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.poster_id != agent.operator_id:
        resp = forbidden_error(
            "Only the task poster can request revisions",
            "You must be the poster of this task to request revisions",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status != "delivered":
        resp = conflict_error(
            "INVALID_STATUS",
            f"Task {task_id} is not in delivered state (status: {task.status})",
            "Revisions can only be requested on delivered tasks",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate deliverable
    del_result = await session.execute(
        select(Deliverable).where(Deliverable.id == deliverable_id).limit(1)
    )
    deliverable = del_result.scalar_one_or_none()
    if not deliverable or deliverable.task_id != task_id:
        resp = conflict_error(
            "DELIVERABLE_NOT_FOUND",
            f"Deliverable {deliverable_id} not found on task {task_id}",
            "Check deliverables for this task",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if deliverable.revision_number >= task.max_revisions + 1:
        resp = max_revisions_error(task_id, deliverable.revision_number, task.max_revisions)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Update deliverable and task
    await session.execute(
        update(Deliverable)
        .where(Deliverable.id == deliverable_id)
        .values(status="revision_requested", revision_notes=revision_notes)
    )
    await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(status="in_progress", updated_at=datetime.now(timezone.utc))
    )
    await session.commit()

    if task.claimed_by_agent_id:
        dispatch_webhook_event(task.claimed_by_agent_id, "deliverable.revision_requested", {
            "task_id": task_id,
            "deliverable_id": deliverable_id,
            "revision_notes": revision_notes,
        })

    resp = success_response({
        "task_id": task_id,
        "deliverable_id": deliverable_id,
        "status": "revision_requested",
        "revision_notes": revision_notes,
        "message": f"Revision requested on deliverable {deliverable_id}. Task {task_id} is back to in_progress.",
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/start ──────────────────────────────────────

@router.post("/{task_id:int}/start")
async def start_task(
    task_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """Mark a claimed task as in_progress when the agent begins active work.

    Transitions: claimed → in_progress
    Only the agent who claimed the task may call this.
    """
    if task_id < 1:
        resp = invalid_parameter_error("Invalid task ID", "Task IDs are positive integers.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    result = await session.execute(
        select(Task.id, Task.status, Task.claimed_by_agent_id, Task.poster_id)
        .where(Task.id == task_id)
        .limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Only the claiming agent may start the task
    if task.claimed_by_agent_id != agent.id:
        resp = forbidden_error(
            "Only the agent who claimed this task can start it",
            "Claim the task first via POST /api/v1/tasks/{task_id}/claims"
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Idempotent: already in_progress is fine
    if task.status == "in_progress":
        resp = success_response({"task_id": task_id, "status": "in_progress"})
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status != "claimed":
        resp = invalid_status_error(
            task_id, task.status,
            "Task must be in 'claimed' state to start. Only claimed tasks can be transitioned to in_progress."
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(status="in_progress", updated_at=datetime.now(timezone.utc))
    )
    await session.commit()

    # Broadcast real-time event to poster
    poster_id = task.poster_id
    try:
        event_broadcaster.broadcast(poster_id, "task_updated", {
            "task_id": task_id,
            "status": "in_progress",
        })
    except Exception:
        pass

    resp = success_response({"task_id": task_id, "status": "in_progress"})
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/rollback ───────────────────────────────────

@router.post("/{task_id:int}/rollback")
async def rollback_task(
    task_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error("Invalid task ID", "Task IDs are positive integers.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    result = await session.execute(
        select(Task.id, Task.status, Task.poster_id, Task.claimed_by_agent_id)
        .where(Task.id == task_id)
        .limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.poster_id != agent.operator_id:
        resp = rollback_forbidden_error()
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status != "claimed":
        resp = task_not_claimed_error(task_id, task.status)
        return add_rate_limit_headers(resp, agent.rate_limit)

    previous_agent_id = task.claimed_by_agent_id

    await session.execute(
        update(TaskClaim)
        .where(and_(TaskClaim.task_id == task_id, TaskClaim.status == "accepted"))
        .values(status="withdrawn")
    )
    await session.execute(
        update(Task)
        .where(Task.id == task_id)
        .values(status="open", claimed_by_agent_id=None, updated_at=datetime.now(timezone.utc))
    )
    await session.commit()

    resp = success_response({
        "task_id": task_id,
        "previous_status": "claimed",
        "status": "open",
        "previous_agent_id": previous_agent_id,
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/review ─────────────────────────────────────

@router.post("/{task_id:int}/review")
async def review_task(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error(
            "Invalid task ID",
            "Task IDs are positive integers. Use GET /api/v1/tasks to browse tasks.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            "Send { deliverable_id, verdict: 'pass'|'fail', feedback, scores?, model_used?, key_source? }",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    from app.schemas.reviews import ReviewRequest
    try:
        data = ReviewRequest(**body)
    except Exception as e:
        resp = validation_error(str(e), "Check required fields: deliverable_id, verdict, feedback")
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate task
    result = await session.execute(
        select(
            Task.id, Task.status, Task.auto_review_enabled,
            Task.budget_credits, Task.claimed_by_agent_id, Task.poster_reviews_used,
        ).where(Task.id == task_id).limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if not task.auto_review_enabled:
        resp = forbidden_error(
            f"Task {task_id} does not have automated review enabled",
            "The poster must enable auto_review_enabled when creating or updating the task",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status != "delivered":
        resp = conflict_error(
            "INVALID_STATUS",
            f"Task {task_id} is not in delivered state (status: {task.status})",
            "Automated review can only be performed on tasks in delivered status",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate deliverable
    del_result = await session.execute(
        select(Deliverable).where(
            and_(Deliverable.id == data.deliverable_id, Deliverable.task_id == task_id)
        ).limit(1)
    )
    deliverable = del_result.scalar_one_or_none()
    if not deliverable or deliverable.status != "submitted":
        resp = conflict_error(
            "DELIVERABLE_NOT_FOUND",
            f"Deliverable {data.deliverable_id} not found or not in submitted state on task {task_id}",
            "Check the task's current deliverable",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Get attempt number
    attempt_count = await session.execute(
        select(func.count()).select_from(SubmissionAttempt).where(
            and_(
                SubmissionAttempt.task_id == task_id,
                SubmissionAttempt.agent_id == deliverable.agent_id,
            )
        )
    )
    attempt_number = (attempt_count.scalar() or 0) + 1
    reviewed_at = datetime.now(timezone.utc)

    if data.verdict == "pass":
        # Complete task
        updated = await session.execute(
            update(Task)
            .where(and_(Task.id == task_id, Task.status == "delivered"))
            .values(status="completed", updated_at=datetime.now(timezone.utc))
            .returning(Task.id)
        )
        if not updated.first():
            await session.rollback()
            resp = conflict_error(
                "INVALID_STATUS",
                f"Task {task_id} is no longer in delivered state",
                "The deliverable may have already been reviewed",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)

        await session.execute(
            update(Deliverable).where(Deliverable.id == data.deliverable_id).values(status="accepted")
        )

        attempt = SubmissionAttempt(
            task_id=task_id,
            agent_id=deliverable.agent_id,
            deliverable_id=data.deliverable_id,
            attempt_number=attempt_number,
            content=deliverable.content,
            submitted_at=deliverable.submitted_at,
            review_result="pass",
            review_feedback=data.feedback,
            review_scores=data.scores,
            reviewed_at=reviewed_at,
            review_key_source=data.key_source,
            llm_model_used=data.model_used,
        )
        session.add(attempt)

        if data.key_source == "poster":
            await session.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(poster_reviews_used=Task.poster_reviews_used + 1)
            )

        await session.commit()

        # Process credits
        credit_result = None
        if task.claimed_by_agent_id:
            agent_data = await session.execute(
                select(Agent.operator_id).where(Agent.id == task.claimed_by_agent_id).limit(1)
            )
            agent_row = agent_data.first()
            if agent_row:
                credit_result = await process_task_completion(
                    session, agent_row.operator_id, task.budget_credits, task_id
                )
                await session.execute(
                    update(Agent)
                    .where(Agent.id == task.claimed_by_agent_id)
                    .values(tasks_completed=Agent.tasks_completed + 1, updated_at=datetime.now(timezone.utc))
                )
                await session.commit()

        resp = success_response({
            "task_id": task_id,
            "deliverable_id": data.deliverable_id,
            "verdict": "pass",
            "feedback": data.feedback,
            "scores": data.scores,
            "model_used": data.model_used,
            "key_source": data.key_source,
            "attempt_number": attempt_number,
            "task_status": "completed",
            "credits_paid": credit_result["payment"] if credit_result else 0,
            "platform_fee": credit_result["fee"] if credit_result else 0,
            "reviewed_at": _isoformat(reviewed_at),
        })
        return add_rate_limit_headers(resp, agent.rate_limit)

    else:  # fail
        await session.execute(
            update(Deliverable).where(Deliverable.id == data.deliverable_id).values(status="revision_requested")
        )
        await session.execute(
            update(Task).where(Task.id == task_id).values(status="in_progress", updated_at=datetime.now(timezone.utc))
        )

        attempt = SubmissionAttempt(
            task_id=task_id,
            agent_id=deliverable.agent_id,
            deliverable_id=data.deliverable_id,
            attempt_number=attempt_number,
            content=deliverable.content,
            submitted_at=deliverable.submitted_at,
            review_result="fail",
            review_feedback=data.feedback,
            review_scores=data.scores,
            reviewed_at=reviewed_at,
            review_key_source=data.key_source,
            llm_model_used=data.model_used,
        )
        session.add(attempt)

        if data.key_source == "poster":
            await session.execute(
                update(Task).where(Task.id == task_id).values(poster_reviews_used=Task.poster_reviews_used + 1)
            )

        await session.commit()

        resp = success_response({
            "task_id": task_id,
            "deliverable_id": data.deliverable_id,
            "verdict": "fail",
            "feedback": data.feedback,
            "scores": data.scores,
            "model_used": data.model_used,
            "key_source": data.key_source,
            "attempt_number": attempt_number,
            "task_status": "in_progress",
            "reviewed_at": _isoformat(reviewed_at),
        })
        return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/tasks/{task_id}/review-config ───────────────────────────────

@router.get("/{task_id:int}/review-config")
async def get_review_config(
    task_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
        resp = invalid_parameter_error("Invalid task ID", "Task IDs are positive integers.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    result = await session.execute(
        select(
            Task.id, Task.status, Task.auto_review_enabled,
            Task.poster_llm_key_encrypted, Task.poster_llm_provider,
            Task.poster_max_reviews, Task.poster_reviews_used,
            Task.claimed_by_agent_id,
        ).where(Task.id == task_id).limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if not task.auto_review_enabled:
        resp = forbidden_error(
            f"Task {task_id} does not have automated review enabled",
            "Auto review must be enabled on the task by the poster",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    from app.services.crypto import decrypt_key

    poster_key = None
    poster_under_limit = (
        task.poster_max_reviews is None or task.poster_reviews_used < task.poster_max_reviews
    )
    if task.poster_llm_key_encrypted and poster_under_limit:
        try:
            poster_key = decrypt_key(task.poster_llm_key_encrypted)
        except Exception:
            pass

    freelancer_key = None
    freelancer_provider = None
    if task.claimed_by_agent_id:
        agent_result = await session.execute(
            select(Agent.freelancer_llm_key_encrypted, Agent.freelancer_llm_provider)
            .where(Agent.id == task.claimed_by_agent_id)
            .limit(1)
        )
        claimed_agent = agent_result.first()
        if claimed_agent and claimed_agent.freelancer_llm_key_encrypted:
            try:
                freelancer_key = decrypt_key(claimed_agent.freelancer_llm_key_encrypted)
                freelancer_provider = claimed_agent.freelancer_llm_provider
            except Exception:
                pass

    resolved_key = None
    resolved_provider = None
    key_source = "none"
    if poster_key:
        resolved_key = poster_key
        resolved_provider = task.poster_llm_provider
        key_source = "poster"
    elif freelancer_key:
        resolved_key = freelancer_key
        resolved_provider = freelancer_provider
        key_source = "freelancer"

    resp = success_response({
        "task_id": task_id,
        "auto_review_enabled": task.auto_review_enabled,
        "resolved_key": resolved_key,
        "resolved_provider": resolved_provider,
        "key_source": key_source,
        "poster_provider": task.poster_llm_provider,
        "poster_max_reviews": task.poster_max_reviews,
        "poster_reviews_used": task.poster_reviews_used,
        "poster_under_limit": poster_under_limit,
        "freelancer_provider": freelancer_provider,
        "freelancer_key_available": freelancer_key is not None,
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/remarks ───────────────────────────────

class RemarkRequest(BaseModel):
    remark: str
    evaluation: dict | None = None

@router.post("/{task_id:int}/remarks")
async def add_task_remark(
    task_id: int,
    data: RemarkRequest,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Post an evaluation remark on a task. When the payload includes
    ``evaluation.questions``, each question is also stored as a separate
    ``message_type='question'`` TaskMessage with ``structured_data`` so the
    frontend renders interactive answer widgets (yes/no buttons, radio
    options, text input).
    """
    # Fetch task
    result = await session.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Append to agent_remarks JSONB array (backward compat)
    current_remarks = task.agent_remarks or []
    new_remarks = list(current_remarks)

    timestamp = datetime.utcnow().isoformat() + "Z"
    remark_entry: dict = {
        "agent_id": agent.id,
        "agent_name": agent.name,
        "remark": data.remark,
        "timestamp": timestamp,
    }
    if data.evaluation:
        remark_entry["evaluation"] = data.evaluation
    new_remarks.append(remark_entry)
    task.agent_remarks = new_remarks

    # Extract questions from evaluation (if any) and normalize IDs so responses
    # can always be mapped back to task.agent_remarks.
    questions = []
    if data.evaluation and isinstance(data.evaluation, dict):
        questions = data.evaluation.get("questions", [])
        if not isinstance(questions, list):
            questions = []
        normalized_questions = []
        for idx, q in enumerate(questions):
            if not isinstance(q, dict):
                continue
            qid = str(q.get("id") or "").strip()
            if not qid:
                qid = f"q-{idx + 1}-{uuid.uuid4().hex[:8]}"
            q["id"] = qid
            normalized_questions.append(q)
        questions = normalized_questions

        if "evaluation" in remark_entry:
            remark_entry["evaluation"] = {
                **remark_entry["evaluation"],
                "questions": questions,
            }
            # Keep task.agent_remarks in sync with normalized question IDs.
            new_remarks[-1] = remark_entry
            task.agent_remarks = new_remarks

    # Message type routing:
    # • "evaluation" — remark WITH questions (only shown in Feedback section, never in Chat)
    # • "text"       — remark WITHOUT questions (plain acknowledgment, shown in Chat)
    has_questions = bool(questions)
    feedback_msg_type = "evaluation" if has_questions else "text"

    msg = TaskMessage(
        task_id=task_id,
        sender_type="agent",
        sender_id=agent.id,
        sender_name=agent.name,
        content=data.remark,
        message_type=feedback_msg_type,
    )
    session.add(msg)
    await session.flush()
    remark_entry["message_id"] = msg.id
    new_remarks[-1] = remark_entry
    task.agent_remarks = new_remarks

    # Broadcast the feedback message
    event_broadcaster.broadcast(task.poster_id, "message_created", {
        "task_id": task_id,
        "message_id": msg.id,
        "sender_type": "agent",
        "message_type": feedback_msg_type,
    })

    # Create separate question messages with structured_data
    question_ids = []
    for q in questions:
        if not isinstance(q, dict) or not q.get("text"):
            continue

        qtype = q.get("type", "multiple_choice")
        # Map scout agent question types to structured_data format.
        # question_id links back to agent_remarks so responses can be synced.
        structured: dict = {"question_type": qtype, "question_id": q.get("id", "")}
        if qtype == "multiple_choice":
            opts = q.get("options", [])
            if len(opts) >= 2:
                structured["options"] = opts[:6]
            else:
                continue  # Skip invalid MCQ without options
        elif qtype == "yes_no":
            structured["options"] = ["Yes", "No"]
        elif qtype == "text_input":
            structured["prompt"] = q.get("placeholder", "Type your answer...")
        elif qtype == "scale":
            # Convert scale to multiple_choice for compatibility with the StructuredQuestion component
            smin = int(q.get("scale_min", 1))
            smax = int(q.get("scale_max", 5))
            labels_raw = q.get("scale_labels", ["Low", "High"])
            # Normalise: accept both list ["Low","High"] and dict {"low":"Low","high":"High"}
            if isinstance(labels_raw, dict):
                labels = list(labels_raw.values())
            elif isinstance(labels_raw, list):
                labels = labels_raw
            else:
                labels = ["Low", "High"]
            low_label = labels[0] if labels else "Low"
            high_label = labels[1] if len(labels) > 1 else "High"
            structured["question_type"] = "multiple_choice"
            structured["options"] = [
                f"{i} — {low_label}" if i == smin
                else f"{i} — {high_label}" if i == smax
                else str(i)
                for i in range(smin, smax + 1)
            ]

        q_msg = TaskMessage(
            task_id=task_id,
            sender_type="agent",
            sender_id=agent.id,
            sender_name=agent.name,
            content=q["text"],
            message_type="question",
            structured_data=structured,
            parent_id=msg.id,
        )
        session.add(q_msg)
        await session.flush()
        question_ids.append(q_msg.id)

        event_broadcaster.broadcast(task.poster_id, "message_created", {
            "task_id": task_id,
            "message_id": q_msg.id,
            "sender_type": "agent",
            "message_type": "question",
        })

    await session.commit()

    resp = success_response({
        "status": "remark added",
        "question_message_ids": question_ids,
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/messages — Agent sends message ─────────────

class AgentMessageRequest(BaseModel):
    content: str
    message_type: str = "text"
    structured_data: dict | None = None
    parent_id: int | None = None


# ─── GET /api/v1/tasks/{task_id}/messages — List task messages ────────────────

@router.get("/{task_id:int}/messages")
async def list_task_messages(
    task_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """Agent reads all messages for a task, ordered by created_at."""
    if task_id < 1:
        resp = invalid_parameter_error(
            f"Invalid task ID: {task_id}",
            "Task IDs are positive integers.",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Verify task exists
    task_result = await session.execute(
        select(Task.id).where(Task.id == task_id).limit(1)
    )
    if not task_result.first():
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Fetch all messages ordered by created_at
    msg_result = await session.execute(
        select(
            TaskMessage.id,
            TaskMessage.sender_type,
            TaskMessage.sender_name,
            TaskMessage.content,
            TaskMessage.message_type,
            TaskMessage.structured_data,
            TaskMessage.parent_id,
            TaskMessage.created_at,
        )
        .where(TaskMessage.task_id == task_id)
        .order_by(asc(TaskMessage.created_at))
    )
    messages = msg_result.all()

    data = [
        {
            "id": m.id,
            "sender_type": m.sender_type,
            "sender_name": m.sender_name,
            "content": m.content,
            "message_type": m.message_type,
            "structured_data": m.structured_data,
            "parent_id": m.parent_id,
            "created_at": _isoformat(m.created_at),
        }
        for m in messages
    ]

    resp = success_response(data)
    return add_rate_limit_headers(resp, agent.rate_limit)


@router.post("/{task_id:int}/messages")
async def agent_send_message(
    task_id: int,
    data: AgentMessageRequest,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """Agent sends a message or structured question in the task conversation."""
    result = await session.execute(
        select(Task.id, Task.status, Task.poster_id).where(Task.id == task_id).limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status in ("completed", "cancelled"):
        resp = conflict_error(
            "TASK_CLOSED",
            f"Task {task_id} is {task.status}",
            "Cannot send messages on completed/cancelled tasks",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    msg = TaskMessage(
        task_id=task_id,
        sender_type="agent",
        sender_id=agent.id,
        sender_name=agent.name,
        content=data.content,
        message_type=data.message_type,
        structured_data=data.structured_data,
        parent_id=data.parent_id,
    )
    session.add(msg)
    await session.flush()
    await session.commit()
    await session.refresh(msg)

    # Broadcast to poster
    event_broadcaster.broadcast(task.poster_id, "message_created", {
        "task_id": task_id,
        "message_id": msg.id,
        "sender_type": "agent",
        "sender_name": agent.name,
        "message_type": data.message_type,
    })

    resp = success_response({
        "id": msg.id,
        "task_id": msg.task_id,
        "sender_type": msg.sender_type,
        "sender_name": msg.sender_name,
        "content": msg.content,
        "message_type": msg.message_type,
        "created_at": _isoformat(msg.created_at),
    }, 201)
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── POST /api/v1/tasks/{task_id}/review — Reviewer agent verdict ─────────────

@router.post("/{task_id:int}/review")
async def post_review(
    task_id: int,
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """Submit an automated review verdict (PASS or FAIL) for a task's deliverable.

    Called by the Reviewer Agent after analyzing the submitted deliverable via LLM.
    - PASS: auto-completes the task and flows credits to the agent operator.
    - FAIL: marks the deliverable as revision_requested so the agent can resubmit.

    Idempotency: supported via Idempotency-Key header.
    """
    if task_id < 1:
        resp = invalid_parameter_error("Invalid task ID", "Task IDs are positive integers.")
        return add_rate_limit_headers(resp, agent.rate_limit)

    try:
        body = await request.json()
    except Exception:
        resp = validation_error(
            "Invalid JSON body",
            'Send { "deliverable_id": <int>, "verdict": "pass"|"fail", "feedback": "<text>", "scores": {}, "key_source": "poster"|"freelancer"|"none" }',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    deliverable_id = body.get("deliverable_id")
    verdict = body.get("verdict", "").lower()
    feedback = body.get("feedback", "")
    scores = body.get("scores", {})
    model_used = body.get("model_used")
    key_source = body.get("key_source", "none")

    if not isinstance(deliverable_id, int) or deliverable_id < 1:
        resp = validation_error(
            "deliverable_id is required and must be a positive integer",
            "Include deliverable_id in request body",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if verdict not in ("pass", "fail"):
        resp = validation_error(
            f'Invalid verdict: "{verdict}"',
            'verdict must be "pass" or "fail"',
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if key_source not in ("poster", "freelancer", "none"):
        key_source = "none"

    # Validate task
    result = await session.execute(
        select(Task.id, Task.status, Task.poster_id, Task.budget_credits, Task.claimed_by_agent_id,
               Task.auto_review_enabled, Task.poster_max_reviews, Task.poster_reviews_used)
        .where(Task.id == task_id)
        .limit(1)
    )
    task = result.first()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    if task.status not in ("delivered", "in_progress"):
        resp = conflict_error(
            "INVALID_STATUS",
            f"Task {task_id} is not in a reviewable state (status: {task.status})",
            "Deliverables can only be reviewed when the task is in 'delivered' or 'in_progress' state",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Validate deliverable
    del_result = await session.execute(
        select(Deliverable).where(Deliverable.id == deliverable_id).limit(1)
    )
    deliverable = del_result.scalar_one_or_none()
    if not deliverable or deliverable.task_id != task_id:
        resp = conflict_error(
            "DELIVERABLE_NOT_FOUND",
            f"Deliverable {deliverable_id} not found on task {task_id}",
            f"Use GET /api/v1/tasks/{task_id} to see deliverables",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Record SubmissionAttempt
    attempt_count_result = await session.execute(
        select(func.count()).select_from(SubmissionAttempt)
        .where(and_(
            SubmissionAttempt.task_id == task_id,
            SubmissionAttempt.agent_id == deliverable.agent_id,
        ))
    )
    attempt_number = (attempt_count_result.scalar() or 0) + 1

    attempt = SubmissionAttempt(
        task_id=task_id,
        agent_id=deliverable.agent_id,
        deliverable_id=deliverable_id,
        attempt_number=attempt_number,
        content=deliverable.content,
        review_result=verdict,
        review_feedback=feedback,
        review_scores=scores,
        review_key_source=key_source,
        llm_model_used=model_used,
    )
    session.add(attempt)
    await session.flush()

    credits_paid = 0
    platform_fee = 0
    task_status = task.status

    if verdict == "pass":
        # PASS: auto-complete task and flow credits
        updated = await session.execute(
            update(Task)
            .where(and_(Task.id == task_id, Task.status.in_(["delivered", "in_progress"])))
            .values(status="completed", updated_at=datetime.now(timezone.utc))
            .returning(Task.id)
        )
        if updated.first():
            task_status = "completed"
            await session.execute(
                update(Deliverable)
                .where(Deliverable.id == deliverable_id)
                .values(status="accepted")
            )

            # Process credits
            if task.claimed_by_agent_id:
                agent_data = await session.execute(
                    select(Agent.operator_id).where(Agent.id == task.claimed_by_agent_id).limit(1)
                )
                agent_row = agent_data.first()
                if agent_row:
                    credit_result = await process_task_completion(
                        session, agent_row.operator_id, task.budget_credits, task_id
                    )
                    credits_paid = credit_result["payment"] if credit_result else 0
                    platform_fee = credit_result["fee"] if credit_result else 0
                    await session.execute(
                        update(Agent)
                        .where(Agent.id == task.claimed_by_agent_id)
                        .values(
                            tasks_completed=Agent.tasks_completed + 1,
                            updated_at=datetime.now(timezone.utc),
                        )
                    )

            # Dispatch webhook
            if task.claimed_by_agent_id:
                dispatch_webhook_event(task.claimed_by_agent_id, "deliverable.accepted", {
                    "task_id": task_id,
                    "deliverable_id": deliverable_id,
                    "credits_paid": credits_paid,
                    "platform_fee": platform_fee,
                    "auto_reviewed": True,
                })

    else:
        # FAIL: request revision
        task_status = "in_progress"
        await session.execute(
            update(Deliverable)
            .where(Deliverable.id == deliverable_id)
            .values(status="revision_requested", revision_notes=feedback)
        )
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(status="in_progress", updated_at=datetime.now(timezone.utc))
        )

        if task.claimed_by_agent_id:
            dispatch_webhook_event(task.claimed_by_agent_id, "deliverable.revision_requested", {
                "task_id": task_id,
                "deliverable_id": deliverable_id,
                "feedback": feedback,
                "auto_reviewed": True,
            })

    # Increment poster_reviews_used if reviewer used poster's key
    if key_source == "poster":
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(poster_reviews_used=Task.poster_reviews_used + 1)
        )

    # Update attempt reviewed_at
    from datetime import timezone as tz
    await session.execute(
        update(SubmissionAttempt)
        .where(SubmissionAttempt.id == attempt.id)
        .values(reviewed_at=datetime.now(timezone.utc))
    )
    await session.commit()

    resp = success_response({
        "task_id": task_id,
        "deliverable_id": deliverable_id,
        "verdict": verdict,
        "task_status": task_status,
        "credits_paid": credits_paid,
        "platform_fee": platform_fee,
        "attempt_number": attempt_number,
        "message": (
            f"Task {task_id} completed. {credits_paid} credits paid to agent operator."
            if verdict == "pass"
            else f"Deliverable {deliverable_id} requires revision. Feedback provided."
        ),
    })
    return add_rate_limit_headers(resp, agent.rate_limit)


# ─── GET /api/v1/tasks/search — Full-text search ─────────────────────────────

@router.get("/search")
async def search_tasks(
    request: Request,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """Full-text search on task title and description.

    Results are ranked by relevance (title matches first, then description).
    Supports cursor-based pagination.
    """
    params = dict(request.query_params)
    q = params.get("q", "").strip()

    if not q:
        resp = invalid_parameter_error(
            "Search query is required",
            "Provide a ?q=<search_term> parameter. Example: GET /api/v1/tasks/search?q=REST+API",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    if len(q) > 200:
        resp = invalid_parameter_error(
            "Search query too long (max 200 characters)",
            "Shorten your search query",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Parse optional filters
    status_filter = params.get("status", "open")
    valid_statuses = ["open", "claimed", "in_progress", "delivered", "completed", "cancelled", "disputed"]
    if status_filter not in valid_statuses:
        resp = invalid_parameter_error(
            f"Invalid status: {status_filter!r}",
            f"Valid values: {', '.join(valid_statuses)}",
        )
        return add_rate_limit_headers(resp, agent.rate_limit)

    limit = 20
    try:
        limit = int(params.get("limit", 20))
        if limit < 1 or limit > 100:
            raise ValueError
    except ValueError:
        resp = invalid_parameter_error("limit must be between 1 and 100", "Use limit=20")
        return add_rate_limit_headers(resp, agent.rate_limit)

    cursor_str = params.get("cursor")

    # Build search pattern
    pattern = f"%{q}%"
    conditions = [Task.status == status_filter]

    # Title match prioritized over description
    from sqlalchemy import case, or_
    conditions.append(or_(
        Task.title.ilike(pattern),
        Task.description.ilike(pattern),
    ))

    if cursor_str:
        decoded = decode_cursor(cursor_str)
        if not decoded:
            resp = invalid_parameter_error(
                "Invalid cursor value",
                "Use the cursor value from a previous response's meta.cursor field",
            )
            return add_rate_limit_headers(resp, agent.rate_limit)
        conditions.append(Task.id < decoded["id"])

    # Relevance ordering: title match = 2, description match = 1
    relevance = case(
        (Task.title.ilike(pattern), 2),
        else_=1,
    )

    query = (
        select(
            Task.id,
            Task.title,
            Task.description,
            Task.budget_credits,
            Task.category_id,
            Category.name.label("category_name"),
            Category.slug.label("category_slug"),
            Task.status,
            User.id.label("poster_id"),
            User.name.label("poster_name"),
            Task.deadline,
            Task.max_revisions,
            Task.created_at,
        )
        .select_from(Task)
        .outerjoin(Category, Task.category_id == Category.id)
        .join(User, Task.poster_id == User.id)
        .where(and_(*conditions))
        .order_by(relevance.desc(), desc(Task.id))
        .limit(limit + 1)
    )

    result = await session.execute(query)
    rows = result.all()

    has_more = len(rows) > limit
    page_rows = rows[:limit] if has_more else rows

    # Get claims counts
    task_ids = [r.id for r in page_rows]
    claims_counts: dict[int, int] = {}
    if task_ids:
        counts_q = (
            select(TaskClaim.task_id, func.count().label("cnt"))
            .where(TaskClaim.task_id.in_(task_ids))
            .group_by(TaskClaim.task_id)
        )
        counts_result = await session.execute(counts_q)
        claims_counts = {r.task_id: r.cnt for r in counts_result.all()}

    data = [
        {
            "id": row.id,
            "title": row.title,
            "description": row.description,
            "budget_credits": row.budget_credits,
            "category": (
                {"id": row.category_id, "name": row.category_name, "slug": row.category_slug}
                if row.category_id else None
            ),
            "status": row.status,
            "poster": {"id": row.poster_id, "name": row.poster_name},
            "claims_count": claims_counts.get(row.id, 0),
            "deadline": _isoformat(row.deadline),
            "max_revisions": row.max_revisions,
            "created_at": _isoformat(row.created_at),
        }
        for row in page_rows
    ]

    next_cursor = None
    if has_more and page_rows:
        next_cursor = encode_cursor(page_rows[-1].id)

    resp = success_response(
        data,
        200,
        {"cursor": next_cursor, "has_more": has_more, "count": len(data), "query": q},
    )
    return add_rate_limit_headers(resp, agent.rate_limit)

