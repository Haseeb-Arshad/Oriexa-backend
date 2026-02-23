"""Live progress tracking for task executions.

Stores in-memory progress steps per execution and provides async iteration
for SSE streaming. Each step has a natural language description, phase info,
and timing metadata.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProgressStep:
    """A single progress event in a task execution."""
    phase: str
    title: str
    description: str
    detail: str = ""
    progress_pct: int = 0  # 0-100
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# Human-readable phase descriptions
PHASE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "triage": {
        "start": "Reading through your task to understand exactly what you need",
        "thinking": "Evaluating the complexity and figuring out the best approach",
        "done": "Got a clear picture of what needs to be done",
    },
    "clarification": {
        "start": "Noticed a few things that could use some clarification",
        "thinking": "Crafting thoughtful questions to make sure nothing is missed",
        "done": "Questions sent — waiting for your response",
    },
    "planning": {
        "start": "Mapping out the game plan and breaking this into clear steps",
        "exploring": "Exploring the codebase to understand the existing structure",
        "thinking": "Designing the architecture and ordering the work",
        "done": "Plan is ready — every step laid out with dependencies",
    },
    "execution": {
        "start": "Rolling up sleeves and getting to work on the implementation",
        "writing": "Writing code, creating files, and building things out",
        "testing": "Running tests and verifying everything works as expected",
        "iterating": "Found something to improve — refining the implementation",
        "done": "Implementation complete — all pieces are in place",
    },
    "complex_execution": {
        "start": "This is a deep one — bringing in the heavy-duty reasoning",
        "analyzing": "Carefully analyzing edge cases and architectural implications",
        "writing": "Building out the implementation with extra attention to detail",
        "testing": "Running comprehensive tests and stress-testing the solution",
        "done": "Complex implementation finished — thoroughly tested and verified",
    },
    "review": {
        "start": "Stepping back to review everything with fresh eyes",
        "thinking": "Checking completeness, code quality, and testing coverage",
        "scoring": "Rating the work across multiple quality dimensions",
        "done": "Review complete — quality assessment finalized",
    },
    "delivery": {
        "start": "Packaging everything up for delivery",
        "submitting": "Submitting the deliverable with a detailed summary",
        "done": "Delivered! Your task is complete and ready for review",
    },
    "failed": {
        "start": "Something went wrong — documenting what happened",
        "done": "Logged the issue for investigation",
    },
}

# Phase icons for the UI
PHASE_ICONS: dict[str, str] = {
    "triage": "eye",
    "clarification": "message-circle",
    "planning": "map",
    "execution": "code",
    "complex_execution": "brain",
    "review": "check-circle",
    "delivery": "package",
    "failed": "alert-triangle",
}

# Approximate progress percentages per phase
PHASE_PROGRESS: dict[str, int] = {
    "triage": 10,
    "clarification": 15,
    "planning": 30,
    "execution": 70,
    "complex_execution": 70,
    "review": 85,
    "delivery": 100,
    "failed": 100,
}


class ProgressTracker:
    """Tracks progress steps for all active executions.

    Supports async iteration for SSE streaming — clients can subscribe
    to a specific execution and receive steps as they happen.
    """

    def __init__(self) -> None:
        self._steps: dict[int, list[ProgressStep]] = {}
        self._events: dict[int, asyncio.Event] = {}
        self._cursors: dict[str, int] = {}  # subscriber_id -> last_seen_index

    def add_step(
        self,
        execution_id: int,
        phase: str,
        stage: str = "start",
        detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ProgressStep:
        """Record a progress step and notify waiting subscribers."""
        descriptions = PHASE_DESCRIPTIONS.get(phase, {})
        title = phase.replace("_", " ").title()
        description = descriptions.get(stage, f"Working on {phase}...")
        progress_pct = PHASE_PROGRESS.get(phase, 50)

        # Adjust progress within phase based on stage
        if stage == "start":
            progress_pct = max(progress_pct - 15, 0)
        elif stage == "thinking" or stage == "exploring" or stage == "analyzing":
            progress_pct = max(progress_pct - 8, 0)
        elif stage == "done":
            pass  # use full phase progress

        step = ProgressStep(
            phase=phase,
            title=title,
            description=description,
            detail=detail,
            progress_pct=progress_pct,
            metadata=metadata or {},
        )

        if execution_id not in self._steps:
            self._steps[execution_id] = []
            self._events[execution_id] = asyncio.Event()

        self._steps[execution_id].append(step)

        # Notify all waiting subscribers
        event = self._events.get(execution_id)
        if event:
            event.set()
            # Reset for next wait
            self._events[execution_id] = asyncio.Event()

        return step

    def get_steps(self, execution_id: int) -> list[ProgressStep]:
        """Get all progress steps for an execution."""
        return self._steps.get(execution_id, [])

    async def subscribe(self, execution_id: int, last_index: int = 0):
        """Async generator that yields new steps as they arrive.

        Yields (index, step) tuples. Blocks until new steps appear.
        """
        while True:
            steps = self._steps.get(execution_id, [])

            # Yield any new steps
            while last_index < len(steps):
                yield last_index, steps[last_index]
                last_index += 1

            # Check if execution is done
            if steps and steps[-1].phase in ("delivery", "failed"):
                return

            # Wait for new steps
            if execution_id not in self._events:
                self._events[execution_id] = asyncio.Event()

            try:
                await asyncio.wait_for(
                    self._events[execution_id].wait(),
                    timeout=30.0,  # Heartbeat every 30s
                )
            except asyncio.TimeoutError:
                # Send a keepalive — yield None to indicate heartbeat
                yield -1, None

    def cleanup(self, execution_id: int) -> None:
        """Remove tracking data for a completed execution."""
        self._steps.pop(execution_id, None)
        self._events.pop(execution_id, None)

    def get_active_executions(self) -> list[int]:
        """List execution IDs that have active progress tracking."""
        return list(self._steps.keys())


# Global singleton
progress_tracker = ProgressTracker()
