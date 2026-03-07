"""Tests for the LangGraph supervisor graph structure and routing."""

import pytest

from app.orchestrator.supervisor import (
    build_supervisor_graph,
    route_after_planning,
    route_after_review,
    route_after_triage,
)


class TestGraphRouting:
    """Test the conditional edge routing functions."""

    def test_route_after_triage_needs_clarification(self):
        state = {"needs_clarification": True}
        assert route_after_triage(state) == "planning"

    def test_route_after_triage_can_still_clarify_when_flag_disabled(self):
        state = {
            "needs_clarification": True,
            "clarity_score": 0.1,
            "disable_post_claim_clarification": False,
        }
        assert route_after_triage(state) == "clarification"

    def test_route_after_triage_clear(self):
        state = {"needs_clarification": False}
        assert route_after_triage(state) == "planning"

    def test_route_after_triage_default(self):
        state = {}
        assert route_after_triage(state) == "planning"

    def test_route_after_planning_low_complexity(self):
        state = {"complexity": "low", "task_data": {"budget_credits": 50}}
        assert route_after_planning(state) == "execution"

    def test_route_after_planning_medium_complexity(self):
        state = {"complexity": "medium", "task_data": {"budget_credits": 100}}
        assert route_after_planning(state) == "execution"

    def test_route_after_planning_high_complexity(self):
        state = {"complexity": "high", "task_data": {"budget_credits": 100}}
        assert route_after_planning(state) == "complex_execution"

    def test_route_after_planning_high_budget(self):
        state = {"complexity": "low", "task_data": {"budget_credits": 600}}
        assert route_after_planning(state) == "complex_execution"

    def test_route_after_review_passed(self):
        state = {"review_passed": True}
        assert route_after_review(state) == "deployment"

    def test_route_after_review_failed_retry(self):
        state = {
            "review_passed": False,
            "attempt_count": 1,
            "max_attempts": 3,
            "review_score": 80,
            "complexity": "low",
            "task_data": {"budget_credits": 100},
        }
        assert route_after_review(state) == "execution"

    def test_route_after_review_failed_low_score_escalates(self):
        state = {
            "review_passed": False,
            "attempt_count": 1,
            "max_attempts": 3,
            "review_score": 45,
            "complexity": "low",
            "task_data": {"budget_credits": 100},
        }
        assert route_after_review(state) == "complex_execution"

    def test_route_after_review_failed_max_attempts(self):
        state = {"review_passed": False, "attempt_count": 3, "max_attempts": 3}
        assert route_after_review(state) == "failed"

    def test_route_after_review_failed_no_max(self):
        state = {"review_passed": False, "attempt_count": 5}
        assert route_after_review(state) == "failed"


class TestGraphStructure:
    """Test that the graph builds correctly."""

    def test_build_graph(self):
        graph = build_supervisor_graph()
        assert graph is not None

    def test_graph_has_all_nodes(self):
        graph = build_supervisor_graph()
        node_names = set(graph.nodes.keys())
        expected = {
            "triage", "clarification", "wait_for_response",
            "planning", "execution", "complex_execution",
            "review", "deployment", "delivery", "failed",
        }
        assert expected.issubset(node_names)

    def test_graph_compiles(self):
        graph = build_supervisor_graph()
        compiled = graph.compile()
        assert compiled is not None
