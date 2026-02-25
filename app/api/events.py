"""SSE broadcast endpoint for per-user real-time task events."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/user/events", tags=["events"])


@dataclass
class TaskEvent:
    event_type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


class EventBroadcaster:
    """In-memory pub/sub for per-user SSE events."""

    def __init__(self) -> None:
        self._subscribers: dict[int, list[asyncio.Queue[TaskEvent | None]]] = {}

    def subscribe(self, user_id: int) -> asyncio.Queue[TaskEvent | None]:
        queue: asyncio.Queue[TaskEvent | None] = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(user_id, []).append(queue)
        logger.info("User %s subscribed (total: %d)", user_id, len(self._subscribers[user_id]))
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue[TaskEvent | None]) -> None:
        subs = self._subscribers.get(user_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(user_id, None)
        logger.info("User %s unsubscribed", user_id)

    def broadcast(self, user_id: int, event_type: str, data: dict[str, Any]) -> None:
        event = TaskEvent(event_type=event_type, data=data)
        subs = self._subscribers.get(user_id, [])
        for queue in subs:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Queue full for user %s, dropping event", user_id)


# Global singleton
event_broadcaster = EventBroadcaster()


@router.get("/stream")
async def stream_user_events(user_id: int = Query(..., alias="userId")):
    """SSE stream for all task-related events for a given user."""

    queue = event_broadcaster.subscribe(user_id)

    async def event_generator():
        try:
            # Send initial connection event
            yield _format_sse("connected", {"user_id": user_id})

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if event is None:
                        break
                    yield _format_sse(event.event_type, event.data)
                except asyncio.TimeoutError:
                    # Heartbeat
                    yield f": heartbeat {int(time.time())}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_broadcaster.unsubscribe(user_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
