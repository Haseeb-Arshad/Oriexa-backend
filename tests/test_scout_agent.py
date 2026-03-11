from __future__ import annotations

from unittest.mock import patch


class _FakeClient:
    def __init__(self, task_map: dict[int, list[dict]]):
        self.task_map = task_map
        self.agent_id = 7
        self.requested_task_ids: list[int] = []
        self.claim_calls: list[tuple[int, int, str]] = []

    def browse_tasks(self, status: str, limit: int = 20):
        assert status == "open"
        return [
            {"id": task_id, "updated_at": versions[0]["updated_at"]}
            for task_id, versions in self.task_map.items()
        ][:limit]

    def get_task(self, task_id: int):
        self.requested_task_ids.append(task_id)
        versions = self.task_map[task_id]
        if len(versions) > 1:
            return versions.pop(0)
        return versions[0]

    def get_task_messages(self, task_id: int):
        return []

    def claim_task(self, task_id: int, proposed_credits: int, message: str):
        self.claim_calls.append((task_id, proposed_credits, message))
        return {"ok": True, "data": {"id": 99}}


def _answered_task(task_id: int, *, updated_at: str, status: str = "open") -> dict:
    return {
        "id": task_id,
        "status": status,
        "title": f"Task {task_id}",
        "description": "Build a frontend game with strong visuals.",
        "requirements": "Use React and make it polished.",
        "budget_credits": 100,
        "updated_at": updated_at,
        "agent_remarks": [
            {
                "agent_id": 7,
                "timestamp": "2026-03-11T03:00:00+00:00",
                "evaluation": {
                    "questions": [
                        {"text": "What style?", "answer": "Arcade neon"},
                    ]
                },
            }
        ],
        "category": {"name": "Frontend"},
    }


def test_run_scout_claims_high_confidence_answered_task_without_scanning_rest():
    from agents.scout_agent import run_scout

    client = _FakeClient(
        {
            58: [_answered_task(58, updated_at="2026-03-11T03:05:00+00:00")],
            59: [_answered_task(59, updated_at="2026-03-11T03:06:00+00:00")],
        }
    )

    with patch(
        "agents.scout_agent.evaluate_task",
        return_value={
            "should_claim": True,
            "confidence": "high",
            "proposed_credits": 100,
            "approach": "Build the UI, wire interactions, and polish the visuals.",
        },
    ):
        result = run_scout(client, ["react", "vite", "typescript"])

    assert result["action"] == "claimed"
    assert result["task_id"] == 58
    assert client.requested_task_ids == [58, 58]
    assert len(client.claim_calls) == 1
    assert client.claim_calls[0][0] == 58
    assert "Task 58" in client.claim_calls[0][2]


def test_run_scout_skips_stale_claim_if_task_changed_before_submit():
    from agents.scout_agent import run_scout

    client = _FakeClient(
        {
            58: [
                _answered_task(58, updated_at="2026-03-11T03:05:00+00:00", status="open"),
                _answered_task(58, updated_at="2026-03-11T03:05:30+00:00", status="claimed"),
            ]
        }
    )

    with patch(
        "agents.scout_agent.evaluate_task",
        return_value={
            "should_claim": True,
            "confidence": "high",
            "proposed_credits": 100,
            "approach": "Build the UI, wire interactions, and polish the visuals.",
        },
    ):
        result = run_scout(client, ["react"])

    assert result["action"] == "stale_task"
    assert result["task_id"] == 58
    assert result["status"] == "claimed"
    assert client.claim_calls == []
