from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_summarize_focus_strips_request_noise():
    from agents.coder_agent import _summarize_focus

    focus = _summarize_focus("tic-tac-toe game i want you to build a")

    assert focus == "tic-tac-toe game"


def test_fallback_plan_prefers_static_for_simple_game():
    from agents.coder_agent import DEFAULT_STATIC_TEST_COMMAND, _build_fallback_plan

    plan = _build_fallback_plan(
        "Tic-Tac-Toe Game",
        "I want you to build a tic-tac-toe game in the browser.",
        "Keep it self-contained and playable without a backend.",
    )

    assert plan["project_type"] == "static"
    assert plan["scaffold_command"] is None
    assert plan["test_command"] == DEFAULT_STATIC_TEST_COMMAND
    assert "i want you to build a" not in plan["steps"][0]["description"].lower()
    assert any(file["path"] == "index.html" for file in plan["steps"][0]["files"])


def test_normalize_plan_keeps_single_detailed_static_step():
    from agents.coder_agent import DEFAULT_STATIC_TEST_COMMAND, _normalize_plan

    plan = _normalize_plan(
        {
            "project_type": "static",
            "steps": [
                {
                    "description": (
                        "Create a polished tic-tac-toe board in a single browser page with clear turn status, "
                        "responsive spacing, and keyboard-friendly controls that work without any framework."
                    ),
                    "commit_message": "",
                }
            ],
            "test_command": "",
        },
        "Tic-Tac-Toe Game",
        "Simple browser game.",
        "Use plain HTML, CSS, and JavaScript.",
    )

    assert plan["project_type"] == "static"
    assert len(plan["steps"]) == 1
    assert "next.js foundation" not in plan["steps"][0]["description"].lower()
    assert any(file["path"] == "index.html" for file in plan["steps"][0]["files"])
    assert plan["test_command"] == DEFAULT_STATIC_TEST_COMMAND


def test_return_coder_failure_marks_failed_after_repeats(tmp_path: Path):
    from agents.coder_agent import _return_coder_failure

    task_dir = tmp_path / "task_1"
    task_dir.mkdir()
    state_file = task_dir / ".swarm_state.json"
    state: dict = {"status": "coding"}

    with patch("agents.coder_agent.MAX_CODER_FAILURE_REPEATS", 2), patch(
        "agents.coder_agent._save_state"
    ), patch("agents.coder_agent.write_progress"), patch("agents.coder_agent.append_build_log"):
        first = _return_coder_failure(
            state_file=state_file,
            task_dir=task_dir,
            state=state,
            task_id=1,
            error_code="same_error",
            detail="Repeated coding blocker",
        )
        second = _return_coder_failure(
            state_file=state_file,
            task_dir=task_dir,
            state=state,
            task_id=1,
            error_code="same_error",
            detail="Repeated coding blocker",
        )

    assert first["terminal"] is False
    assert second["terminal"] is True
    assert state["status"] == "failed"
