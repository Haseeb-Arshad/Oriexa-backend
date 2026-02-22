"""All /api/v1/tasks/* endpoints — port of Oriexa/src/app/api/v1/tasks/"""

from datetime import datetime, timezone

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

@router.get("/{task_id:int}")
async def get_task(
    task_id: int,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    if task_id < 1:
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
        .where(Task.id == task_id)
        .limit(1)
    )
    task = result.first()

    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Claims count
    claims_result = await session.execute(
        select(func.count()).select_from(TaskClaim).where(TaskClaim.task_id == task_id)
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
        ).where(Deliverable.task_id == task_id)
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
        .where(and_(Deliverable.task_id == task_id, Deliverable.agent_id == agent.id))
        .order_by(desc(Deliverable.revision_number))
        .limit(1)
    )
    latest_rev = latest.scalar_one_or_none()
    next_revision = (latest_rev + 1) if latest_rev else 1

    # Check max revisions
    if next_revision > task.max_revisions + 1:
        resp = max_revisions_error(task_id, next_revision - 1, task.max_revisions + 1)
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
        resp = conflict_error(
            "MAX_REVISIONS",
            f"Maximum revisions reached ({deliverable.revision_number} of {task.max_revisions + 1} deliveries)",
            "No more revisions allowed. Accept or reject the deliverable.",
        )
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

@router.post("/{task_id:int}/remarks")
async def add_task_remark(
    task_id: int,
    data: RemarkRequest,
    agent: AgentContext = Depends(get_current_agent),
    session: AsyncSession = Depends(get_db),
):
    """
    Allow an agent to post a remark/rejection reason for a task they evaluated but didn't claim.
    """
    # Fetch task
    result = await session.execute(
        select(Task).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        resp = task_not_found_error(task_id)
        return add_rate_limit_headers(resp, agent.rate_limit)

    # Append to JSONB array using Python list logic
    current_remarks = task.agent_remarks or []
    # Using list() guarantees we're modifying a new object that SQLAlchemy will flush
    new_remarks = list(current_remarks)
    
    new_remarks.append({
        "agent_id": agent.id,
        "agent_name": agent.name,
        "remark": data.remark,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    })
    
    task.agent_remarks = new_remarks
    await session.commit()

    resp = success_response({"status": "remark added"})
    return add_rate_limit_headers(resp, agent.rate_limit)
