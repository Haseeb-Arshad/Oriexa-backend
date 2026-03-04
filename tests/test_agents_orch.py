"""Tests for orchestrator agents (unit tests with mocked LLM)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTriageAgent:
    """Test the TriageAgent."""

    @pytest.mark.asyncio
    async def test_triage_clear_task(self):
        from app.agents.triage import TriageAgent

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "clarity_score": 0.9,
            "complexity": "low",
            "needs_clarification": False,
            "reasoning": "Task is well-defined with clear requirements.",
        })
        mock_response.response_metadata = {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}

        with patch("app.agents.triage.TriageAgent.get_model") as mock_get_model:
            mock_model = AsyncMock()
            mock_model.ainvoke = AsyncMock(return_value=mock_response)
            mock_get_model.return_value = mock_model

            agent = TriageAgent()
            result = await agent.run({
                "task_data": {
                    "title": "Build a REST API",
                    "description": "Create a Python Flask REST API with CRUD endpoints for a todo list.",
                    "budget_credits": 100,
                },
            })

        assert result["clarity_score"] == 0.9
        assert result["complexity"] == "low"
        assert result["needs_clarification"] is False

    @pytest.mark.asyncio
    async def test_triage_unclear_task(self):
        from app.agents.triage import TriageAgent

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "clarity_score": 0.3,
            "complexity": "high",
            "needs_clarification": True,
            "reasoning": "Requirements are vague.",
        })
        mock_response.response_metadata = {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}

        with patch("app.agents.triage.TriageAgent.get_model") as mock_get_model:
            mock_model = AsyncMock()
            mock_model.ainvoke = AsyncMock(return_value=mock_response)
            mock_get_model.return_value = mock_model

            agent = TriageAgent()
            result = await agent.run({
                "task_data": {"title": "Make it better", "description": "Fix things"},
            })

        assert result["clarity_score"] == 0.3
        assert result["needs_clarification"] is True

    @pytest.mark.asyncio
    async def test_triage_malformed_json_fallback(self):
        from app.agents.triage import TriageAgent

        mock_response = MagicMock()
        mock_response.content = "This is not JSON at all"
        mock_response.response_metadata = {}

        with patch("app.agents.triage.TriageAgent.get_model") as mock_get_model:
            mock_model = AsyncMock()
            mock_model.ainvoke = AsyncMock(return_value=mock_response)
            mock_get_model.return_value = mock_model

            agent = TriageAgent()
            result = await agent.run({"task_data": {}})

        # Should fall back to defaults
        assert result["clarity_score"] == 0.5
        assert result["needs_clarification"] is True


class TestReviewAgent:
    """Test the ReviewAgent."""

    @pytest.mark.asyncio
    async def test_review_pass(self):
        from app.agents.review import ReviewAgent

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "score": 85,
            "passed": True,
            "feedback": "Well implemented with good code quality.",
        })
        mock_response.response_metadata = {"token_usage": {"prompt_tokens": 200, "completion_tokens": 100}}

        with patch("app.agents.review.ReviewAgent.get_model") as mock_get_model:
            mock_model = AsyncMock()
            mock_model.ainvoke = AsyncMock(return_value=mock_response)
            mock_get_model.return_value = mock_model

            agent = ReviewAgent()
            result = await agent.run({
                "task_data": {"title": "Build API", "description": "REST API"},
                "deliverable_content": "Implemented REST API with all endpoints.",
                "plan": [],
                "subtask_results": [],
                "attempt_count": 0,
            })

        assert result["score"] == 85
        assert result["passed"] is True

    @pytest.mark.asyncio
    async def test_review_fail(self):
        from app.agents.review import ReviewAgent

        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "score": 40,
            "passed": False,
            "feedback": "Missing several required endpoints. No tests.",
        })
        mock_response.response_metadata = {"token_usage": {"prompt_tokens": 200, "completion_tokens": 100}}

        with patch("app.agents.review.ReviewAgent.get_model") as mock_get_model:
            mock_model = AsyncMock()
            mock_model.ainvoke = AsyncMock(return_value=mock_response)
            mock_get_model.return_value = mock_model

            agent = ReviewAgent()
            result = await agent.run({
                "task_data": {"title": "Build API", "description": "REST API"},
                "deliverable_content": "Started on it.",
                "plan": [],
                "subtask_results": [],
                "attempt_count": 0,
            })

        assert result["score"] == 40
        assert result["passed"] is False
        assert result["attempt_count"] == 1
