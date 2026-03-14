from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, update, asc, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.engine import get_db
from app.db.models import User, Task, TaskClaim, Category, Deliverable, Agent, SubmissionAttempt, CreditTransaction, TaskMessage
from app.auth.user_auth import get_current_user_id
from app.services.agent_workspaces import cleanup_workspace, sync_task_status
from app.services.marketplace import (
    MarketplaceError,
    accept_claim as marketplace_accept_claim,
    accept_deliverable as marketplace_accept_deliverable,
    answer_question as marketplace_answer_question,
    cancel_task as marketplace_cancel_task,
    create_task as marketplace_create_task,
    request_revision as marketplace_request_revision,
    send_message as marketplace_send_message,
)
from app.services.webhooks import dispatch_new_task_match, dispatch_webhook_event
from app.services.reputation import compute_reputation_tier
from app.api.events import event_broadcaster

router = APIRouter()


def _sync_workspace_status(task_id: int, status: str) -> None:
    try:
        sync_task_status(task_id, status)
    except Exception:
        pass


def _cleanup_task_workspace(task_id: int, reason: str) -> None:
    try:
        cleanup_workspace(task_id, reason=reason)
    except Exception:
        pass


def _raise_marketplace_error(error: MarketplaceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)

@router.get("/profile")
async def get_profile(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    result = await session.execute(
        select(User).where(User.id == user_id).limit(1)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "credit_balance": user.credit_balance,
        "role": user.role
    }

@router.get("/credits")
async def get_user_credits(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    from app.services.credits import get_user_transactions
    txns = await get_user_transactions(session, user_id)
    return [
        {
            "id": t.id,
            "amount": t.amount,
            "type": t.type,
            "description": t.description,
            "balance_after": t.balance_after,
            "created_at": t.created_at,
            "task_id": t.task_id
        }
        for t in txns
    ]

@router.get("/tasks")
async def get_user_tasks(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    # Fetch tasks
    result = await session.execute(
        select(Task, Category.name.label("category_name"))
        .outerjoin(Category, Task.category_id == Category.id)
        .where(Task.poster_id == user_id)
        .order_by(Task.created_at.desc())
    )
    tasks_with_cats = result.all()
    
    # Fetch claim counts for these tasks
    task_ids = [t.Task.id for t in tasks_with_cats]
    claims_counts = {}
    if task_ids:
        counts_query = (
            select(TaskClaim.task_id, func.count(TaskClaim.id))
            .where(TaskClaim.task_id.in_(task_ids))
            .group_by(TaskClaim.task_id)
        )
        counts_result = await session.execute(counts_query)
        claims_counts = {row[0]: row[1] for row in counts_result.all()}

    return [
        {
            "id": t.Task.id,
            "title": t.Task.title,
            "status": t.Task.status,
            "budget_credits": t.Task.budget_credits,
            "category_name": t.category_name,
            "created_at": t.Task.created_at,
            "deadline": t.Task.deadline,
            "claims_count": claims_counts.get(t.Task.id, 0)
        }
        for t in tasks_with_cats
    ]


@router.get("/tasks/{task_id}")
async def get_user_task_detail(
    task_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    # Fetch task
    result = await session.execute(
        select(Task, Category.name.label("category_name"))
        .outerjoin(Category, Task.category_id == Category.id)
        .where(Task.id == task_id, Task.poster_id == user_id)
        .limit(1)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = row.Task

    # Fetch claims with agent info
    claims_result = await session.execute(
        select(
            TaskClaim, Agent.name, Agent.reputation_score,
            Agent.tasks_completed, Agent.avg_rating, Agent.capabilities,
        )
        .join(Agent, TaskClaim.agent_id == Agent.id)
        .where(TaskClaim.task_id == task_id)
        .order_by(TaskClaim.created_at.desc())
    )
    claims = []
    for c in claims_result.all():
        tier = compute_reputation_tier(c.reputation_score, c.tasks_completed, c.avg_rating)
        claims.append({
            "id": c.TaskClaim.id,
            "agent_id": c.TaskClaim.agent_id,
            "agent_name": c.name,
            "proposed_credits": c.TaskClaim.proposed_credits,
            "message": c.TaskClaim.message,
            "status": c.TaskClaim.status,
            "created_at": c.TaskClaim.created_at,
            "reputation_score": c.reputation_score,
            "tasks_completed": c.tasks_completed,
            "avg_rating": c.avg_rating,
            "capabilities": c.capabilities,
            "reputation_tier": tier,
        })

    # Fetch deliverables
    deliv_result = await session.execute(
        select(Deliverable, Agent.name)
        .join(Agent, Deliverable.agent_id == Agent.id)
        .where(Deliverable.task_id == task_id)
        .order_by(Deliverable.submitted_at.desc())
    )
    deliverables = [
        {
            "id": d.Deliverable.id,
            "agent_id": d.Deliverable.agent_id,
            "agent_name": d.name,
            "content": d.Deliverable.content,
            "status": d.Deliverable.status,
            "revision_number": d.Deliverable.revision_number,
            "revision_notes": d.Deliverable.revision_notes,
            "submitted_at": d.Deliverable.submitted_at
        }
        for d in deliv_result.all()
    ]

    # Fetch submission attempts (activity)
    sub_res = await session.execute(
        select(SubmissionAttempt, Agent.name)
        .join(Agent, SubmissionAttempt.agent_id == Agent.id)
        .where(SubmissionAttempt.task_id == task_id)
        .order_by(SubmissionAttempt.submitted_at.desc())
    )
    activity = [
        {
            "id": s.SubmissionAttempt.id,
            "agent_name": s.name,
            "attempt_number": s.SubmissionAttempt.attempt_number,
            "review_result": s.SubmissionAttempt.review_result,
            "review_feedback": s.SubmissionAttempt.review_feedback,
            "submitted_at": s.SubmissionAttempt.submitted_at
        }
        for s in sub_res.all()
    ]

    msg_res = await session.execute(
        select(TaskMessage)
        .where(TaskMessage.task_id == task_id)
        .order_by(asc(TaskMessage.created_at), asc(TaskMessage.id))
        .limit(300)
    )
    messages = [
        {
            "id": m.id,
            "task_id": m.task_id,
            "sender_type": m.sender_type,
            "sender_id": m.sender_id,
            "sender_name": m.sender_name,
            "content": m.content,
            "message_type": m.message_type,
            "structured_data": m.structured_data,
            "parent_id": m.parent_id,
            "claim_id": m.claim_id,
            "is_read": m.is_read,
            "created_at": _isoformat(m.created_at),
        }
        for m in msg_res.scalars().all()
    ]

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "requirements": task.requirements,
        "budget_credits": task.budget_credits,
        "status": task.status,
        "poster_id": task.poster_id,
        "claimed_by_agent_id": task.claimed_by_agent_id,
        "max_revisions": task.max_revisions,
        "deadline": task.deadline,
        "created_at": task.created_at,
        "category_name": row.category_name,
        "agent_remarks": task.agent_remarks,
        "claims": claims,
        "deliverables": deliverables,
        "activity": activity,
        "messages": messages,
    }


@router.patch("/tasks/{task_id}")
async def update_user_task(
    task_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    from app.schemas.tasks import UpdateTaskRequest
    try:
        data = UpdateTaskRequest(**request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == user_id).limit(1)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or not yours")

    if task.status != "open":
        raise HTTPException(status_code=400, detail="Only 'open' tasks can be updated")

    if data.description:
        task.description = data.description
    if data.requirements:
        task.requirements = data.requirements

    # Clear old agent remarks so agents re-evaluate the clarified task
    task.agent_remarks = []
    task.updated_at = datetime.now(timezone.utc)
    await session.commit()
    
    return {"success": True}


@router.post("/tasks")
async def create_user_task(
    request: dict,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    from app.schemas.tasks import CreateTaskRequest
    try:
        data = CreateTaskRequest(**request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = await marketplace_create_task(
            session,
            poster_id=user_id,
            title=data.title,
            description=data.description,
            requirements=data.requirements,
            budget_credits=data.budget_credits,
            category_id=data.category_id,
            deadline=data.deadline,
            max_revisions=data.max_revisions,
            auto_review_enabled=data.auto_review_enabled,
            poster_llm_key=data.poster_llm_key,
            poster_llm_provider=data.poster_llm_provider,
            poster_max_reviews=data.poster_max_reviews,
        )
    except MarketplaceError as error:
        _raise_marketplace_error(error)

    return {"id": result["task_id"]}


@router.post("/tasks/{task_id}/accept-claim")
async def user_accept_claim(
    task_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    claim_id = request.get("claim_id")
    if not claim_id:
        raise HTTPException(status_code=400, detail="claim_id is required")

    try:
        await marketplace_accept_claim(
            session,
            task_id=task_id,
            claim_id=claim_id,
            poster_id=user_id,
        )
    except MarketplaceError as error:
        _raise_marketplace_error(error)

    return {"success": True}


@router.post("/tasks/{task_id}/accept-deliverable")
async def user_accept_deliverable(
    task_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    deliverable_id = request.get("deliverable_id")
    if not deliverable_id:
        raise HTTPException(status_code=400, detail="deliverable_id is required")

    try:
        await marketplace_accept_deliverable(
            session,
            task_id=task_id,
            deliverable_id=deliverable_id,
            poster_id=user_id,
        )
    except MarketplaceError as error:
        _raise_marketplace_error(error)

    return {"success": True}


@router.post("/tasks/{task_id}/request-revision")
async def user_request_revision(
    task_id: int,
    request: dict,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    deliverable_id = request.get("deliverable_id")
    notes = request.get("notes", "")
    if not deliverable_id:
        raise HTTPException(status_code=400, detail="deliverable_id is required")

    try:
        await marketplace_request_revision(
            session,
            task_id=task_id,
            deliverable_id=deliverable_id,
            poster_id=user_id,
            notes=notes,
        )
    except MarketplaceError as error:
        _raise_marketplace_error(error)

    return {"success": True}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    request: dict | None = None,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Cancel a task. Can be used on open, claimed, in_progress, or delivered tasks.
    Refunds escrowed credits if the task was already claimed.
    """
    try:
        await marketplace_cancel_task(
            session,
            task_id=task_id,
            poster_id=user_id,
        )
    except MarketplaceError as error:
        _raise_marketplace_error(error)

    return {"success": True, "message": f"Task #{task_id} has been cancelled"}


# ─── Task Messages (Conversation) ────────────────────────────────────────────

def _isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


class SendMessageRequest(BaseModel):
    content: str
    message_type: str = "text"
    parent_id: int | None = None
    structured_data: dict | None = None


class RespondToQuestionRequest(BaseModel):
    response: str
    option_index: int | None = None


@router.get("/tasks/{task_id}/messages")
async def get_task_messages(
    task_id: int,
    limit: int = Query(50, ge=1, le=200),
    before: int | None = Query(None),
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Fetch conversation messages for a task (paginated, newest last)."""
    # Verify ownership
    result = await session.execute(
        select(Task.id).where(Task.id == task_id, Task.poster_id == user_id).limit(1)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Task not found")

    conditions = [TaskMessage.task_id == task_id]
    if before:
        conditions.append(TaskMessage.id < before)

    msg_result = await session.execute(
        select(TaskMessage)
        .where(*conditions)
        .order_by(desc(TaskMessage.id))
        .limit(limit)
    )
    messages = msg_result.scalars().all()
    messages = list(reversed(messages))  # chronological order

    # Collect agent sender_ids to fetch reputation data
    agent_ids = {m.sender_id for m in messages if m.sender_type == "agent" and m.sender_id}
    agent_tiers = {}
    if agent_ids:
        agents_result = await session.execute(
            select(Agent.id, Agent.reputation_score, Agent.tasks_completed, Agent.avg_rating)
            .where(Agent.id.in_(agent_ids))
        )
        for a in agents_result.all():
            agent_tiers[a.id] = compute_reputation_tier(a.reputation_score, a.tasks_completed, a.avg_rating)

    data = []
    for m in messages:
        msg_data = {
            "id": m.id,
            "task_id": m.task_id,
            "sender_type": m.sender_type,
            "sender_id": m.sender_id,
            "sender_name": m.sender_name,
            "content": m.content,
            "message_type": m.message_type,
            "structured_data": m.structured_data,
            "parent_id": m.parent_id,
            "claim_id": m.claim_id,
            "is_read": m.is_read,
            "created_at": _isoformat(m.created_at),
        }
        if m.sender_type == "agent" and m.sender_id in agent_tiers:
            msg_data["reputation_tier"] = agent_tiers[m.sender_id]
        data.append(msg_data)

    has_more = len(messages) == limit
    return {
        "messages": data,
        "has_more": has_more,
        "cursor": messages[0].id if messages and has_more else None,
    }


@router.post("/tasks/{task_id}/messages")
async def send_task_message(
    task_id: int,
    request: SendMessageRequest,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Poster sends a message in the task conversation."""
    user_result = await session.execute(
        select(User.name).where(User.id == user_id).limit(1)
    )
    user_name = user_result.scalar_one_or_none() or "Poster"

    try:
        result = await marketplace_send_message(
            session,
            task_id=task_id,
            sender_type="poster",
            sender_id=user_id,
            sender_name=user_name,
            content=request.content,
            message_type=request.message_type,
            parent_id=request.parent_id,
            structured_data=request.structured_data,
        )
    except MarketplaceError as error:
        _raise_marketplace_error(error)

    msg = await session.get(TaskMessage, result["message_id"])

    return {
        "id": msg.id,
        "task_id": msg.task_id,
        "sender_type": msg.sender_type,
        "sender_name": msg.sender_name,
        "content": msg.content,
        "message_type": msg.message_type,
        "created_at": _isoformat(msg.created_at),
    }


@router.patch("/tasks/{task_id}/messages/{message_id}/respond")
async def respond_to_question(
    task_id: int,
    message_id: int,
    request: RespondToQuestionRequest,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Poster responds to a structured question from an agent."""
    user_result = await session.execute(
        select(User.name).where(User.id == user_id).limit(1)
    )
    user_name = user_result.scalar_one_or_none() or "Poster"

    try:
        result = await marketplace_answer_question(
            session,
            task_id=task_id,
            message_id=message_id,
            poster_id=user_id,
            poster_name=user_name,
            response=request.response,
            option_index=request.option_index,
        )
    except MarketplaceError as error:
        _raise_marketplace_error(error)

    return {"success": True, "reply_id": result["message_id"]}


class EvaluationAnswerItem(BaseModel):
    question_id: str
    answer: str


class SubmitEvaluationAnswersRequest(BaseModel):
    agent_id: int
    answers: list[EvaluationAnswerItem]


@router.post("/tasks/{task_id}/remarks/answers")
async def submit_evaluation_answers(
    task_id: int,
    data: SubmitEvaluationAnswersRequest,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
):
    """Poster submits bulk answers to an agent's evaluation questions.
    Updates agent_remarks JSONB so the scout detects answers on next poll.
    Also posts a text reply message so conv_messages check fires too.
    """
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == user_id).limit(1)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    answer_map = {a.question_id: a.answer for a in data.answers}
    current_remarks = list(task.agent_remarks or [])
    updated = False
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for remark in reversed(current_remarks):
        if remark.get("agent_id") != data.agent_id:
            continue
        eval_data = remark.get("evaluation")
        if not eval_data or not eval_data.get("questions"):
            continue
        remark_updated = False
        for idx, q in enumerate(eval_data["questions"]):
            qid = str(q.get("id") or "").strip() or f"q-{idx + 1}"
            if qid in answer_map and not q.get("answer"):
                q["id"] = qid
                q["answer"] = answer_map[qid]
                q["answered_at"] = now_iso
                remark_updated = True
        if remark_updated:
            updated = True
            break

    # Keep question message structured_data in sync so chat/threads show answered
    # state immediately without waiting for a re-run.
    question_result = await session.execute(
        select(TaskMessage).where(
            TaskMessage.task_id == task_id,
            TaskMessage.sender_type == "agent",
            TaskMessage.message_type == "question",
            TaskMessage.sender_id == data.agent_id,
        )
    )
    question_msgs = question_result.scalars().all()
    question_msgs.sort(key=lambda m: (m.created_at, m.id))
    for idx, msg in enumerate(question_msgs):
        structured = dict(msg.structured_data or {})
        qid = str(structured.get("question_id") or "").strip() or f"q-{idx + 1}"
        if qid not in answer_map:
            continue
        if structured.get("response"):
            continue
        structured["question_id"] = qid
        structured["response"] = answer_map[qid]
        structured["responded_at"] = now_iso
        msg.structured_data = structured
        updated = True

    if not updated:
        raise HTTPException(status_code=404, detail="No matching questions found")

    # Save updated remarks and bump updated_at so scout re-evaluates
    task.agent_remarks = current_remarks
    task.updated_at = datetime.now(timezone.utc)

    # Post a short text message so scout's conv_messages check also detects the answers
    user_result = await session.execute(
        select(User.name).where(User.id == user_id).limit(1)
    )
    user_name = user_result.scalar_one_or_none() or "Poster"

    answers_text = "; ".join(f"{a.answer}" for a in data.answers[:5])
    reply_msg = TaskMessage(
        task_id=task_id,
        sender_type="poster",
        sender_id=user_id,
        sender_name=user_name,
        content=f"Answers submitted: {answers_text}",
        message_type="text",
    )
    session.add(reply_msg)
    await session.flush()
    await session.commit()

    event_broadcaster.broadcast(user_id, "message_created", {
        "task_id": task_id,
        "message_id": reply_msg.id,
        "sender_type": "poster",
        "message_type": "text",
    })

    return {"success": True}


