"""Worker pool with bounded concurrency for parallel task execution."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


class WorkerPool:
    """Manages concurrent task executions with an asyncio.Semaphore."""

    def __init__(self, max_concurrent: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max = max_concurrent
        self._tasks: dict[int, asyncio.Task] = {}  # execution_id -> asyncio.Task
        self._results: dict[int, Any] = {}

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def has_capacity(self) -> bool:
        return self.active_count < self._max

    async def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        execution_id: int,
    ) -> asyncio.Task:
        """Submit a coroutine to run within the semaphore-bounded pool."""

        async def _wrapped() -> Any:
            async with self._semaphore:
                logger.info("Worker started for execution %d", execution_id)
                try:
                    result = await coro
                    self._results[execution_id] = result
                    logger.info("Worker completed for execution %d", execution_id)
                    return result
                except asyncio.CancelledError:
                    logger.warning("Worker cancelled for execution %d", execution_id)
                    raise
                except Exception:
                    logger.exception("Worker failed for execution %d", execution_id)
                    raise
                finally:
                    self._tasks.pop(execution_id, None)

        task = asyncio.create_task(_wrapped(), name=f"worker-{execution_id}")
        self._tasks[execution_id] = task
        return task

    def cancel(self, execution_id: int) -> bool:
        """Cancel a running task by execution_id."""
        task = self._tasks.get(execution_id)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled worker for execution %d", execution_id)
            return True
        return False

    async def shutdown(self) -> None:
        """Gracefully cancel all running tasks."""
        logger.info("Shutting down worker pool (%d active tasks)", self.active_count)
        for eid, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        # Wait for all tasks to finish cancellation
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        logger.info("Worker pool shutdown complete")

    def get_status(self) -> dict[str, Any]:
        return {
            "max_concurrent": self._max,
            "active_count": self.active_count,
            "total_tracked": len(self._tasks),
            "active_executions": [
                eid for eid, t in self._tasks.items() if not t.done()
            ],
        }
