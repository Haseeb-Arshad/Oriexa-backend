"""Task picker daemon — polls Oriexa for tasks, registers webhooks, dispatches to worker pool."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from app.config import settings
from app.db.engine import async_session
from app.db.enums import OrchTaskStatus
from app.db.models import OrchTaskExecution
from app.orchestrator.concurrency import WorkerPool
from app.sandbox.workspace import WorkspaceManager
from app.oriexa_client.client import OriexaClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Coding-task filter — only pick up development/coding tasks
# ---------------------------------------------------------------------------

NON_CODING_INDICATORS = [
    "write a blog",
    "write an article",
    "social media",
    "marketing copy",
    "translate",
    "transcribe",
    "data entry",
    "proofread",
    "content writing",
    "seo optimization",
    "graphic design only",
    "logo design only",
    "video editing",
    "voiceover",
    "virtual assistant",
]

CODING_INDICATORS = [
    "build", "develop", "implement", "code", "program",
    "api", "endpoint", "database", "frontend", "backend",
    "react", "next.js", "vue", "svelte", "angular",
    "python", "javascript", "typescript", "html", "css",
    "deploy", "docker", "ci/cd", "devops",
    "bug fix", "refactor", "test", "script",
    "web app", "website", "dashboard", "landing page",
    "component", "feature", "integration",
    "fastapi", "express", "django", "flask",
    "node.js", "npm", "package",
]


def _is_coding_task(task_data: dict[str, Any]) -> bool:
    """Determine if a task is a coding/development task.

    Uses keyword heuristics on title, description, and category.
    Conservative: defaults to True for ambiguous tasks.
    """
    title = (task_data.get("title") or "").lower()
    description = (task_data.get("description") or "").lower()
    # category may be a dict {"id":1,"name":"Coding",...} or a plain string
    cat_raw = task_data.get("category") or ""
    if isinstance(cat_raw, dict):
        cat_raw = cat_raw.get("name") or cat_raw.get("slug") or ""
    category = str(cat_raw).lower()
    text = f"{title} {description} {category}"

    # Check for explicit non-coding indicators
    for indicator in NON_CODING_INDICATORS:
        if indicator in text:
            has_coding_signal = any(ci in text for ci in CODING_INDICATORS)
            if not has_coding_signal:
                return False

    # Check for coding indicators
    for indicator in CODING_INDICATORS:
        if indicator in text:
            return True

    # Explicit development categories
    if category in ("development", "engineering", "coding", "programming", "web development"):
        return True

    # Default: assume coding (conservative — don't skip ambiguous tasks)
    return True


# All webhook events the agent subscribes to
WEBHOOK_EVENTS = [
    "task.new_match",
    "claim.accepted",
    "claim.rejected",
    "deliverable.accepted",
    "deliverable.revision_requested",
    "message.created",
]


class TaskPickerDaemon:
    """Background daemon that polls for new tasks and dispatches graph executions.

    On startup, registers webhooks with the Oriexa API so the agent receives
    real-time notifications for task matches, claim decisions, and revision requests.
    Falls back to polling if webhook registration fails.
    """

    def __init__(
        self,
        worker_pool: WorkerPool,
        client: OriexaClient | None = None,
        poll_interval: int | None = None,
        webhook_url: str | None = None,
    ):
        self.pool = worker_pool
        self.client = client or OriexaClient()
        self.poll_interval = poll_interval or settings.TASK_POLL_INTERVAL
        self.workspace_mgr = WorkspaceManager()
        self.webhook_url = webhook_url  # e.g. "https://my-agent.example.com/orchestrator/webhooks/oriexa"
        self._running = False
        self._task: asyncio.Task | None = None
        self._webhook_id: int | None = None

    async def start(self) -> None:
        """Start the daemon: register webhooks, then begin polling loop."""
        if self._running:
            return
        self._running = True
        self.workspace_mgr.ensure_root()

        # Register webhooks if URL is configured
        if self.webhook_url:
            await self._register_webhooks()

        self._task = asyncio.create_task(self._poll_loop(), name="task-picker-daemon")
        logger.info("TaskPickerDaemon started (interval=%ds)", self.poll_interval)

    async def stop(self) -> None:
        """Stop the polling loop and clean up webhooks."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Clean up webhook registration
        if self._webhook_id:
            try:
                await self.client.delete_webhook(self._webhook_id)
                logger.info("Webhook %d deregistered", self._webhook_id)
            except Exception:
                logger.warning("Failed to deregister webhook %d", self._webhook_id)

        await self.client.close()
        logger.info("TaskPickerDaemon stopped")

    # -- Webhook registration --

    async def _register_webhooks(self) -> None:
        """Register webhook with Oriexa API for real-time notifications."""
        try:
            # Check if we already have webhooks registered
            existing = await self.client.list_webhooks()
            for wh in existing:
                if wh.get("url") == self.webhook_url:
                    self._webhook_id = wh["id"]
                    logger.info("Reusing existing webhook %d at %s", self._webhook_id, self.webhook_url)
                    return

            # Register new webhook
            result = await self.client.register_webhook(
                url=self.webhook_url,
                events=WEBHOOK_EVENTS,
            )
            if result and "id" in result:
                self._webhook_id = result["id"]
                logger.info(
                    "Registered webhook %d at %s for events: %s",
                    self._webhook_id, self.webhook_url, WEBHOOK_EVENTS,
                )
            else:
                logger.warning("Webhook registration returned no ID — falling back to polling only")
        except Exception:
            logger.exception("Failed to register webhooks — falling back to polling only")

    # -- Webhook event handlers (called by the inbound webhook router) --

    async def handle_webhook_event(self, event: str, data: dict[str, Any]) -> None:
        """Process an inbound webhook event from Oriexa."""
        logger.info("Handling webhook event: %s", event)

        if event == "task.new_match":
            task_id = data.get("task_id") or data.get("taskId") or data.get("id")
            if task_id and self.pool.has_capacity():
                task_data = await self.client.get_task(task_id)
                if task_data:
                    # Filter: only process coding/development tasks
                    if not _is_coding_task(task_data):
                        logger.info("Skipping non-coding webhook task %d", task_id)
                        return

                    # Check not already tracked
                    async with async_session() as session:
                        result = await session.execute(
                            select(OrchTaskExecution.id).where(
                                OrchTaskExecution.oriexa_task_id == task_id,
                                OrchTaskExecution.status.notin_(["failed", "completed"]),
                            )
                        )
                        if not result.first():
                            await self._process_task(task_data)

        elif event == "claim.accepted":
            task_id = data.get("task_id") or data.get("taskId")
            logger.info("Claim accepted for task %s — starting orchestration", task_id)
            if task_id and self.pool.has_capacity():
                # Check if we're already tracking this task
                async with async_session() as session:
                    result = await session.execute(
                        select(OrchTaskExecution.id).where(
                            OrchTaskExecution.oriexa_task_id == task_id,
                            OrchTaskExecution.status.notin_(["failed", "completed"]),
                        )
                    )
                    if not result.first():
                        task_data = await self.client.get_task(task_id)
                        if task_data:
                            await self._start_accepted_task(task_data)

        elif event == "claim.rejected":
            task_id = data.get("task_id") or data.get("taskId")
            logger.warning("Claim rejected for task %s", task_id)
            if task_id:
                await self._mark_task_failed(task_id, "Claim was rejected by poster")

        elif event == "deliverable.revision_requested":
            task_id = data.get("task_id") or data.get("taskId")
            revision_notes = data.get("revision_notes") or data.get("revisionNotes", "")
            logger.info("Revision requested for task %s", task_id)
            if task_id:
                await self._handle_revision_request(task_id, revision_notes)

        elif event == "deliverable.accepted":
            task_id = data.get("task_id") or data.get("taskId")
            logger.info("Deliverable accepted for task %s — marking complete", task_id)
            if task_id:
                await self._mark_task_completed(task_id)

        elif event == "message.created":
            task_id = data.get("task_id") or data.get("taskId")
            msg_content = data.get("content")
            sender_id = data.get("sender_id")
            
            # Ensure it's not the agent's own message bouncing back
            # Usually we'd check sender_id != agent_id, but here we'll just check if it's running
            if task_id and msg_content:
                logger.info("New message received for task %s, attempting to inject into execution", task_id)
                await self._inject_chat_message(task_id, msg_content)

    async def _handle_revision_request(self, task_id: int, revision_notes: str) -> None:
        """Re-run the graph for a task that received revision feedback."""
        async with async_session() as session:
            result = await session.execute(
                select(OrchTaskExecution).where(
                    OrchTaskExecution.oriexa_task_id == task_id
                ).order_by(OrchTaskExecution.id.desc()).limit(1)
            )
            execution = result.scalar_one_or_none()

            if not execution:
                logger.warning("No execution found for task %d revision", task_id)
                return

            # Reset status to pending so it can be picked up, or start it immediately 
            # We'll re-start it immediately here by creating a task.
            task_data = await self.client.get_task(task_id)
            if not task_data:
                return
                
            # Make sure we generate a new thread ID so LangGraph State starts fresh but with feedback
            thread_id = str(uuid.uuid4())
            workspace = self.workspace_mgr.create(execution.id)

            await session.execute(
                update(OrchTaskExecution)
                .where(OrchTaskExecution.id == execution.id)
                .values(
                    status=OrchTaskStatus.PENDING.value,
                    graph_thread_id=thread_id,
                    attempt_count=execution.attempt_count + 1,
                    updated_at=datetime.now(timezone.utc)
                )
            )
            await session.commit()
            
            # Explicitly clear out old variables and pass revision notes
            coro = self._run_graph(
                execution.id, task_id, task_data, thread_id, str(workspace),
                review_feedback=revision_notes,
                attempt_count=execution.attempt_count + 1,
            )
            await self.pool.submit(coro, execution.id)

    async def _inject_chat_message(self, task_id: int, content: str) -> None:
        """Inject a live user chat message into the active LangGraph thread.
        This updates the thread state immediately so the executing agent can see it.
        """
        async with async_session() as session:
            result = await session.execute(
                select(OrchTaskExecution).where(
                    OrchTaskExecution.oriexa_task_id == task_id,
                    OrchTaskExecution.status == OrchTaskStatus.IN_PROGRESS.value
                ).order_by(OrchTaskExecution.id.desc()).limit(1)
            )
            execution = result.scalar_one_or_none()
            thread_id = execution.graph_thread_id if execution else None

        if not thread_id:
            logger.info("No in_progress execution found for task %d to inject message.", task_id)
            return

        from app.orchestrator.supervisor import build_supervisor_graph
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        
        # We need to manually update the state
        try:
            async with AsyncSqliteSaver.from_conn_string("sqlite+aiosqlite:///oriexa.db") as checkpointer:
                graph = build_supervisor_graph()
                graph.checkpointer = checkpointer
                
                config = {"configurable": {"thread_id": thread_id}}
                state = await graph.aget_state(config)
                
                if state and hasattr(state, "values"):
                    current_messages = state.values.get("user_live_messages", [])
                    new_messages = current_messages + [
                        {"role": "user", "content": content, "timestamp": datetime.now(timezone.utc).isoformat()}
                    ]
                    
                    # Update graph state with the new message list
                    await graph.aupdate_state(config, {"user_live_messages": new_messages})
                    logger.info("Injected chat message into task %d thread %s", task_id, thread_id)
        except Exception as exc:
            logger.warning("Failed to inject message into task %d thread: %s", task_id, exc)

    async def _mark_task_failed(self, task_id: int, reason: str) -> None:
        async with async_session() as session:
            await session.execute(
                update(OrchTaskExecution)
                .where(OrchTaskExecution.oriexa_task_id == task_id)
                .values(
                    status=OrchTaskStatus.FAILED.value,
                    error_message=reason,
                    completed_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    async def _mark_task_completed(self, task_id: int) -> None:
        async with async_session() as session:
            await session.execute(
                update(OrchTaskExecution)
                .where(OrchTaskExecution.oriexa_task_id == task_id)
                .values(
                    status=OrchTaskStatus.COMPLETED.value,
                    completed_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

    # -- Polling loop --

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._check_paused_tasks()

                if self.pool.has_capacity():
                    await self._discover_and_claim_tasks()

                # Also pick up tasks where our claims were accepted by the user
                if self.pool.has_capacity():
                    await self._discover_accepted_claims()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in task picker poll loop")

            await asyncio.sleep(self.poll_interval)

    async def _discover_and_claim_tasks(self) -> None:
        """Discover open tasks from Oriexa and claim them."""
        tasks = await self.client.browse_tasks(status="open", limit=10)
        if not tasks:
            return

        async with async_session() as session:
            result = await session.execute(
                select(OrchTaskExecution.oriexa_task_id).where(
                    OrchTaskExecution.status.notin_(["failed", "completed"])
                )
            )
            tracked_ids = {row[0] for row in result.all()}

        new_tasks = [t for t in tasks if t.get("id") not in tracked_ids]
        if not new_tasks:
            return

        for task_data in new_tasks:
            if not self.pool.has_capacity():
                break

            # Filter: only process coding/development tasks
            if not _is_coding_task(task_data):
                logger.info(
                    "Skipping non-coding task %d: %s",
                    task_data.get("id", 0),
                    task_data.get("title", "Untitled")[:80],
                )
                continue

            await self._process_task(task_data)

    async def _discover_accepted_claims(self) -> None:
        """Find tasks where our claims were accepted and start orchestration.

        When a user manually accepts an agent's claim via the frontend,
        the task status changes to 'claimed' but the orchestrator doesn't
        know about it. This method polls for accepted claims and starts
        the graph for those tasks.
        """
        try:
            result = await self.client._request("GET", "/agents/me/claims", params={"status": "accepted"})
            claims = result if isinstance(result, list) else []
        except Exception:
            logger.debug("Could not fetch accepted claims")
            return

        if not claims:
            return

        # Get task IDs we're already tracking
        async with async_session() as session:
            tracked_result = await session.execute(
                select(OrchTaskExecution.oriexa_task_id).where(
                    OrchTaskExecution.status.notin_(["failed", "completed"])
                )
            )
            tracked_ids = {row[0] for row in tracked_result.all()}

        for claim in claims:
            if not self.pool.has_capacity():
                break

            task_id = claim.get("task_id")
            if not task_id or task_id in tracked_ids:
                continue

            # Fetch full task data and start orchestration
            task_data = await self.client.get_task(task_id)
            if task_data and task_data.get("status") == "claimed":
                logger.info(
                    "Discovered accepted claim for task %d — starting orchestration",
                    task_id,
                )
                await self._start_accepted_task(task_data)

    async def _start_accepted_task(self, task_data: dict[str, Any]) -> None:
        """Start orchestration for a task where our claim was already accepted."""
        task_id = task_data["id"]
        budget = task_data.get("budget_credits", task_data.get("budgetCredits", 100))
        thread_id = str(uuid.uuid4())

        async with async_session() as session:
            # Check for existing execution
            result = await session.execute(
                select(OrchTaskExecution).where(
                    OrchTaskExecution.oriexa_task_id == task_id
                )
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                if existing.status not in (OrchTaskStatus.FAILED.value, OrchTaskStatus.CLAIMING.value):
                    logger.info(
                        "Task %d execution %d already active (status=%s), skipping",
                        task_id, existing.id, existing.status,
                    )
                    return
                # Reset the failed record for a fresh retry
                await session.execute(
                    update(OrchTaskExecution)
                    .where(OrchTaskExecution.id == existing.id)
                    .values(
                        status=OrchTaskStatus.PENDING.value,
                        graph_thread_id=thread_id,
                        task_snapshot=task_data,
                        claimed_credits=budget,
                        started_at=datetime.now(timezone.utc),
                        completed_at=None,
                        error_message=None,
                        attempt_count=existing.attempt_count + 1,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
                execution_id = existing.id
            else:
                execution = OrchTaskExecution(
                    oriexa_task_id=task_id,
                    status=OrchTaskStatus.PENDING.value,
                    task_snapshot=task_data,
                    graph_thread_id=thread_id,
                    claimed_credits=budget,
                    started_at=datetime.now(timezone.utc),
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)
                execution_id = execution.id

        workspace = self.workspace_mgr.create(execution_id)

        # Store workspace path
        async with async_session() as session:
            await session.execute(
                update(OrchTaskExecution)
                .where(OrchTaskExecution.id == execution_id)
                .values(workspace_path=str(workspace))
            )
            await session.commit()

        logger.info("Starting orchestration for accepted task %d -> execution %d", task_id, execution_id)

        coro = self._run_graph(execution_id, task_id, task_data, thread_id, str(workspace))
        await self.pool.submit(coro, execution_id)

    async def _process_task(self, task_data: dict[str, Any]) -> None:
        """Claim a task and start the orchestrator graph."""
        task_id = task_data["id"]
        budget = task_data.get("budget_credits", task_data.get("budgetCredits", 100))

        claim_result = await self.client.claim_task(
            task_id=task_id,
            proposed_credits=budget,
            message="Oriexa AI Agent ready to work on this task. I'll analyze requirements, plan the implementation, execute iteratively with testing, and deliver quality results.",
        )

        if not claim_result:
            logger.warning("Failed to claim task %d", task_id)
            return

        thread_id = str(uuid.uuid4())
        async with async_session() as session:
            # Guard against UniqueViolationError: check if a record already exists
            # (happens when the poll loop picks up a previously-failed task)
            result = await session.execute(
                select(OrchTaskExecution).where(
                    OrchTaskExecution.oriexa_task_id == task_id
                )
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                if existing.status not in (OrchTaskStatus.FAILED.value, OrchTaskStatus.CLAIMING.value):
                    # Already pending/in-progress — don't double-start
                    logger.info(
                        "Task %d execution %d already active (status=%s), skipping",
                        task_id, existing.id, existing.status,
                    )
                    return
                # Reset the record to CLAIMING
                logger.info("Retrying failed execution %d or claim %d", existing.id, task_id)
                await session.execute(
                    update(OrchTaskExecution)
                    .where(OrchTaskExecution.id == existing.id)
                    .values(
                        status=OrchTaskStatus.CLAIMING.value,
                        graph_thread_id=thread_id,
                        task_snapshot=task_data,
                        claimed_credits=budget,
                        started_at=None,
                        completed_at=None,
                        error_message=None,
                        attempt_count=existing.attempt_count + 1,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
                execution_id = existing.id
            else:
                execution = OrchTaskExecution(
                    oriexa_task_id=task_id,
                    status=OrchTaskStatus.CLAIMING.value,
                    task_snapshot=task_data,
                    graph_thread_id=thread_id,
                    claimed_credits=budget,
                    started_at=None,
                )
                session.add(execution)
                await session.commit()
                await session.refresh(execution)
                execution_id = execution.id

        workspace = self.workspace_mgr.create(execution_id)

        # Store workspace path on the execution record for preview access
        async with async_session() as session:
            await session.execute(
                update(OrchTaskExecution)
                .where(OrchTaskExecution.id == execution_id)
                .values(workspace_path=str(workspace))
            )
            await session.commit()

        logger.info("Claimed task %d -> execution %d (budget=%d). Waiting for accepted claim.", task_id, execution_id, budget)
        # We DO NOT start the graph here. We wait for `claim.accepted` webhook or `_discover_accepted_claims`.

    async def _run_graph(
        self,
        execution_id: int,
        task_id: int,
        task_data: dict[str, Any],
        thread_id: str,
        workspace_path: str,
        review_feedback: str = "",
        attempt_count: int = 0,
    ) -> None:
        """Execute the LangGraph supervisor for a single task."""
        from app.orchestrator.supervisor import build_supervisor_graph

        graph = build_supervisor_graph()
        compiled = graph.compile()

        initial_state = {
            "execution_id": execution_id,
            "oriexa_task_id": task_id,
            "task_data": task_data,
            "phase": "triage",
            "workspace_path": workspace_path,
            "attempt_count": attempt_count,
            "max_attempts": 3,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "files_created": [],
            "files_modified": [],
            "commands_executed": [],
            "subtask_results": [],
            "messages": [],
            "action_hashes": [],
            "error": None,
            "review_feedback": review_feedback,
        }

        try:
            await self._update_execution_status(execution_id, OrchTaskStatus.PLANNING)

            final_state = await compiled.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": thread_id}},
            )

            phase = final_state.get("phase", "failed")
            if phase == "delivery":
                status = OrchTaskStatus.COMPLETED
            else:
                status = OrchTaskStatus.FAILED

            await self._finalize_execution(execution_id, status, final_state)

        except asyncio.CancelledError:
            await self._update_execution_status(execution_id, OrchTaskStatus.FAILED)
            raise
        except Exception as exc:
            logger.exception("Graph execution failed for execution %d: %s", execution_id, exc)
            await self._update_execution_status(
                execution_id, OrchTaskStatus.FAILED, error=str(exc)
            )
        # NOTE: workspace is intentionally NOT cleaned up so files remain
        # accessible through the preview dashboard. Use the cleanup API to
        # reclaim disk space for old executions.

    async def _check_paused_tasks(self) -> None:
        """Check for tasks waiting for poster response and resume if response received."""
        async with async_session() as session:
            result = await session.execute(
                select(OrchTaskExecution).where(
                    OrchTaskExecution.status == OrchTaskStatus.CLARIFYING.value
                )
            )
            paused = result.scalars().all()

        for execution in paused:
            deliverables = await self.client.get_deliverables(execution.oriexa_task_id)
            if deliverables:
                latest = deliverables[-1] if deliverables else None
                if latest and latest.get("revision_notes"):
                    logger.info("Poster responded for execution %d, resuming", execution.id)
                    await self._handle_revision_request(
                        execution.oriexa_task_id,
                        latest.get("revision_notes", ""),
                    )

    async def _update_execution_status(
        self, execution_id: int, status: OrchTaskStatus, error: str | None = None
    ) -> None:
        async with async_session() as session:
            values: dict[str, Any] = {
                "status": status.value,
                "updated_at": datetime.now(timezone.utc),
            }
            if error:
                values["error_message"] = error
            if status in (OrchTaskStatus.COMPLETED, OrchTaskStatus.FAILED):
                values["completed_at"] = datetime.now(timezone.utc)
            await session.execute(
                update(OrchTaskExecution)
                .where(OrchTaskExecution.id == execution_id)
                .values(**values)
            )
            await session.commit()

    async def _finalize_execution(
        self,
        execution_id: int,
        status: OrchTaskStatus,
        final_state: dict[str, Any],
    ) -> None:
        async with async_session() as session:
            await session.execute(
                update(OrchTaskExecution)
                .where(OrchTaskExecution.id == execution_id)
                .values(
                    status=status.value,
                    total_tokens_used=(
                        final_state.get("total_prompt_tokens", 0)
                        + final_state.get("total_completion_tokens", 0)
                    ),
                    error_message=final_state.get("error"),
                    attempt_count=final_state.get("attempt_count", 0),
                    completed_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
        logger.info("Execution %d finalized with status %s", execution_id, status.value)

    async def trigger_task(self, task_id: int) -> int | None:
        """Manually trigger a task by ID (for API endpoint). Returns execution_id."""
        task_data = await self.client.get_task(task_id)
        if not task_data:
            return None
        await self._process_task(task_data)
        async with async_session() as session:
            result = await session.execute(
                select(OrchTaskExecution.id).where(
                    OrchTaskExecution.oriexa_task_id == task_id
                ).order_by(OrchTaskExecution.id.desc()).limit(1)
            )
            row = result.first()
            return row[0] if row else None
