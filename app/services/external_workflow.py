from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Agent,
    Category,
    Deliverable,
    OrchTaskExecution,
    SubmissionAttempt,
    Task,
    TaskClaim,
    TaskMessage,
    User,
)
from app.orchestrator.legacy_bridge import read_legacy_progress_summary, resolve_legacy_workspace
from app.services.reputation import compute_reputation_tier


def isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


def frontend_origin() -> str:
    raw = (settings.NEXT_APP_URL or "").strip() or "http://localhost:3000"
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw.rstrip("/")


async def _fetch_task_row(session: AsyncSession, task_id: int) -> Any | None:
    result = await session.execute(
        select(
            Task.id,
            Task.poster_id,
            Task.title,
            Task.description,
            Task.requirements,
            Task.budget_credits,
            Task.category_id,
            Category.name.label("category_name"),
            Category.slug.label("category_slug"),
            Task.status,
            Task.claimed_by_agent_id,
            Task.deadline,
            Task.max_revisions,
            Task.auto_review_enabled,
            Task.poster_reviews_used,
            Task.poster_max_reviews,
            Task.agent_remarks,
            Task.created_at,
            Task.updated_at,
            User.name.label("poster_name"),
            User.email.label("poster_email"),
            Agent.name.label("claimed_agent_name"),
        )
        .select_from(Task)
        .join(User, Task.poster_id == User.id)
        .outerjoin(Category, Task.category_id == Category.id)
        .outerjoin(Agent, Task.claimed_by_agent_id == Agent.id)
        .where(Task.id == task_id)
        .limit(1)
    )
    return result.first()


async def _fetch_claims(session: AsyncSession, task_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        select(
            TaskClaim.id,
            TaskClaim.task_id,
            TaskClaim.agent_id,
            Agent.name.label("agent_name"),
            Agent.description.label("agent_description"),
            Agent.capabilities,
            Agent.reputation_score,
            Agent.tasks_completed,
            Agent.avg_rating,
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
    claims: list[dict[str, Any]] = []
    for row in result.all():
        claims.append(
            {
                "id": row.id,
                "task_id": row.task_id,
                "agent_id": row.agent_id,
                "agent_name": row.agent_name,
                "agent_description": row.agent_description,
                "capabilities": row.capabilities or [],
                "proposed_credits": row.proposed_credits,
                "message": row.message,
                "status": row.status,
                "created_at": isoformat(row.created_at),
                "reputation": {
                    "score": row.reputation_score,
                    "tasks_completed": row.tasks_completed,
                    "avg_rating": row.avg_rating,
                    "tier": compute_reputation_tier(
                        row.reputation_score,
                        row.tasks_completed,
                        row.avg_rating,
                    ),
                },
            }
        )
    return claims


async def _fetch_deliverables(session: AsyncSession, task_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        select(
            Deliverable.id,
            Deliverable.task_id,
            Deliverable.agent_id,
            Agent.name.label("agent_name"),
            Deliverable.content,
            Deliverable.status,
            Deliverable.revision_number,
            Deliverable.revision_notes,
            Deliverable.submitted_at,
        )
        .select_from(Deliverable)
        .join(Agent, Deliverable.agent_id == Agent.id)
        .where(Deliverable.task_id == task_id)
        .order_by(Deliverable.revision_number.desc(), Deliverable.id.desc())
    )
    return [
        {
            "id": row.id,
            "task_id": row.task_id,
            "agent_id": row.agent_id,
            "agent_name": row.agent_name,
            "content": row.content,
            "status": row.status,
            "revision_number": row.revision_number,
            "revision_notes": row.revision_notes,
            "submitted_at": isoformat(row.submitted_at),
        }
        for row in result.all()
    ]


async def _fetch_messages(session: AsyncSession, task_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        select(TaskMessage)
        .where(TaskMessage.task_id == task_id)
        .order_by(asc(TaskMessage.created_at), asc(TaskMessage.id))
    )
    return [
        {
            "id": msg.id,
            "task_id": msg.task_id,
            "sender_type": msg.sender_type,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "content": msg.content,
            "message_type": msg.message_type,
            "structured_data": msg.structured_data,
            "parent_id": msg.parent_id,
            "claim_id": msg.claim_id,
            "is_read": msg.is_read,
            "created_at": isoformat(msg.created_at),
        }
        for msg in result.scalars().all()
    ]


async def _fetch_activity(session: AsyncSession, task_id: int) -> list[dict[str, Any]]:
    result = await session.execute(
        select(
            SubmissionAttempt.id,
            SubmissionAttempt.agent_id,
            Agent.name.label("agent_name"),
            SubmissionAttempt.deliverable_id,
            SubmissionAttempt.attempt_number,
            SubmissionAttempt.review_result,
            SubmissionAttempt.review_feedback,
            SubmissionAttempt.review_scores,
            SubmissionAttempt.review_key_source,
            SubmissionAttempt.llm_model_used,
            SubmissionAttempt.submitted_at,
            SubmissionAttempt.reviewed_at,
        )
        .select_from(SubmissionAttempt)
        .join(Agent, SubmissionAttempt.agent_id == Agent.id)
        .where(SubmissionAttempt.task_id == task_id)
        .order_by(SubmissionAttempt.submitted_at.desc(), SubmissionAttempt.id.desc())
    )
    return [
        {
            "id": row.id,
            "agent_id": row.agent_id,
            "agent_name": row.agent_name,
            "deliverable_id": row.deliverable_id,
            "attempt_number": row.attempt_number,
            "review_result": row.review_result,
            "review_feedback": row.review_feedback,
            "review_scores": row.review_scores,
            "review_key_source": row.review_key_source,
            "llm_model_used": row.llm_model_used,
            "submitted_at": isoformat(row.submitted_at),
            "reviewed_at": isoformat(row.reviewed_at),
        }
        for row in result.all()
    ]


async def _fetch_execution(session: AsyncSession, task_id: int) -> OrchTaskExecution | None:
    result = await session.execute(
        select(OrchTaskExecution)
        .where(OrchTaskExecution.oriexa_task_id == task_id)
        .order_by(OrchTaskExecution.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _build_progress(execution: OrchTaskExecution | None) -> dict[str, Any] | None:
    if execution is None:
        return None

    origin = frontend_origin()
    progress_api = f"{origin}/api/orchestrator/progress/executions/{execution.id}"
    preview_api = f"{origin}/api/orchestrator/preview/executions/{execution.id}"

    summary = None
    try:
        workspace = resolve_legacy_workspace(
            task_id=execution.oriexa_task_id,
            execution_id=execution.id,
            workspace_path=execution.workspace_path,
        )
        summary = read_legacy_progress_summary(Path(workspace))
    except Exception:
        summary = None

    return {
        "execution_id": execution.id,
        "status": execution.status,
        "current_phase": (summary or {}).get("current_phase") or execution.status,
        "progress_pct": (summary or {}).get("progress_pct"),
        "error_message": execution.error_message,
        "started_at": isoformat(execution.started_at),
        "completed_at": isoformat(execution.completed_at),
        "progress_url": progress_api,
        "progress_stream_url": f"{progress_api}/stream",
        "preview_url": preview_api,
    }


def _question_needs_response(message: dict[str, Any]) -> bool:
    if message.get("message_type") != "question":
        return False
    structured = message.get("structured_data") or {}
    return not structured.get("response")


def _filter_claims_for_viewer(
    claims: list[dict[str, Any]],
    *,
    viewer_user_id: int | None,
    viewer_agent_id: int | None,
    poster_id: int,
) -> list[dict[str, Any]]:
    if viewer_user_id == poster_id:
        return claims
    if viewer_agent_id is None:
        return []
    return [claim for claim in claims if claim["agent_id"] == viewer_agent_id]


def _filter_messages_for_viewer(
    messages: list[dict[str, Any]],
    *,
    viewer_user_id: int | None,
    viewer_agent_id: int | None,
    poster_id: int,
    claimed_by_agent_id: int | None,
    own_claim_agent_ids: set[int],
) -> list[dict[str, Any]]:
    if viewer_user_id == poster_id:
        return messages
    if viewer_agent_id is None:
        return []
    if viewer_agent_id == claimed_by_agent_id or viewer_agent_id in own_claim_agent_ids:
        return messages
    return []


def _build_workflow(
    *,
    task: Any,
    claims: list[dict[str, Any]],
    deliverables: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    progress: dict[str, Any] | None,
    viewer_user_id: int | None,
    viewer_agent_id: int | None,
) -> dict[str, Any]:
    pending_claims = [claim for claim in claims if claim["status"] == "pending"]
    own_claim = next((claim for claim in claims if claim["agent_id"] == viewer_agent_id), None)
    latest_deliverable = deliverables[0] if deliverables else None
    latest_message = messages[-1] if messages else None
    pending_questions = [msg for msg in messages if _question_needs_response(msg)]

    phase = task.status
    awaiting_actor = "none"
    next_actions: list[str] = []
    reason = f"Task is currently {task.status}."

    if task.status == "open":
        if pending_claims:
            phase = "awaiting_claim_acceptance"
            awaiting_actor = "poster"
            next_actions = ["accept_claim", "send_message"]
            reason = f"{len(pending_claims)} claim(s) are waiting for the poster to review."
        elif own_claim:
            phase = "claim_pending"
            awaiting_actor = "poster"
            next_actions = ["send_message"]
            reason = "Your claim is pending poster review."
        else:
            phase = "marketplace_open"
            awaiting_actor = "worker"
            next_actions = ["claim_task"]
            reason = "The task is open in the marketplace and can be claimed."
    elif task.status == "claimed":
        phase = "claimed"
        awaiting_actor = "worker"
        next_actions = ["submit_deliverable", "send_message"]
        reason = "A claim was accepted and the worker can start delivery."
    elif task.status == "in_progress":
        if latest_deliverable and latest_deliverable["status"] == "revision_requested":
            phase = "revision_requested"
            awaiting_actor = "worker"
            next_actions = ["submit_deliverable", "send_message"]
            reason = "A revision was requested and the worker needs to resubmit."
        elif pending_questions:
            phase = "awaiting_question_response"
            awaiting_actor = "poster"
            next_actions = ["answer_question", "send_message"]
            reason = "The worker is blocked on a poster response."
        else:
            phase = "in_progress"
            awaiting_actor = "worker"
            next_actions = ["submit_deliverable", "send_message"]
            reason = "Work is in progress and the next step is a deliverable submission."
    elif task.status == "delivered":
        phase = "awaiting_deliverable_review"
        awaiting_actor = "poster"
        next_actions = ["accept_deliverable", "request_revision", "send_message"]
        reason = "A deliverable was submitted and is waiting for poster review."
    elif task.status == "completed":
        phase = "completed"
        awaiting_actor = "none"
        next_actions = []
        reason = "The task has been completed."
    elif task.status == "cancelled":
        phase = "cancelled"
        awaiting_actor = "none"
        next_actions = []
        reason = "The task has been cancelled."

    if viewer_user_id == task.poster_id:
        allowed = {
            "create_task",
            "accept_claim",
            "request_revision",
            "accept_deliverable",
            "send_message",
            "answer_question",
        }
        next_actions = [action for action in next_actions if action in allowed]
    elif viewer_agent_id is not None:
        allowed = {"claim_task", "submit_deliverable", "send_message"}
        next_actions = [action for action in next_actions if action in allowed]

    unread_count = 0
    if viewer_user_id == task.poster_id:
        unread_count = sum(
            1 for message in messages if message["sender_type"] == "agent" and not message["is_read"]
        )
    elif viewer_agent_id is not None:
        unread_count = sum(
            1 for message in messages if message["sender_type"] == "poster" and not message["is_read"]
        )

    return {
        "phase": phase,
        "awaiting_actor": awaiting_actor,
        "next_actions": next_actions,
        "reason": reason,
        "unread_count": unread_count,
        "latest_message": latest_message,
        "progress": progress,
    }


async def build_external_task_bundle(
    session: AsyncSession,
    task_id: int,
    *,
    viewer_user_id: int | None = None,
    viewer_agent_id: int | None = None,
    include_claims: bool = True,
    include_deliverables: bool = True,
    include_messages: bool = True,
    include_activity: bool = True,
) -> dict[str, Any] | None:
    task = await _fetch_task_row(session, task_id)
    if task is None:
        return None

    claims = await _fetch_claims(session, task_id)
    deliverables = await _fetch_deliverables(session, task_id)
    messages = await _fetch_messages(session, task_id)
    activity = await _fetch_activity(session, task_id) if include_activity else []
    execution = await _fetch_execution(session, task_id)
    progress = _build_progress(execution)

    own_claim_agent_ids = {claim["agent_id"] for claim in claims if claim["agent_id"] == viewer_agent_id}
    visible_claims = _filter_claims_for_viewer(
        claims,
        viewer_user_id=viewer_user_id,
        viewer_agent_id=viewer_agent_id,
        poster_id=task.poster_id,
    )
    visible_messages = _filter_messages_for_viewer(
        messages,
        viewer_user_id=viewer_user_id,
        viewer_agent_id=viewer_agent_id,
        poster_id=task.poster_id,
        claimed_by_agent_id=task.claimed_by_agent_id,
        own_claim_agent_ids=own_claim_agent_ids,
    )
    visible_deliverables = (
        deliverables
        if viewer_user_id == task.poster_id or viewer_agent_id == task.claimed_by_agent_id
        else []
    )

    workflow = _build_workflow(
        task=task,
        claims=claims,
        deliverables=deliverables,
        messages=messages,
        progress=progress,
        viewer_user_id=viewer_user_id,
        viewer_agent_id=viewer_agent_id,
    )

    bundle = {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "requirements": task.requirements,
        "budget_credits": task.budget_credits,
        "status": task.status,
        "category": (
            {
                "id": task.category_id,
                "name": task.category_name,
                "slug": task.category_slug,
            }
            if task.category_id
            else None
        ),
        "poster": {
            "id": task.poster_id,
            "name": task.poster_name,
            "email": task.poster_email,
        },
        "claimed_by_agent": (
            {
                "id": task.claimed_by_agent_id,
                "name": task.claimed_agent_name,
            }
            if task.claimed_by_agent_id
            else None
        ),
        "deadline": isoformat(task.deadline),
        "max_revisions": task.max_revisions,
        "auto_review_enabled": task.auto_review_enabled,
        "poster_reviews_used": task.poster_reviews_used,
        "poster_max_reviews": task.poster_max_reviews,
        "agent_remarks": task.agent_remarks or [],
        "claims_count": len(claims),
        "deliverables_count": len(deliverables),
        "created_at": isoformat(task.created_at),
        "updated_at": isoformat(task.updated_at),
        "workflow": workflow,
    }

    if include_claims:
        bundle["claims"] = visible_claims
    if include_deliverables:
        bundle["deliverables"] = visible_deliverables
    if include_messages:
        bundle["messages"] = visible_messages
    if include_activity:
        bundle["activity"] = activity

    return bundle


async def build_external_event_payload(session: AsyncSession, task_id: int) -> dict[str, Any] | None:
    task = await _fetch_task_row(session, task_id)
    if task is None:
        return None

    claims = await _fetch_claims(session, task_id)
    deliverables = await _fetch_deliverables(session, task_id)
    messages = await _fetch_messages(session, task_id)
    progress = _build_progress(await _fetch_execution(session, task_id))
    workflow = _build_workflow(
        task=task,
        claims=claims,
        deliverables=deliverables,
        messages=messages,
        progress=progress,
        viewer_user_id=None,
        viewer_agent_id=None,
    )
    latest_claim = claims[0] if claims else None
    latest_deliverable = deliverables[0] if deliverables else None
    latest_message = workflow.get("latest_message")
    progress = workflow.get("progress") or {}

    return {
        "task_id": task.id,
        "task_status": task.status,
        "phase": workflow["phase"],
        "awaiting_actor": workflow["awaiting_actor"],
        "next_action": workflow["next_actions"][0] if workflow["next_actions"] else None,
        "claim_id": latest_claim["id"] if latest_claim else None,
        "deliverable_id": latest_deliverable["id"] if latest_deliverable else None,
        "message_id": latest_message["id"] if latest_message else None,
        "execution_id": progress.get("execution_id"),
        "progress_url": progress.get("progress_url"),
        "progress_stream_url": progress.get("progress_stream_url"),
        "preview_url": progress.get("preview_url"),
        "workflow": workflow,
    }
