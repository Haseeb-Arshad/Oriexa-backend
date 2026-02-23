"""Internal event bus for cross-component signaling."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

EventHandler = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Simple async event bus for internal orchestrator events."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def on(self, event: str, handler: EventHandler) -> None:
        """Register a handler for an event type."""
        self._handlers[event].append(handler)

    def off(self, event: str, handler: EventHandler) -> None:
        """Remove a handler for an event type."""
        handlers = self._handlers.get(event, [])
        if handler in handlers:
            handlers.remove(handler)

    async def emit(self, event: str, **kwargs: Any) -> None:
        """Emit an event to all registered handlers."""
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            try:
                await handler(**kwargs)
            except Exception:
                logger.exception("Event handler error for %s", event)

    def emit_background(self, event: str, **kwargs: Any) -> None:
        """Emit an event in a fire-and-forget background task."""
        asyncio.create_task(self.emit(event, **kwargs))


# Global event bus instance
event_bus = EventBus()

# Event type constants
TASK_CLAIMED = "task.claimed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"
AGENT_STARTED = "agent.started"
AGENT_FINISHED = "agent.finished"
DELIVERABLE_SUBMITTED = "deliverable.submitted"
