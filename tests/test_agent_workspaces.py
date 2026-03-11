from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents import git_ops
from app.services import agent_workspaces as aw


def _write_metadata_timestamp(meta_path: Path, ts: datetime) -> None:
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    iso_ts = ts.isoformat()
    payload["last_activity_at"] = iso_ts
    payload["updated_at"] = iso_ts
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_cleanup_workspace_removes_task_dir_and_keeps_metadata(tmp_path: Path):
    workspace_root = tmp_path / "agent_works"
    task_dir = workspace_root / "task_101"
    task_dir.mkdir(parents=True)
    (task_dir / "README.md").write_text("hello", encoding="utf-8")

    aw.update_workspace_metadata(
        101,
        workspace_root=workspace_root,
        task_status="in_progress",
        repo_url="https://github.com/example/oriexa-task-101",
        state={"status": "coding", "repo_url": "https://github.com/example/oriexa-task-101"},
    )

    cleaned = aw.cleanup_workspace(101, reason="completed", workspace_dir=task_dir)

    meta = aw.read_workspace_metadata(101, workspace_root=workspace_root)
    assert cleaned is True
    assert not task_dir.exists()
    assert meta["task_id"] == 101
    assert meta["workspace_present"] is False
    assert meta["last_cleanup_reason"] == "completed"


def test_ensure_local_workspace_rehydrates_and_resets_stale_delivery_state(
    tmp_path: Path,
    monkeypatch,
):
    workspace_root = tmp_path / "agent_works"
    repo_url = "https://github.com/example/oriexa-task-202"
    aw.update_workspace_metadata(
        202,
        workspace_root=workspace_root,
        task_status="in_progress",
        repo_url=repo_url,
        state={"status": "delivered", "repo_url": repo_url},
    )

    def fake_clone(task_id: int, clone_repo_url: str, *, workspace_dir: Path | None = None) -> bool:
        assert task_id == 202
        assert clone_repo_url == repo_url
        assert workspace_dir is not None
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "README.md").write_text("rehydrated", encoding="utf-8")
        return True

    monkeypatch.setattr(aw, "clone_workspace_from_repo", fake_clone)

    task_dir, meta, rehydrated = aw.ensure_local_workspace(
        202,
        task_status="in_progress",
        workspace_root=workspace_root,
    )

    state = aw.load_swarm_state(202, workspace_dir=task_dir)
    assert rehydrated is True
    assert task_dir.exists()
    assert (task_dir / "README.md").exists()
    assert meta["repo_url"] == repo_url
    assert state["status"] == "coding"
    assert state["repo_url"] == repo_url


def test_sweep_workspaces_cleans_terminal_and_idle_workspaces(tmp_path: Path):
    workspace_root = tmp_path / "agent_works"

    completed_dir = workspace_root / "task_301"
    completed_dir.mkdir(parents=True)
    (completed_dir / "done.txt").write_text("done", encoding="utf-8")
    aw.sync_task_status(301, "completed", workspace_root=workspace_root)

    idle_dir = workspace_root / "task_302"
    idle_dir.mkdir(parents=True)
    (idle_dir / "app.txt").write_text("app", encoding="utf-8")
    aw.update_workspace_metadata(
        302,
        workspace_root=workspace_root,
        task_status="in_progress",
        repo_url="https://github.com/example/oriexa-task-302",
        state={"status": "coding", "repo_url": "https://github.com/example/oriexa-task-302"},
    )

    old_ts = datetime.now(timezone.utc) - timedelta(hours=30)
    old_epoch = old_ts.timestamp()
    os.utime(idle_dir, (old_epoch, old_epoch))
    _write_metadata_timestamp(aw.metadata_path(302, workspace_root), old_ts)

    actions = aw.sweep_workspaces(
        task_statuses={301: "completed", 302: "in_progress"},
        idle_ttl_seconds=60,
        workspace_root=workspace_root,
    )

    action_map = {(action["task_id"], action["reason"]) for action in actions}
    assert (301, "completed") in action_map
    assert (302, "idle>60s") in action_map
    assert not completed_dir.exists()
    assert not idle_dir.exists()


def test_create_github_repo_skips_push_when_remote_exists_without_local_changes(
    tmp_path: Path,
    monkeypatch,
):
    task_dir = tmp_path / "task_401"
    task_dir.mkdir()
    commands: list[list[str]] = []
    updates: list[tuple[tuple, dict]] = []

    def fake_run(cmd: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str]:
        commands.append(cmd)
        if cmd == ["git", "remote"]:
            return 0, ""
        if cmd[:3] == ["git", "remote", "add"]:
            return 0, ""
        raise AssertionError(f"Unexpected git command: {cmd}")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(git_ops, "_run", fake_run)
    monkeypatch.setattr(git_ops, "remote_repo_exists", lambda repo_url: True)
    monkeypatch.setattr(git_ops, "has_meaningful_implementation", lambda _: False)
    monkeypatch.setattr(
        git_ops,
        "update_workspace_metadata",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    repo_url = git_ops.create_github_repo(401, task_dir)

    assert repo_url == git_ops.expected_repo_url(401)
    assert any(cmd[:3] == ["git", "remote", "add"] for cmd in commands)
    assert not any("push" in cmd for cmd in commands)
    assert updates
