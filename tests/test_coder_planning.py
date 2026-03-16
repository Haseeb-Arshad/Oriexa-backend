from __future__ import annotations

import json
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


def test_normalize_plan_strips_protected_framework_files():
    from agents.coder_agent import _normalize_plan

    plan = _normalize_plan(
        {
            "project_type": "nextjs",
            "steps": [
                {
                    "description": (
                        "Create a detailed dashboard screen with a real page layout, responsive sections, "
                        "and user-facing content that fits into a standard Next.js app shell."
                    ),
                    "files": [
                        {"path": "package.json", "description": "Do not allow this."},
                        {"path": "app/page.tsx", "description": "Safe page implementation."},
                    ],
                }
            ],
            "test_command": "npm run build",
        },
        "Dashboard",
        "Create a dashboard.",
        "Use Next.js.",
    )

    files = plan["steps"][0]["files"]
    assert all(file["path"] != "package.json" for file in files)
    assert any(file["path"] == "app/page.tsx" for file in files)


def test_generate_step_code_filters_protected_framework_files(tmp_path: Path):
    from agents.coder_agent import generate_step_code

    step = {
        "step_number": 2,
        "description": "Build the main page.",
        "files": [{"path": "app/page.tsx", "description": "Main page"}],
    }

    with patch(
        "agents.coder_agent.llm_json",
        return_value={
            "files": [
                {"path": "package.json", "content": '{"dependencies":{"next":"15.0.0"}}'},
                {"path": "app/page.tsx", "content": "export default function Page() { return <main>ok</main>; }"},
            ]
        },
    ):
        files = generate_step_code(
            step,
            "Dashboard",
            "Create a dashboard.",
            "Use Next.js.",
            "Blueprint",
            [],
            [],
            task_dir=tmp_path,
            project_type="nextjs",
        )

    assert [file["path"] for file in files] == ["app/page.tsx"]


def test_fix_build_errors_filters_protected_framework_files(tmp_path: Path):
    from agents.coder_agent import _fix_build_errors

    (tmp_path / "app").mkdir()
    (tmp_path / "app/page.tsx").write_text("export default function Page() { return <main>broken</main>; }", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.0.0", "react": "19.0.0", "react-dom": "19.0.0"}}),
        encoding="utf-8",
    )

    with patch(
        "agents.coder_agent.llm_json",
        return_value={
            "files": [
                {"path": "package.json", "content": '{"dependencies":{"next":"99.0.0"}}'},
                {"path": "app/page.tsx", "content": "export default function Page() { return <main>fixed</main>; }"},
            ]
        },
    ), patch("agents.coder_agent.append_build_log"):
        files = _fix_build_errors(
            "Error in app/page.tsx",
            "Dashboard",
            "Create a dashboard.",
            "Use Next.js.",
            "Blueprint",
            ["app/page.tsx"],
            [],
            "",
            tmp_path,
            project_type="nextjs",
        )

    assert [file["path"] for file in files] == ["app/page.tsx"]


def test_workspace_integrity_detects_protected_core_drift(tmp_path: Path):
    from agents.coder_agent import _capture_protected_core_snapshot, _workspace_integrity_issues

    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.0.0", "react": "19.0.0", "react-dom": "19.0.0"}}),
        encoding="utf-8",
    )
    (tmp_path / "app").mkdir()

    state = {"plan": {"project_type": "nextjs"}}
    state["protected_core_snapshot"] = _capture_protected_core_snapshot(tmp_path, "nextjs")

    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.0.0"}}),
        encoding="utf-8",
    )

    issues = _workspace_integrity_issues(tmp_path, state)

    assert any("protected framework core file 'package.json' changed unexpectedly" in issue for issue in issues)
