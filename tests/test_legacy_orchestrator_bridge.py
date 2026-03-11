from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db.models import OrchTaskExecution, Task
from tests.conftest import test_session_factory


@pytest.mark.asyncio
async def test_active_endpoint_creates_legacy_execution(client, registered_user, agent_with_key):
    async with test_session_factory() as session:
        task = Task(
            poster_id=registered_user["id"],
            title="Legacy worker task",
            description="Task already claimed and being processed by the legacy worker.",
            requirements="Show progress in the dashboard.",
            budget_credits=150,
            category_id=1,
            status="in_progress",
            claimed_by_agent_id=agent_with_key["agent_id"],
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        task_id = task.id

    resp = await client.get(f"/orchestrator/tasks/by-task/{task_id}/active")
    assert resp.status_code == 200

    payload = resp.json()
    assert payload["ok"] is True
    assert payload["data"]["execution_id"] >= 1
    assert payload["data"]["status"] == "planning"

    async with test_session_factory() as session:
        result = await session.get(OrchTaskExecution, payload["data"]["execution_id"])
        assert result is not None
        assert result.oriexa_task_id == task_id
        assert result.graph_thread_id == f"legacy-worker-{task_id}"


@pytest.mark.asyncio
async def test_preview_falls_back_to_legacy_plan_subtasks(client, tmp_path):
    workspace = Path(tmp_path) / "task_77"
    workspace.mkdir()
    (workspace / ".implementation_plan.json").write_text(
        json.dumps(
            {
                "steps": [
                    {"step_number": 1, "description": "Scaffold app", "commit_message": "chore: scaffold"},
                    {"step_number": 2, "description": "Implement gameplay", "commit_message": "feat: gameplay"},
                    {"step_number": 3, "description": "Polish visuals", "commit_message": "style: polish"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (workspace / ".swarm_state.json").write_text(
        json.dumps(
            {
                "status": "coding",
                "current_step": 1,
                "completed_steps": [
                    {"step_number": 1, "description": "Scaffold app"},
                ],
            }
        ),
        encoding="utf-8",
    )

    async with test_session_factory() as session:
        execution = OrchTaskExecution(
            oriexa_task_id=77,
            status="planning",
            task_snapshot={"title": "Legacy roadmap"},
            workspace_path=str(workspace),
        )
        session.add(execution)
        await session.commit()
        await session.refresh(execution)
        execution_id = execution.id

    resp = await client.get(f"/orchestrator/preview/executions/{execution_id}")
    assert resp.status_code == 200

    payload = resp.json()
    subtasks = payload["data"]["subtasks"]
    assert [s["title"] for s in subtasks] == [
        "Scaffold app",
        "Implement gameplay",
        "Polish visuals",
    ]
    assert [s["status"] for s in subtasks] == [
        "completed",
        "in_progress",
        "pending",
    ]
