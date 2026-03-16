from __future__ import annotations

import json
from pathlib import Path


def test_optional_dependency_failure_is_detected_and_summarized():
    from agents.shell_executor import has_optional_dependency_issue, summarize_failure_output

    output = (
        "Error evaluating Node.js code\n"
        "Error: Cannot find native binding. npm has a bug related to optional dependencies.\n"
        "Cannot find module '@tailwindcss/oxide-linux-x64-gnu'\n"
    )

    assert has_optional_dependency_issue(output) is True
    summary = summarize_failure_output("npm run build", output)
    assert "optional dependency recovery" in summary.lower()


def test_render_readme_includes_final_deployment_details(tmp_path: Path):
    from agents.deploy_agent import _render_readme

    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                "scripts": {"dev": "next dev", "build": "next build"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "app").mkdir()

    readme = _render_readme(
        task_id=89,
        title="Tic Tac Toe",
        description="A polished browser game.",
        requirements="Build a production-ready tic-tac-toe game.",
        repo_url="https://github.com/example/tic-tac-toe",
        task_dir=tmp_path,
        state={
            "completed_steps": [{"description": "Built the full tic-tac-toe experience"}],
            "vercel_url": "https://private-task.vercel.app",
            "smoke_test": {"passed": False, "details": "HTTP 401 - deployment is protected/private."},
            "deployment_mode": "production_private",
        },
    )

    assert "## Deployment" in readme
    assert "https://private-task.vercel.app" in readme
    assert "access-protected/private" in readme


def test_private_vercel_deploy_still_submits_deliverable(tmp_path: Path, monkeypatch):
    from agents import deploy_agent

    class DummyClient:
        def __init__(self) -> None:
            self.deliveries: list[tuple[int, str]] = []

        def get_task(self, task_id: int) -> dict:
            return {
                "title": "Tic Tac Toe",
                "description": "Ship a playable browser game.",
                "requirements": "Responsive UI, polished gameplay, final README.",
            }

        def submit_deliverable(self, task_id: int, content: str) -> None:
            self.deliveries.append((task_id, content))

    state = {
        "status": "deploying",
        "repo_url": "https://github.com/example/tic-tac-toe",
        "completed_steps": [{"description": "Built the full tic-tac-toe experience"}],
    }

    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"next": "15.0.0"}, "scripts": {"build": "next build"}}),
        encoding="utf-8",
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "page.tsx").write_text("export default function Page() { return null; }", encoding="utf-8")

    monkeypatch.setattr(deploy_agent, "ensure_local_workspace", lambda task_id, workspace_root=None: (tmp_path, None, False))
    monkeypatch.setattr(deploy_agent, "load_swarm_state", lambda task_id, workspace_dir=None: state)
    monkeypatch.setattr(deploy_agent, "write_swarm_state", lambda task_id, state, workspace_dir=None: None)
    monkeypatch.setattr(deploy_agent, "cleanup_workspace", lambda task_id, reason=None, workspace_dir=None: False)
    monkeypatch.setattr(deploy_agent, "write_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy_agent, "append_build_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy_agent, "has_meaningful_implementation", lambda task_dir: True)
    monkeypatch.setattr(deploy_agent, "verify_remote_has_main", lambda task_dir: True)
    monkeypatch.setattr(deploy_agent, "verify_remote_head_matches_local", lambda task_dir: True)
    monkeypatch.setattr(deploy_agent, "push_to_remote", lambda task_dir: True)
    monkeypatch.setattr(deploy_agent, "append_commit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy_agent, "commit_step", lambda *args, **kwargs: "abc123")
    monkeypatch.setattr(
        deploy_agent,
        "run_vercel_deploy",
        lambda task_dir, production=True, force_unlinked=False: "https://private-task.vercel.app" if production else None,
    )
    monkeypatch.setattr(
        deploy_agent,
        "smoke_test",
        lambda url, retries=3, wait=10: (False, "HTTP 401 - deployment is protected/private."),
    )
    monkeypatch.setattr(deploy_agent, "smart_llm_call", lambda **kwargs: "")

    client = DummyClient()
    result = deploy_agent.process_task(client, 89)

    assert result["action"] == "delivered"
    assert client.deliveries
    assert "private" in client.deliveries[0][1].lower()
    assert "README.md" in client.deliveries[0][1]
    assert "https://private-task.vercel.app" in (tmp_path / "README.md").read_text(encoding="utf-8")
