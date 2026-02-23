"""Tests for the worker pool and task picker daemon."""

import asyncio

import pytest

from app.orchestrator.concurrency import WorkerPool


class TestWorkerPool:
    """Test the WorkerPool concurrency manager."""

    @pytest.mark.asyncio
    async def test_has_capacity_initial(self):
        pool = WorkerPool(max_concurrent=3)
        assert pool.has_capacity() is True
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_submit_and_complete(self):
        pool = WorkerPool(max_concurrent=3)

        async def simple_task():
            return "done"

        task = await pool.submit(simple_task(), execution_id=1)
        result = await task
        assert result == "done"

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        pool = WorkerPool(max_concurrent=3)

        async def long_task():
            await asyncio.sleep(100)

        await pool.submit(long_task(), execution_id=1)
        # Give the task a moment to start
        await asyncio.sleep(0.01)
        assert pool.cancel(1) is True

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self):
        pool = WorkerPool(max_concurrent=3)
        assert pool.cancel(999) is False

    @pytest.mark.asyncio
    async def test_concurrent_limit(self):
        pool = WorkerPool(max_concurrent=2)
        started = []

        async def tracked_task(task_id):
            started.append(task_id)
            await asyncio.sleep(0.1)
            return task_id

        t1 = await pool.submit(tracked_task(1), execution_id=1)
        t2 = await pool.submit(tracked_task(2), execution_id=2)
        t3 = await pool.submit(tracked_task(3), execution_id=3)

        # Wait for all to complete
        results = await asyncio.gather(t1, t2, t3)
        assert set(results) == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_shutdown(self):
        pool = WorkerPool(max_concurrent=3)

        async def long_task():
            await asyncio.sleep(100)

        await pool.submit(long_task(), execution_id=1)
        await pool.submit(long_task(), execution_id=2)

        await asyncio.sleep(0.01)
        await pool.shutdown()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_get_status(self):
        pool = WorkerPool(max_concurrent=5)
        status = pool.get_status()
        assert status["max_concurrent"] == 5
        assert status["active_count"] == 0

    @pytest.mark.asyncio
    async def test_exception_in_task(self):
        pool = WorkerPool(max_concurrent=3)

        async def failing_task():
            raise ValueError("boom")

        task = await pool.submit(failing_task(), execution_id=1)
        with pytest.raises(ValueError, match="boom"):
            await task
