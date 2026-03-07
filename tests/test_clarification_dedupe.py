"""Tests for clarification/question deduplication helpers."""

from app.agents.clarification import _extract_existing_question_state, _format_answered
from app.orchestrator.task_picker import _message_requests_replan
from app.tools.communication import _normalize_options, _normalize_question_text


def test_extract_existing_question_state_splits_pending_and_answered():
    messages = [
        {
            "id": 10,
            "sender_type": "agent",
            "message_type": "question",
            "content": "Which mode?",
            "structured_data": {"question_type": "multiple_choice"},
        },
        {
            "id": 11,
            "sender_type": "agent",
            "message_type": "question",
            "content": "Preferred style?",
            "structured_data": {"response": "Neon", "responded_at": "2026-03-07T00:00:00Z"},
        },
        {
            "id": 12,
            "sender_type": "poster",
            "message_type": "question",
            "content": "Ignore me",
            "structured_data": {},
        },
    ]

    state = _extract_existing_question_state(messages)
    assert state["pending_ids"] == [10]
    assert len(state["answered_items"]) == 1
    assert state["answered_items"][0]["question"] == "Preferred style?"


def test_format_answered_handles_empty_and_populated():
    assert _format_answered([]) == "(none)"
    formatted = _format_answered([{"question": "Q1", "answer": "A1"}])
    assert "Q1" in formatted
    assert "A1" in formatted


def test_question_text_normalization():
    assert _normalize_question_text("  Which   mode   do YOU want? ") == "which mode do you want?"


def test_options_normalization():
    assert _normalize_options(["  Yes ", "No  "]) == ["yes", "no"]
    assert _normalize_options(None) == []


def test_replan_message_detection():
    assert _message_requests_replan("Please change requirement and update the plan.") is True
    assert _message_requests_replan("Looks good, continue execution.") is False
