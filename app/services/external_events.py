from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExternalEvent:
    event_type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)


def user_channel(user_id: int) -> str:
    return f"user:{user_id}"


def agent_channel(agent_id: int) -> str:
    return f"agent:{agent_id}"


class ExternalEventBroadcaster:
    """In-memory fan-out channel broadcaster for external actor SSE streams."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[ExternalEvent | None]]] = {}

    def subscribe(self, channels: list[str]) -> asyncio.Queue[ExternalEvent | None]:
        queue: asyncio.Queue[ExternalEvent | None] = asyncio.Queue(maxsize=256)
        for channel in channels:
            self._subscribers.setdefault(channel, []).append(queue)
        return queue

    def unsubscribe(self, channels: list[str], queue: asyncio.Queue[ExternalEvent | None]) -> None:
        for channel in channels:
            subscribers = self._subscribers.get(channel, [])
            if queue in subscribers:
                subscribers.remove(queue)
            if not subscribers:
                self._subscribers.pop(channel, None)

    def broadcast(self, channel: str, event_type: str, data: dict[str, Any]) -> None:
        event = ExternalEvent(event_type=event_type, data=data)
        for queue in self._subscribers.get(channel, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue

    def broadcast_many(self, channels: list[str], event_type: str, data: dict[str, Any]) -> None:
        for channel in channels:
            self.broadcast(channel, event_type, data)


external_event_broadcaster = ExternalEventBroadcaster()
