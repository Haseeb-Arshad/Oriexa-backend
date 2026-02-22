from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.engine import get_db
from app.db.models import User, Task, TaskClaim, Category, Deliverable, Agent, SubmissionAttempt, CreditTransaction
from app.auth.user_auth import get_current_user_id
from app.services.webhooks import dispatch_new_task_match, dispatch_webhook_event

router = APIRouter()

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
        select(TaskClaim, Agent.name, Agent.reputation_score, Agent.tasks_completed)
        .join(Agent, TaskClaim.agent_id == Agent.id)
        .where(TaskClaim.task_id == task_id)
        .order_by(TaskClaim.created_at.desc())
    )
    claims = [
        {
            "id": c.TaskClaim.id,
            "agent_id": c.TaskClaim.agent_id,
            "agent_name": c.name,
            "proposed_credits": c.TaskClaim.proposed_credits,
            "message": c.TaskClaim.message,
            "status": c.TaskClaim.status,
            "created_at": c.TaskClaim.created_at,
            "reputation_score": c.reputation_score,
            "tasks_completed": c.tasks_completed
        }
        for c in claims_result.all()
    ]

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
        "activity": activity
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
    from app.services.crypto import encrypt_key
    try:
        data = CreateTaskRequest(**request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Encrect LLM key if provided
    poster_llm_key_encrypted = None
    if data.poster_llm_key:
        try:
            poster_llm_key_encrypted = encrypt_key(data.poster_llm_key)
        except Exception:
            pass

    task = Task(
        poster_id=user_id,
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
    await session.flush() # Get task.id

    # Escrow credits
    from app.services.credits import deduct_credits
    await deduct_credits(
        session, 
        user_id, 
        task.budget_credits, 
        "payment", 
        f"Escrow for task: {task.title}", 
        task.id
    )

    await session.commit()
    await session.refresh(task)

    # Dispatch webhooks: notify agents about new task match
    dispatch_new_task_match(task.id, task.category_id, {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "budget_credits": task.budget_credits,
        "category_id": task.category_id,
        "created_at": task.created_at.isoformat().replace("+00:00", "Z"),
    })

    return {"id": task.id}


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

    # Verify task ownership and status
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == user_id).limit(1)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or not yours")
    if task.status != "open":
        raise HTTPException(status_code=400, detail=f"Task is not open (status: {task.status})")

    # Verify claim
    claim_result = await session.execute(
        select(TaskClaim).where(
            TaskClaim.id == claim_id,
            TaskClaim.task_id == task_id,
            TaskClaim.status == "pending"
        ).limit(1)
    )
    claim = claim_result.scalar_one_or_none()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found or not pending")

    # Accept this claim
    await session.execute(
        update(TaskClaim).where(TaskClaim.id == claim_id).values(status="accepted")
    )
    # Reject others
    await session.execute(
        update(TaskClaim)
        .where(TaskClaim.task_id == task_id, TaskClaim.id != claim_id, TaskClaim.status == "pending")
        .values(status="rejected")
    )
    # Update task status to 'claimed' so no more claims can be submitted
    task.status = "claimed"
    task.claimed_by_agent_id = claim.agent_id
    task.updated_at = datetime.now(timezone.utc)
    await session.commit()

    # Dispatch webhooks
    dispatch_webhook_event(claim.agent_id, "claim.accepted", {
        "task_id": task_id,
        "claim_id": claim_id,
        "agent_id": claim.agent_id,
    })

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

    # Verify task
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == user_id).limit(1)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or not yours")
    if task.status != "delivered":
        raise HTTPException(status_code=400, detail=f"Task is not in delivered state (status: {task.status})")

    # Validate deliverable
    del_result = await session.execute(
        select(Deliverable).where(Deliverable.id == deliverable_id, Deliverable.task_id == task_id).limit(1)
    )
    deliverable = del_result.scalar_one_or_none()
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    # Accept deliverable
    deliverable.status = "accepted"
    task.status = "completed"
    task.updated_at = datetime.now(timezone.utc)

    # Process credits
    from app.services.credits import process_task_completion
    if task.claimed_by_agent_id:
        agent_data = await session.execute(
            select(Agent).where(Agent.id == task.claimed_by_agent_id).limit(1)
        )
        agent = agent_data.scalar_one_or_none()
        if agent:
            await process_task_completion(session, agent.operator_id, task.budget_credits, task_id)
            agent.tasks_completed += 1
            agent.updated_at = datetime.now(timezone.utc)

    await session.commit()

    # Dispatch webhook
    if task.claimed_by_agent_id:
        dispatch_webhook_event(task.claimed_by_agent_id, "deliverable.accepted", {
            "task_id": task_id,
            "deliverable_id": deliverable_id,
            "credits_paid": task.budget_credits,
        })

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

    # Verify task
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == user_id).limit(1)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or not yours")
    if task.status != "delivered":
        raise HTTPException(status_code=400, detail="Task is not in delivered state")

    # Validate deliverable
    del_result = await session.execute(
        select(Deliverable).where(Deliverable.id == deliverable_id, Deliverable.task_id == task_id).limit(1)
    )
    deliverable = del_result.scalar_one_or_none()
    if not deliverable:
        raise HTTPException(status_code=404, detail="Deliverable not found")

    if deliverable.revision_number >= task.max_revisions + 1:
        raise HTTPException(status_code=400, detail="Maximum revisions reached")

    # Mark deliverable as revision_requested
    deliverable.status = "revision_requested"
    deliverable.revision_notes = notes
    
    # Move task back to in_progress
    task.status = "in_progress"
    task.updated_at = datetime.now(timezone.utc)

    await session.commit()

    # Dispatch webhook
    if task.claimed_by_agent_id:
        dispatch_webhook_event(task.claimed_by_agent_id, "deliverable.revision_requested", {
            "task_id": task_id,
            "deliverable_id": deliverable_id,
            "revision_notes": notes,
        })

    return {"success": True}


@router.get("/agents")
async def get_user_agents(
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    result = await session.execute(
        select(Agent, User.name.label("operator_name"), User.email.label("operator_email"))
        .join(User, Agent.operator_id == User.id)
        .where(Agent.operator_id == user_id)
        .order_by(Agent.created_at.desc())
    )
    agents_list = []
    for row in result:
        a = row.Agent
        agents_list.append({
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "capabilities": a.capabilities,
            "status": a.status,
            "api_key_prefix": a.api_key_prefix,
            "reputation_score": a.reputation_score,
            "tasks_completed": a.tasks_completed,
            "created_at": a.created_at,
            "operator_name": row.operator_name,
            "operator_email": row.operator_email
        })
    return agents_list


@router.post("/agents")
async def register_user_agent(
    request: dict,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    from app.services.auth import generate_api_key
    from app.services.credits import grant_agent_bonus

    name = request.get("name")
    description = request.get("description")
    capabilities = request.get("capabilities", "")
    
    if isinstance(capabilities, str):
        caps = [s.strip() for s in capabilities.split(",") if s.strip()]
    else:
        caps = capabilities

    if not name:
        raise HTTPException(status_code=400, detail="Agent name is required")
    if not description or len(description) < 10:
        raise HTTPException(status_code=400, detail="Description must be at least 10 characters")

    raw_key, hashed, prefix = generate_api_key()

    agent = Agent(
        operator_id=user_id,
        name=name,
        description=description,
        capabilities=caps,
        api_key_hash=hashed,
        api_key_prefix=prefix,
        status="active"
    )
    session.add(agent)
    await session.flush()

    # Grant agent bonus
    await grant_agent_bonus(session, user_id)
    
    await session.commit()
    await session.refresh(agent)

    return {"agent_id": agent.id, "api_key": raw_key}


@router.post("/agents/{agent_id}/regenerate-key")
async def regenerate_user_agent_key(
    agent_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    from app.services.auth import generate_api_key

    result = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.operator_id == user_id).limit(1)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or not yours")

    raw_key, hashed, prefix = generate_api_key()
    
    agent.api_key_hash = hashed
    agent.api_key_prefix = prefix
    agent.updated_at = datetime.now(timezone.utc)
    
    await session.commit()
    return {"api_key": raw_key}


@router.post("/agents/{agent_id}/revoke-key")
async def revoke_user_agent_key(
    agent_id: int,
    user_id: int = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db)
):
    result = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.operator_id == user_id).limit(1)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found or not yours")

    agent.api_key_hash = None
    agent.api_key_prefix = None
    agent.updated_at = datetime.now(timezone.utc)
    
    await session.commit()
    return {"success": True}
