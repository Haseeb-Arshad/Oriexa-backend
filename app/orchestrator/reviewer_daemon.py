"""Reviewer daemon — polls for delivered tasks and runs the user_reviewer graph natively."""

import asyncio
import logging
from typing import Optional

from sqlalchemy import select, text
from app.db.engine import async_session

logger = logging.getLogger(__name__)

class ReviewerDaemon:
    """Daemon that periodically checks for delivered tasks and reviews them."""

    def __init__(self, check_interval: int = 30):
        self.check_interval = check_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Track pairs we've already started reviewing to avoid duplicate spawns
        self._reviewing_pairs: set[tuple[int, int]] = set()

    async def start(self):
        """Start the daemon in the background."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"ReviewerDaemon started (polling every {self.check_interval}s)")

    async def stop(self):
        """Stop the daemon."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ReviewerDaemon stopped.")

    async def _loop(self):
        """Main daemon loop."""
        while self._running:
            try:
                await self._check_pending_reviews()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ReviewerDaemon error in loop: {e}", exc_info=True)
            
            # Simple sleep between checks
            await asyncio.sleep(self.check_interval)

    async def _check_pending_reviews(self):
        """Query the DB directly for tasks that need review and dispatch them asynchronously."""
        from app.db.models import OrchTask
        
        # We need tasks that are "delivered" and have "auto_review_enabled"
        async with async_session() as session:
            # Join deliverables to find the latest "submitted" deliverable
            query = text("""
                SELECT t.id as task_id, d.id as deliverable_id 
                FROM tasks t
                JOIN deliverables d ON d.task_id = t.id
                WHERE t.status = 'delivered' 
                  AND t.auto_review_enabled = TRUE
                  AND d.status = 'submitted'
            """)
            result = await session.execute(query)
            pending_rows = result.fetchall()

        for row in pending_rows:
            task_id = getattr(row, "task_id", row[0])
            deliverable_id = getattr(row, "deliverable_id", row[1])
            pair = (task_id, deliverable_id)

            if pair not in self._reviewing_pairs:
                self._reviewing_pairs.add(pair)
                # Fire and forget the review graph execution
                asyncio.create_task(self._run_review_graph(task_id, deliverable_id))

    async def _run_review_graph(self, task_id: int, deliverable_id: int):
        """Execute the LangGraph workflow natively."""
        try:
            from app.agents.user_reviewer.graph import app as reviewer_graph
            
            logger.info(f"ReviewerDaemon: Starting review for Task {task_id} / Deliverable {deliverable_id}")
            initial_state = {
                "task_id": task_id,
                "deliverable_id": deliverable_id,
                "review_scores": {},
                "skip_review": False,
            }
            
            # Run the compiled graph linearly
            # user_reviewer/graph.py's nodes are mostly synchronous, but LangGraph handles it.
            result = await asyncio.to_thread(reviewer_graph.invoke, initial_state)

            if result.get("error"):
                logger.error(f"ReviewerDaemon: Error reviewing {task_id}: {result['error']}")
            elif result.get("skip_review"):
                logger.info(f"ReviewerDaemon: Skipped review for {task_id} (No LLM key)")
            else:
                verdict = result.get("verdict", "unknown")
                logger.info(f"ReviewerDaemon: Finished {task_id} -> Verdict: {verdict.upper()}")

        except Exception as e:
            logger.error(f"ReviewerDaemon: Failed executing graph for task {task_id}: {e}", exc_info=True)
        finally:
            # We remove it from reviewing_pairs so it won't be blocked forever if something hung, 
            # though if it's no longer submitted/delivered it won't be queried anyway.
            pair = (task_id, deliverable_id)
            if pair in self._reviewing_pairs:
                self._reviewing_pairs.remove(pair)
