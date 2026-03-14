from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.events import event_broadcaster
from app.db.models import Agent, Deliverable, Task, TaskClaim, TaskMessage
from app.orchestrator.legacy_bridge import ensure_legacy_execution
from app.services.agent_workspaces import cleanup_workspace, sync_task_status
from app.services.credits import _add_credits, deduct_credits, process_task_completion
from app.services.crypto import encrypt_key
from app.services.external_events import agent_channel, external_event_broadcaster, user_channel
from app.services.external_workflow import build_external_event_payload
from app.services.reputation import compute_reputation_tier
from app.services.webhooks import dispatch_new_task_match, dispatch_webhook_event


@dataclass
class MarketplaceError(Exception):
    status_code: int
    code: str
    message: str
    suggestion: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_deadline(deadline: str | None) -> datetime | None:
    if not deadline:
        return None
    return datetime.fromisoformat(deadline.replace("Z", "+00:00"))


def _sync_workspace_status(task_id: int, status: str) -> None:
    try:
        sync_task_status(task_id, status)
    except Exception:
        pass


def _cleanup_workspace(task_id: int, reason: str) -> None:
    try:
        cleanup_workspace(task_id, reason=reason)
    except Exception:
        pass


async def _resolve_primary_agent_id(session: AsyncSession, operator_id: int) -> int | None:
    result = await session.execute(
        select(Agent.id)
        .where(Agent.operator_id == operator_id, Agent.status == "active")
        .order_by(Agent.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _dispatch_operator_webhook(
    session: AsyncSession,
    *,
    operator_id: int,
    event: str,
    data: dict[str, Any],
    preferred_agent_id: int | None = None,
) -> None:
    agent_id = preferred_agent_id or await _resolve_primary_agent_id(session, operator_id)
    if agent_id is not None:
        dispatch_webhook_event(agent_id, event, data)


async def _emit_external_update(
    session: AsyncSession,
    *,
    task_id: int,
    event_type: str,
    poster_id: int,
    agent_ids: list[int] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = await build_external_event_payload(session, task_id)
    if payload is None:
        return
    if extra:
        payload.update(extra)

    channels = [user_channel(poster_id)]
    for agent_id in agent_ids or []:
        if agent_id:
            channels.append(agent_channel(agent_id))

    external_event_broadcaster.broadcast_many(
        list(dict.fromkeys(channels)),
        event_type,
        payload,
    )


async def get_task_with_access(
    session: AsyncSession,
    *,
    task_id: int,
    poster_id: int | None = None,
    worker_agent_id: int | None = None,
    allow_open_marketplace: bool = False,
) -> Task:
    result = await session.execute(select(Task).where(Task.id == task_id).limit(1))
    task = result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found.",
            "List tasks first and use a valid task_id.",
        )

    if poster_id is not None and task.poster_id == poster_id:
        return task

    if worker_agent_id is not None:
        if task.claimed_by_agent_id == worker_agent_id:
            return task
        claim_result = await session.execute(
            select(TaskClaim.id)
            .where(TaskClaim.task_id == task_id, TaskClaim.agent_id == worker_agent_id)
            .limit(1)
        )
        if claim_result.scalar_one_or_none() is not None:
            return task
        if allow_open_marketplace and task.status == "open":
            return task

    raise MarketplaceError(
        403,
        "TASK_FORBIDDEN",
        f"You do not have access to task {task_id}.",
        "Use a task you posted, claimed, or can see in the marketplace.",
    )


async def list_task_ids_for_view(
    session: AsyncSession,
    *,
    user_id: int,
    agent_id: int,
    view: str,
    status: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[int], str | None, bool]:
    from app.api.pagination import decode_cursor, encode_cursor

    conditions = []
    if status:
        conditions.append(Task.status == status)

    if view == "mine":
        conditions.append(Task.poster_id == user_id)
    elif view == "claimed":
        conditions.append(Task.claimed_by_agent_id == agent_id)
    elif view == "inbox":
        claim_subquery = select(TaskClaim.task_id).where(TaskClaim.agent_id == agent_id)
        conditions.append(
            (Task.poster_id == user_id) | (Task.claimed_by_agent_id == agent_id) | (Task.id.in_(claim_subquery))
        )
    else:
        if status is None:
            conditions.append(Task.status == "open")

    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is None:
            raise MarketplaceError(
                400,
                "INVALID_CURSOR",
                "The cursor value is invalid.",
                "Use the cursor returned by the previous list_tasks response.",
            )
        conditions.append(Task.id < decoded["id"])

    result = await session.execute(
        select(Task.id)
        .where(*conditions)
        .order_by(Task.id.desc())
        .limit(limit + 1)
    )
    rows = [row.id for row in result.all()]
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    next_cursor = encode_cursor(page[-1]) if has_more and page else None
    return page, next_cursor, has_more


async def create_task(
    session: AsyncSession,
    *,
    poster_id: int,
    title: str,
    description: str,
    budget_credits: int,
    category_id: int | None = None,
    requirements: str | None = None,
    deadline: str | None = None,
    max_revisions: int | None = None,
    auto_review_enabled: bool = False,
    poster_llm_key: str | None = None,
    poster_llm_provider: str | None = None,
    poster_max_reviews: int | None = None,
    poster_notify_agent_id: int | None = None,
) -> dict[str, Any]:
    poster_llm_key_encrypted = None
    if poster_llm_key:
        try:
            poster_llm_key_encrypted = encrypt_key(poster_llm_key)
        except Exception:
            poster_llm_key_encrypted = None

    task = Task(
        poster_id=poster_id,
        title=title,
        description=description,
        requirements=requirements,
        budget_credits=budget_credits,
        category_id=category_id,
        deadline=_parse_deadline(deadline),
        max_revisions=max_revisions if max_revisions is not None else 2,
        status="open",
        auto_review_enabled=auto_review_enabled,
        poster_llm_key_encrypted=poster_llm_key_encrypted,
        poster_llm_provider=poster_llm_provider,
        poster_max_reviews=poster_max_reviews,
        updated_at=_now(),
    )
    session.add(task)
    await session.flush()
    await session.commit()
    await session.refresh(task)

    dispatch_new_task_match(
        task.id,
        task.category_id,
        {
            "id": task.id,
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "budget_credits": task.budget_credits,
            "category_id": task.category_id,
            "created_at": task.created_at.isoformat().replace("+00:00", "Z"),
        },
    )
    event_broadcaster.broadcast(
        poster_id,
        "task_created",
        {"task_id": task.id, "title": task.title, "status": task.status},
    )
    await _dispatch_operator_webhook(
        session,
        operator_id=poster_id,
        event="task.updated",
        data={"task_id": task.id, "status": task.status, "title": task.title},
        preferred_agent_id=poster_notify_agent_id,
    )
    await _emit_external_update(
        session,
        task_id=task.id,
        event_type="task.updated",
        poster_id=poster_id,
        agent_ids=[poster_notify_agent_id] if poster_notify_agent_id else [],
        extra={"task_id": task.id},
    )
    return {"task_id": task.id}


async def create_claim(
    session: AsyncSession,
    *,
    task_id: int,
    agent_id: int,
    agent_name: str,
    proposed_credits: int,
    message: str | None,
) -> dict[str, Any]:
    task_result = await session.execute(select(Task).where(Task.id == task_id).limit(1))
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found.",
            "List open tasks first and claim a valid task.",
        )
    if task.status != "open":
        raise MarketplaceError(
            409,
            "TASK_NOT_OPEN",
            f"Task {task_id} is not open.",
            "Claim only tasks whose status is open.",
        )
    if proposed_credits > task.budget_credits:
        raise MarketplaceError(
            422,
            "INVALID_CREDITS",
            f"proposed_credits ({proposed_credits}) exceeds the task budget ({task.budget_credits}).",
            "Use a proposal at or below the task budget.",
        )

    duplicate = await session.execute(
        select(TaskClaim.id)
        .where(
            TaskClaim.task_id == task_id,
            TaskClaim.agent_id == agent_id,
            TaskClaim.status.in_(["pending", "accepted"]),
        )
        .limit(1)
    )
    if duplicate.scalar_one_or_none() is not None:
        raise MarketplaceError(
            409,
            "DUPLICATE_CLAIM",
            f"Your agent already has an active claim on task {task_id}.",
            "List your claimed or pending tasks instead of creating another claim.",
        )

    claim = TaskClaim(
        task_id=task_id,
        agent_id=agent_id,
        proposed_credits=proposed_credits,
        message=message,
        status="pending",
    )
    session.add(claim)
    await session.flush()

    task_message = TaskMessage(
        task_id=task_id,
        sender_type="agent",
        sender_id=agent_id,
        sender_name=agent_name,
        content=message or f"Proposed {proposed_credits} credits",
        message_type="claim_proposal",
        claim_id=claim.id,
        structured_data={"proposed_credits": proposed_credits, "message": message},
    )
    session.add(task_message)
    task.updated_at = _now()
    await session.commit()
    await session.refresh(claim)
    await session.refresh(task_message)

    event_broadcaster.broadcast(
        task.poster_id,
        "claim_created",
        {
            "task_id": task_id,
            "claim_id": claim.id,
            "agent_id": agent_id,
            "proposed_credits": proposed_credits,
        },
    )
    event_broadcaster.broadcast(
        task.poster_id,
        "message_created",
        {
            "task_id": task_id,
            "message_id": task_message.id,
            "sender_type": "agent",
            "message_type": "claim_proposal",
        },
    )
    await _dispatch_operator_webhook(
        session,
        operator_id=task.poster_id,
        event="claim.created",
        data={
            "task_id": task_id,
            "claim_id": claim.id,
            "agent_id": agent_id,
            "proposed_credits": proposed_credits,
        },
    )
    await _dispatch_operator_webhook(
        session,
        operator_id=task.poster_id,
        event="message.created",
        data={
            "task_id": task_id,
            "message_id": task_message.id,
            "sender_type": "agent",
            "message_type": "claim_proposal",
        },
    )
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="claim.created",
        poster_id=task.poster_id,
        agent_ids=[agent_id],
        extra={"claim_id": claim.id, "message_id": task_message.id},
    )
    return {"task_id": task_id, "claim_id": claim.id, "message_id": task_message.id}


async def accept_claim(
    session: AsyncSession,
    *,
    task_id: int,
    claim_id: int,
    poster_id: int,
    poster_notify_agent_id: int | None = None,
) -> dict[str, Any]:
    task_result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == poster_id).limit(1)
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found for this poster.",
            "Use a task you posted.",
        )
    if task.status != "open":
        raise MarketplaceError(
            409,
            "TASK_NOT_OPEN",
            f"Task {task_id} is not open.",
            "Only open tasks can accept claims.",
        )

    claim_result = await session.execute(
        select(TaskClaim)
        .where(
            TaskClaim.id == claim_id,
            TaskClaim.task_id == task_id,
            TaskClaim.status == "pending",
        )
        .limit(1)
    )
    claim = claim_result.scalar_one_or_none()
    if claim is None:
        raise MarketplaceError(
            404,
            "CLAIM_NOT_FOUND",
            f"Claim {claim_id} was not found or is no longer pending.",
            "List pending claims first and accept one of them.",
        )

    await deduct_credits(
        session,
        poster_id,
        task.budget_credits,
        "payment",
        f"Escrow for task: {task.title}",
        task.id,
    )
    await session.execute(update(TaskClaim).where(TaskClaim.id == claim.id).values(status="accepted"))
    await session.execute(
        update(TaskClaim)
        .where(TaskClaim.task_id == task_id, TaskClaim.id != claim.id, TaskClaim.status == "pending")
        .values(status="rejected")
    )
    task.status = "claimed"
    task.claimed_by_agent_id = claim.agent_id
    task.updated_at = _now()
    await session.flush()
    await ensure_legacy_execution(
        session,
        task_id=task.id,
        task_snapshot={
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "requirements": task.requirements,
            "budget_credits": task.budget_credits,
            "category_id": task.category_id,
            "status": task.status,
        },
    )
    await session.commit()
    _sync_workspace_status(task_id, "claimed")

    dispatch_webhook_event(
        claim.agent_id,
        "claim.accepted",
        {"task_id": task_id, "claim_id": claim.id, "agent_id": claim.agent_id},
    )
    await _dispatch_operator_webhook(
        session,
        operator_id=poster_id,
        event="task.updated",
        data={"task_id": task_id, "status": "claimed", "claim_id": claim.id},
        preferred_agent_id=poster_notify_agent_id,
    )
    event_broadcaster.broadcast(
        poster_id,
        "claim_updated",
        {"task_id": task_id, "claim_id": claim.id, "status": "accepted"},
    )
    event_broadcaster.broadcast(
        poster_id,
        "task_updated",
        {"task_id": task_id, "status": "claimed"},
    )
    agent_ids = [claim.agent_id]
    if poster_notify_agent_id:
        agent_ids.append(poster_notify_agent_id)
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="claim.accepted",
        poster_id=poster_id,
        agent_ids=agent_ids,
        extra={"claim_id": claim.id},
    )
    return {"task_id": task_id, "claim_id": claim.id, "agent_id": claim.agent_id}


async def submit_deliverable(
    session: AsyncSession,
    *,
    task_id: int,
    agent_id: int,
    content: str,
) -> dict[str, Any]:
    task_result = await session.execute(select(Task).where(Task.id == task_id).limit(1))
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found.",
            "Use a valid claimed task.",
        )
    if task.status not in ("claimed", "in_progress"):
        raise MarketplaceError(
            409,
            "INVALID_STATUS",
            f"Task {task_id} cannot accept a deliverable while it is {task.status}.",
            "Submit deliverables only on claimed or in_progress tasks.",
        )
    if task.claimed_by_agent_id != agent_id:
        raise MarketplaceError(
            403,
            "TASK_NOT_CLAIMED_BY_ACTOR",
            f"Task {task_id} is not claimed by this agent.",
            "Only the accepted worker can submit deliverables.",
        )

    latest = await session.execute(
        select(Deliverable.revision_number)
        .where(Deliverable.task_id == task_id, Deliverable.agent_id == agent_id)
        .order_by(desc(Deliverable.revision_number))
        .limit(1)
    )
    latest_revision = latest.scalar_one_or_none()
    next_revision = (latest_revision + 1) if latest_revision is not None else 1

    if next_revision > task.max_revisions + 1:
        raise MarketplaceError(
            409,
            "MAX_REVISIONS_REACHED",
            f"Task {task_id} already used all allowed revision attempts.",
            "The poster must accept the last deliverable or increase max_revisions.",
        )

    deliverable = Deliverable(
        task_id=task_id,
        agent_id=agent_id,
        content=content,
        status="submitted",
        revision_number=next_revision,
    )
    session.add(deliverable)
    task.status = "delivered"
    task.updated_at = _now()
    await session.flush()
    await session.commit()
    await session.refresh(deliverable)
    _sync_workspace_status(task_id, "delivered")

    event_broadcaster.broadcast(
        task.poster_id,
        "deliverable_submitted",
        {
            "task_id": task_id,
            "deliverable_id": deliverable.id,
            "revision_number": deliverable.revision_number,
        },
    )
    event_broadcaster.broadcast(
        task.poster_id,
        "task_updated",
        {"task_id": task_id, "status": "delivered"},
    )
    await _dispatch_operator_webhook(
        session,
        operator_id=task.poster_id,
        event="deliverable.submitted",
        data={
            "task_id": task_id,
            "deliverable_id": deliverable.id,
            "agent_id": agent_id,
            "revision_number": deliverable.revision_number,
        },
    )
    dispatch_webhook_event(
        agent_id,
        "task.updated",
        {"task_id": task_id, "status": "delivered", "deliverable_id": deliverable.id},
    )
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="deliverable.submitted",
        poster_id=task.poster_id,
        agent_ids=[agent_id],
        extra={"deliverable_id": deliverable.id},
    )
    return {"task_id": task_id, "deliverable_id": deliverable.id}


async def request_revision(
    session: AsyncSession,
    *,
    task_id: int,
    deliverable_id: int,
    poster_id: int,
    notes: str,
    poster_notify_agent_id: int | None = None,
) -> dict[str, Any]:
    task_result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == poster_id).limit(1)
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found for this poster.",
            "Use a task you posted.",
        )
    if task.status != "delivered":
        raise MarketplaceError(
            409,
            "INVALID_STATUS",
            f"Task {task_id} is not in delivered state.",
            "Only delivered tasks can request revisions.",
        )

    deliverable_result = await session.execute(
        select(Deliverable)
        .where(Deliverable.id == deliverable_id, Deliverable.task_id == task_id)
        .limit(1)
    )
    deliverable = deliverable_result.scalar_one_or_none()
    if deliverable is None:
        raise MarketplaceError(
            404,
            "DELIVERABLE_NOT_FOUND",
            f"Deliverable {deliverable_id} was not found on task {task_id}.",
            "List deliverables first and select a valid deliverable_id.",
        )
    if deliverable.revision_number >= task.max_revisions + 1:
        raise MarketplaceError(
            409,
            "MAX_REVISIONS_REACHED",
            f"Task {task_id} has already reached its revision limit.",
            "Accept the current deliverable or increase max_revisions.",
        )

    deliverable.status = "revision_requested"
    deliverable.revision_notes = notes
    task.status = "in_progress"
    task.updated_at = _now()
    await session.commit()
    _sync_workspace_status(task_id, "in_progress")

    dispatch_webhook_event(
        deliverable.agent_id,
        "deliverable.revision_requested",
        {
            "task_id": task_id,
            "deliverable_id": deliverable.id,
            "revision_notes": notes,
        },
    )
    await _dispatch_operator_webhook(
        session,
        operator_id=poster_id,
        event="task.updated",
        data={"task_id": task_id, "status": "in_progress", "deliverable_id": deliverable.id},
        preferred_agent_id=poster_notify_agent_id,
    )
    event_broadcaster.broadcast(
        poster_id,
        "task_updated",
        {"task_id": task_id, "status": "in_progress"},
    )
    agent_ids = [deliverable.agent_id]
    if poster_notify_agent_id:
        agent_ids.append(poster_notify_agent_id)
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="deliverable.revision_requested",
        poster_id=poster_id,
        agent_ids=agent_ids,
        extra={"deliverable_id": deliverable.id},
    )
    return {"task_id": task_id, "deliverable_id": deliverable.id}


async def accept_deliverable(
    session: AsyncSession,
    *,
    task_id: int,
    deliverable_id: int,
    poster_id: int,
    poster_notify_agent_id: int | None = None,
) -> dict[str, Any]:
    task_result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == poster_id).limit(1)
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found for this poster.",
            "Use a task you posted.",
        )
    if task.status != "delivered":
        raise MarketplaceError(
            409,
            "INVALID_STATUS",
            f"Task {task_id} is not in delivered state.",
            "Only delivered tasks can be accepted.",
        )

    deliverable_result = await session.execute(
        select(Deliverable)
        .where(Deliverable.id == deliverable_id, Deliverable.task_id == task_id)
        .limit(1)
    )
    deliverable = deliverable_result.scalar_one_or_none()
    if deliverable is None:
        raise MarketplaceError(
            404,
            "DELIVERABLE_NOT_FOUND",
            f"Deliverable {deliverable_id} was not found on task {task_id}.",
            "List deliverables first and select a valid deliverable_id.",
        )

    deliverable.status = "accepted"
    task.status = "completed"
    task.updated_at = _now()

    credit_result = {"payment": 0, "fee": 0}
    if task.claimed_by_agent_id:
        agent_result = await session.execute(
            select(Agent).where(Agent.id == task.claimed_by_agent_id).limit(1)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is not None:
            credit_result = await process_task_completion(
                session,
                agent.operator_id,
                task.budget_credits,
                task_id,
            )
            agent.tasks_completed += 1
            agent.updated_at = _now()

    await session.commit()
    _sync_workspace_status(task_id, "completed")
    _cleanup_workspace(task_id, "completed")

    if task.claimed_by_agent_id:
        dispatch_webhook_event(
            task.claimed_by_agent_id,
            "deliverable.accepted",
            {
                "task_id": task_id,
                "deliverable_id": deliverable.id,
                "credits_paid": credit_result["payment"],
            },
        )
    await _dispatch_operator_webhook(
        session,
        operator_id=poster_id,
        event="task.updated",
        data={"task_id": task_id, "status": "completed", "deliverable_id": deliverable.id},
        preferred_agent_id=poster_notify_agent_id,
    )
    event_broadcaster.broadcast(
        poster_id,
        "task_updated",
        {"task_id": task_id, "status": "completed"},
    )
    agent_ids: list[int] = []
    if task.claimed_by_agent_id:
        agent_ids.append(task.claimed_by_agent_id)
    if poster_notify_agent_id:
        agent_ids.append(poster_notify_agent_id)
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="deliverable.accepted",
        poster_id=poster_id,
        agent_ids=agent_ids,
        extra={"deliverable_id": deliverable.id},
    )
    return {
        "task_id": task_id,
        "deliverable_id": deliverable.id,
        "credits_paid": credit_result["payment"],
        "platform_fee": credit_result["fee"],
    }


async def send_message(
    session: AsyncSession,
    *,
    task_id: int,
    sender_type: str,
    sender_id: int,
    sender_name: str,
    content: str,
    message_type: str = "text",
    parent_id: int | None = None,
    structured_data: dict[str, Any] | None = None,
    notify_agent_id: int | None = None,
) -> dict[str, Any]:
    task_result = await session.execute(select(Task).where(Task.id == task_id).limit(1))
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found.",
            "Use a valid task_id.",
        )
    if task.status in ("completed", "cancelled"):
        raise MarketplaceError(
            409,
            "TASK_CLOSED",
            f"Task {task_id} is {task.status}.",
            "Messages can only be sent on active tasks.",
        )

    message = TaskMessage(
        task_id=task_id,
        sender_type=sender_type,
        sender_id=sender_id,
        sender_name=sender_name,
        content=content,
        message_type=message_type,
        structured_data=structured_data,
        parent_id=parent_id,
    )
    session.add(message)
    await session.flush()
    task.updated_at = _now()
    await session.commit()
    await session.refresh(message)

    event_broadcaster.broadcast(
        task.poster_id,
        "message_created",
        {
            "task_id": task_id,
            "message_id": message.id,
            "sender_type": sender_type,
            "message_type": message_type,
        },
    )
    if sender_type == "poster" and task.claimed_by_agent_id:
        dispatch_webhook_event(
            task.claimed_by_agent_id,
            "message.created",
            {
                "task_id": task_id,
                "message_id": message.id,
                "sender_type": sender_type,
                "message_type": message_type,
            },
        )
    else:
        await _dispatch_operator_webhook(
            session,
            operator_id=task.poster_id,
            event="message.created",
            data={
                "task_id": task_id,
                "message_id": message.id,
                "sender_type": sender_type,
                "message_type": message_type,
            },
            preferred_agent_id=notify_agent_id,
        )
    agent_ids: list[int] = []
    if task.claimed_by_agent_id:
        agent_ids.append(task.claimed_by_agent_id)
    if sender_type == "agent":
        agent_ids.append(sender_id)
    if notify_agent_id:
        agent_ids.append(notify_agent_id)
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="message.created",
        poster_id=task.poster_id,
        agent_ids=agent_ids,
        extra={"message_id": message.id},
    )
    return {"task_id": task_id, "message_id": message.id}


async def answer_question(
    session: AsyncSession,
    *,
    task_id: int,
    message_id: int,
    poster_id: int,
    poster_name: str,
    response: str,
    option_index: int | None = None,
    poster_notify_agent_id: int | None = None,
) -> dict[str, Any]:
    task_result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == poster_id).limit(1)
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found for this poster.",
            "Use a task you posted.",
        )

    question_result = await session.execute(
        select(TaskMessage)
        .where(
            TaskMessage.id == message_id,
            TaskMessage.task_id == task_id,
            TaskMessage.message_type == "question",
        )
        .limit(1)
    )
    question = question_result.scalar_one_or_none()
    if question is None:
        raise MarketplaceError(
            404,
            "QUESTION_NOT_FOUND",
            f"Question message {message_id} was not found on task {task_id}.",
            "List task messages first and use a valid question message_id.",
        )

    responded_at = _now().isoformat().replace("+00:00", "Z")
    updated_data = dict(question.structured_data or {})
    updated_data["response"] = response
    if option_index is not None:
        updated_data["selected_option"] = option_index
    updated_data["responded_at"] = responded_at
    question.structured_data = updated_data

    if question.sender_id and task.agent_remarks:
        updated_remarks = list(task.agent_remarks)
        question_id = updated_data.get("question_id", "")
        for remark in reversed(updated_remarks):
            if remark.get("agent_id") != question.sender_id:
                continue
            questions = (remark.get("evaluation") or {}).get("questions", [])
            for item in questions:
                if item.get("id") == question_id:
                    item["answer"] = response
                    item["answered_at"] = responded_at
                    break
            else:
                continue
            break
        task.agent_remarks = updated_remarks

    reply = TaskMessage(
        task_id=task_id,
        sender_type="poster",
        sender_id=poster_id,
        sender_name=poster_name,
        content=response,
        message_type="text",
        parent_id=message_id,
    )
    session.add(reply)
    task.updated_at = _now()
    await session.flush()
    await session.commit()
    await session.refresh(reply)

    if question.sender_id:
        dispatch_webhook_event(
            question.sender_id,
            "message.created",
            {
                "task_id": task_id,
                "message_id": reply.id,
                "sender_type": "poster",
                "message_type": "text",
                "question_id": question.id,
            },
        )
    await _dispatch_operator_webhook(
        session,
        operator_id=poster_id,
        event="message.created",
        data={
            "task_id": task_id,
            "message_id": reply.id,
            "sender_type": "poster",
            "message_type": "text",
        },
        preferred_agent_id=poster_notify_agent_id,
    )
    event_broadcaster.broadcast(
        poster_id,
        "message_created",
        {
            "task_id": task_id,
            "message_id": reply.id,
            "sender_type": "poster",
            "message_type": "text",
        },
    )
    agent_ids: list[int] = []
    if question.sender_id:
        agent_ids.append(question.sender_id)
    if poster_notify_agent_id:
        agent_ids.append(poster_notify_agent_id)
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="message.created",
        poster_id=poster_id,
        agent_ids=agent_ids,
        extra={"message_id": reply.id},
    )
    return {"task_id": task_id, "message_id": reply.id, "question_id": question.id}


async def cancel_task(
    session: AsyncSession,
    *,
    task_id: int,
    poster_id: int,
    poster_notify_agent_id: int | None = None,
) -> dict[str, Any]:
    task_result = await session.execute(
        select(Task).where(Task.id == task_id, Task.poster_id == poster_id).limit(1)
    )
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found for this poster.",
            "Use a task you posted.",
        )
    if task.status in ("completed", "cancelled"):
        raise MarketplaceError(
            409,
            "TASK_ALREADY_CLOSED",
            f"Task {task_id} is already {task.status}.",
            "Only active tasks can be cancelled.",
        )

    if task.status in ("claimed", "in_progress", "delivered") and task.budget_credits > 0:
        await _add_credits(
            session,
            poster_id,
            task.budget_credits,
            "refund",
            f"Refund for cancelled task: {task.title}",
            task.id,
        )

    await session.execute(
        update(TaskClaim)
        .where(TaskClaim.task_id == task_id, TaskClaim.status == "pending")
        .values(status="rejected")
    )
    task.status = "cancelled"
    task.updated_at = _now()
    await session.commit()
    _sync_workspace_status(task_id, "cancelled")
    _cleanup_workspace(task_id, "cancelled")

    await _dispatch_operator_webhook(
        session,
        operator_id=poster_id,
        event="task.updated",
        data={"task_id": task_id, "status": "cancelled"},
        preferred_agent_id=poster_notify_agent_id,
    )
    event_broadcaster.broadcast(
        poster_id,
        "task_updated",
        {"task_id": task_id, "status": "cancelled"},
    )
    await _emit_external_update(
        session,
        task_id=task_id,
        event_type="task.updated",
        poster_id=poster_id,
        agent_ids=[poster_notify_agent_id] if poster_notify_agent_id else [],
        extra={"task_id": task_id},
    )
    return {"task_id": task_id}


async def list_claims_for_task(
    session: AsyncSession,
    *,
    task_id: int,
    viewer_user_id: int | None = None,
    viewer_agent_id: int | None = None,
) -> list[dict[str, Any]]:
    task_result = await session.execute(select(Task).where(Task.id == task_id).limit(1))
    task = task_result.scalar_one_or_none()
    if task is None:
        raise MarketplaceError(
            404,
            "TASK_NOT_FOUND",
            f"Task {task_id} was not found.",
            "Use a valid task_id.",
        )

    result = await session.execute(
        select(
            TaskClaim.id,
            TaskClaim.task_id,
            TaskClaim.agent_id,
            Agent.name.label("agent_name"),
            Agent.reputation_score,
            Agent.tasks_completed,
            Agent.avg_rating,
            Agent.capabilities,
            TaskClaim.proposed_credits,
            TaskClaim.message,
            TaskClaim.status,
            TaskClaim.created_at,
        )
        .select_from(TaskClaim)
        .join(Agent, TaskClaim.agent_id == Agent.id)
        .where(TaskClaim.task_id == task_id)
        .order_by(TaskClaim.created_at.desc(), TaskClaim.id.desc())
    )

    claims = []
    for row in result.all():
        if viewer_user_id != task.poster_id and viewer_agent_id != row.agent_id:
            continue
        claims.append(
            {
                "id": row.id,
                "task_id": row.task_id,
                "agent_id": row.agent_id,
                "agent_name": row.agent_name,
                "proposed_credits": row.proposed_credits,
                "message": row.message,
                "status": row.status,
                "created_at": row.created_at,
                "reputation_score": row.reputation_score,
                "tasks_completed": row.tasks_completed,
                "avg_rating": row.avg_rating,
                "capabilities": row.capabilities,
                "reputation_tier": compute_reputation_tier(
                    row.reputation_score,
                    row.tasks_completed,
                    row.avg_rating,
                ),
            }
        )
    return claims
