"""Lifecycle helpers for legacy agent workspaces under AGENT_WORKSPACE_DIR."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_IDLE_TTL_SECONDS = int(
    os.environ.get("TASK_WORKSPACE_IDLE_TTL_SECONDS", str(24 * 60 * 60))
)

_METADATA_DIRNAME = ".task_meta"
_STATE_FILE_NAME = ".swarm_state.json"
_ACTIVITY_FILE_NAMES = (
    ".swarm_state.json",
    ".build_log",
    ".dispatch_log",
    ".test_results.json",
    ".deploy_results.json",
    ".implementation_plan.json",
    "progress.jsonl",
)
_TERMINAL_TASK_STATUSES = {"completed", "cancelled"}
_STATE_SNAPSHOT_KEYS = {
    "status",
    "current_step",
    "total_steps",
    "completed_steps",
    "commit_log",
    "iterations",
    "test_command",
    "test_errors",
    "test_iteration",
    "repo_url",
    "vercel_url",
    "plan",
    "cached_blueprint",
    "scaffolded",
    "smoke_test",
    "deployment_mode",
    "error",
    "failed_at",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _workspace_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    env_root = os.environ.get("AGENT_WORKSPACE_DIR")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[2] / "agent_works"


def workspace_path(task_id: int, root: str | Path | None = None) -> Path:
    return _workspace_root(root) / f"task_{task_id}"


def metadata_dir(root: str | Path | None = None) -> Path:
    return _workspace_root(root) / _METADATA_DIRNAME


def metadata_path(task_id: int, root: str | Path | None = None) -> Path:
    return metadata_dir(root) / f"task_{task_id}.json"


def repo_name_for_task(task_id: int, task_title: str | None = None) -> str:
    if not task_title:
        return f"oriexa-task-{task_id}"

    slug = re.sub(r"[^a-z0-9]+", "-", task_title.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return f"oriexa-task-{task_id}"

    suffix = f"-task-{task_id}"
    max_slug_length = max(12, 100 - len(suffix))
    return f"{slug[:max_slug_length].rstrip('-')}{suffix}"


def expected_repo_url(task_id: int, task_title: str | None = None) -> str:
    github_username = os.environ.get("GITHUB_USERNAME", "Haseeb-Arshad")
    return f"https://github.com/{github_username}/{repo_name_for_task(task_id, task_title)}"


def _parse_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _json_dump(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _state_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}

    snapshot = {key: state[key] for key in _STATE_SNAPSHOT_KEYS if key in state}
    commit_log = snapshot.get("commit_log")
    if isinstance(commit_log, list):
        snapshot["commit_log"] = commit_log[-100:]
    return snapshot


def read_workspace_metadata(
    task_id: int,
    *,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    path = metadata_path(task_id, workspace_root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def update_workspace_metadata(
    task_id: int,
    *,
    workspace_dir: Path | None = None,
    workspace_root: str | Path | None = None,
    state: dict[str, Any] | None = None,
    task_status: str | None = None,
    repo_url: str | None = None,
    vercel_url: str | None = None,
    cleanup_reason: str | None = None,
    last_activity: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = workspace_root if workspace_root is not None else workspace_dir.parent if workspace_dir else None
    task_dir = workspace_dir or workspace_path(task_id, root)
    meta = read_workspace_metadata(task_id, workspace_root=root)
    now_iso = _utcnow_iso()

    meta["task_id"] = task_id
    meta["workspace_path"] = str(task_dir)
    meta["workspace_present"] = task_dir.exists()
    meta["updated_at"] = now_iso

    if task_status is not None:
        meta["task_status"] = task_status
    if repo_url is not None:
        meta["repo_url"] = repo_url
    if vercel_url is not None:
        meta["vercel_url"] = vercel_url

    if state is not None:
        snapshot = _state_snapshot(state)
        meta["state"] = snapshot
        state_status = snapshot.get("status")
        if state_status:
            meta["state_status"] = state_status
        repo_from_state = snapshot.get("repo_url")
        if repo_from_state:
            meta["repo_url"] = repo_from_state
        vercel_from_state = snapshot.get("vercel_url")
        if vercel_from_state:
            meta["vercel_url"] = vercel_from_state
        last_activity = True

    if cleanup_reason:
        meta["last_cleanup_at"] = now_iso
        meta["last_cleanup_reason"] = cleanup_reason

    if extra:
        meta.update(extra)

    if last_activity:
        meta["last_activity_at"] = now_iso

    _json_dump(metadata_path(task_id, root), meta)
    return meta


def sync_task_status(
    task_id: int,
    task_status: str,
    *,
    workspace_dir: Path | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    return update_workspace_metadata(
        task_id,
        workspace_dir=workspace_dir,
        workspace_root=workspace_root,
        task_status=task_status,
        last_activity=task_status not in _TERMINAL_TASK_STATUSES,
    )


def write_swarm_state(
    task_id: int,
    state: dict[str, Any],
    *,
    workspace_dir: Path | None = None,
    task_status: str | None = None,
) -> Path:
    task_dir = workspace_dir or workspace_path(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / _STATE_FILE_NAME
    _json_dump(path, state)
    update_workspace_metadata(
        task_id,
        workspace_dir=task_dir,
        state=state,
        task_status=task_status,
    )
    return path


def load_swarm_state(
    task_id: int,
    *,
    workspace_dir: Path | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_dir = workspace_dir or workspace_path(task_id)
    path = task_dir / _STATE_FILE_NAME
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

    merged = dict(default or {})
    meta = read_workspace_metadata(task_id, workspace_root=task_dir.parent)
    snapshot = meta.get("state")
    if isinstance(snapshot, dict):
        merged.update(snapshot)
    return merged


def _remote_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def authenticated_repo_url(repo_url: str) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token or "github.com/" not in repo_url:
        return repo_url

    repo_path = repo_url.split("github.com/", 1)[1].strip("/")
    if not repo_path.endswith(".git"):
        repo_path = f"{repo_path}.git"
    return f"https://x-access-token:{token}@github.com/{repo_path}"


def ensure_authenticated_remote(task_dir: Path, repo_url: str | None = None) -> None:
    if repo_url is None:
        proc = _remote_command(["git", "remote", "get-url", "origin"], cwd=task_dir, timeout=30)
        if proc.returncode != 0:
            return
        repo_url = (proc.stdout or proc.stderr).strip()
        if repo_url.startswith("https://x-access-token:"):
            return

    auth_url = authenticated_repo_url(repo_url)
    if auth_url == repo_url:
        return
    _remote_command(["git", "remote", "set-url", "origin", auth_url], cwd=task_dir, timeout=30)


def remote_repo_exists(repo_url: str) -> bool:
    try:
        proc = _remote_command(
            ["git", "ls-remote", "--heads", repo_url, "main"],
            timeout=30,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return False


def clone_workspace_from_repo(
    task_id: int,
    repo_url: str,
    *,
    workspace_dir: Path | None = None,
) -> bool:
    task_dir = workspace_dir or workspace_path(task_id)
    if task_dir.exists():
        return True

    task_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = _remote_command(
            ["git", "clone", authenticated_repo_url(repo_url), str(task_dir)],
            timeout=180,
        )
    except Exception:
        return False

    if proc.returncode != 0:
        return False

    ensure_authenticated_remote(task_dir, repo_url)
    update_workspace_metadata(
        task_id,
        workspace_dir=task_dir,
        repo_url=repo_url,
        last_activity=True,
        extra={"last_rehydrated_at": _utcnow_iso()},
    )
    return True


def _restore_state_after_clone(
    task_id: int,
    *,
    task_dir: Path,
    task_status: str | None,
    metadata: dict[str, Any],
) -> None:
    state = dict(metadata.get("state") or {})
    if not state:
        return

    if task_status in {"claimed", "in_progress"} and state.get("status") in {"deploying", "delivered"}:
        state["status"] = "coding"
    if task_status in {"claimed", "in_progress"} and not state.get("status"):
        state["status"] = "coding"
    if metadata.get("repo_url") and not state.get("repo_url"):
        state["repo_url"] = metadata["repo_url"]

    write_swarm_state(task_id, state, workspace_dir=task_dir, task_status=task_status)


def ensure_local_workspace(
    task_id: int,
    *,
    task_status: str | None = None,
    workspace_root: str | Path | None = None,
) -> tuple[Path, dict[str, Any], bool]:
    task_dir = workspace_path(task_id, workspace_root)
    meta = read_workspace_metadata(task_id, workspace_root=task_dir.parent)

    if task_status is not None:
        meta = update_workspace_metadata(
            task_id,
            workspace_dir=task_dir,
            task_status=task_status,
            last_activity=task_status not in _TERMINAL_TASK_STATUSES,
        )

    if task_dir.exists():
        return task_dir, meta, False

    repo_url = meta.get("repo_url")
    if not repo_url:
        candidate = expected_repo_url(task_id)
        if remote_repo_exists(candidate):
            repo_url = candidate
            meta = update_workspace_metadata(
                task_id,
                workspace_dir=task_dir,
                repo_url=repo_url,
            )

    if repo_url and clone_workspace_from_repo(task_id, repo_url, workspace_dir=task_dir):
        meta = read_workspace_metadata(task_id, workspace_root=task_dir.parent)
        _restore_state_after_clone(
            task_id,
            task_dir=task_dir,
            task_status=task_status,
            metadata=meta,
        )
        return task_dir, meta, True

    task_dir.mkdir(parents=True, exist_ok=True)
    meta = update_workspace_metadata(
        task_id,
        workspace_dir=task_dir,
        task_status=task_status,
    )
    return task_dir, meta, False


def workspace_last_activity(
    task_id: int,
    *,
    workspace_dir: Path | None = None,
) -> float:
    task_dir = workspace_dir or workspace_path(task_id)
    meta = read_workspace_metadata(task_id, workspace_root=task_dir.parent)
    timestamps: list[float] = []

    for key in ("last_activity_at", "updated_at"):
        parsed = _parse_iso(meta.get(key))
        if parsed is not None:
            timestamps.append(parsed)

    if task_dir.exists():
        try:
            timestamps.append(task_dir.stat().st_mtime)
        except OSError:
            pass

        for name in _ACTIVITY_FILE_NAMES:
            candidate = task_dir / name
            if not candidate.exists():
                continue
            try:
                timestamps.append(candidate.stat().st_mtime)
            except OSError:
                continue

    return max(timestamps) if timestamps else 0.0


def _has_deployment_artifact(task_dir: Path, meta: dict[str, Any]) -> bool:
    if meta.get("vercel_url"):
        return True

    state = meta.get("state") or {}
    if isinstance(state, dict) and state.get("vercel_url"):
        return True

    deploy_file = task_dir / ".deploy_results.json"
    return deploy_file.exists()


def cleanup_workspace(
    task_id: int,
    *,
    reason: str,
    workspace_dir: Path | None = None,
    preserve_metadata: bool = True,
) -> bool:
    task_dir = workspace_dir or workspace_path(task_id)
    cleaned = False

    lock_file = task_dir / ".agent_lock"
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass

    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
        cleaned = True

    if preserve_metadata:
        update_workspace_metadata(
            task_id,
            workspace_dir=task_dir,
            cleanup_reason=reason,
        )
    else:
        try:
            metadata_path(task_id, task_dir.parent).unlink(missing_ok=True)
        except Exception:
            pass

    return cleaned


def sweep_workspaces(
    *,
    task_statuses: dict[int, str] | None = None,
    idle_ttl_seconds: int | None = None,
    workspace_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    root = _workspace_root(workspace_root)
    idle_ttl = idle_ttl_seconds or DEFAULT_IDLE_TTL_SECONDS
    task_ids: set[int] = set()

    meta_root = metadata_dir(root)
    if meta_root.exists():
        for entry in meta_root.glob("task_*.json"):
            try:
                task_ids.add(int(entry.stem.split("_", 1)[1]))
            except Exception:
                continue

    if root.exists():
        for entry in root.iterdir():
            if not entry.is_dir() or not entry.name.startswith("task_"):
                continue
            try:
                task_ids.add(int(entry.name.split("_", 1)[1]))
            except Exception:
                continue

    if not task_ids:
        return []

    now_ts = _utcnow().timestamp()
    actions: list[dict[str, Any]] = []
    status_map = task_statuses or {}

    for task_id in sorted(task_ids):
        task_dir = workspace_path(task_id, root)
        meta = read_workspace_metadata(task_id, workspace_root=root)
        task_status = status_map.get(task_id) or meta.get("task_status")

        if task_status and meta.get("task_status") != task_status:
            meta = update_workspace_metadata(
                task_id,
                workspace_dir=task_dir,
                task_status=task_status,
            )

        if task_status in _TERMINAL_TASK_STATUSES:
            if cleanup_workspace(task_id, reason=task_status, workspace_dir=task_dir):
                actions.append({"task_id": task_id, "reason": task_status})
            continue

        if not task_dir.exists():
            continue

        repo_url = meta.get("repo_url") or ((meta.get("state") or {}).get("repo_url"))
        state_status = str(meta.get("state_status") or ((meta.get("state") or {}).get("status") or ""))
        deployment_ready = _has_deployment_artifact(task_dir, meta)

        idle_eligible = False
        if repo_url and task_status in {"claimed", "in_progress"}:
            idle_eligible = True
        elif repo_url and task_status == "delivered" and deployment_ready:
            idle_eligible = True
        elif repo_url and state_status in {"deploying", "delivered"} and deployment_ready:
            idle_eligible = True

        if not idle_eligible:
            continue

        last_activity = workspace_last_activity(task_id, workspace_dir=task_dir)
        if not last_activity or (now_ts - last_activity) < idle_ttl:
            continue

        reason = f"idle>{idle_ttl}s"
        if cleanup_workspace(task_id, reason=reason, workspace_dir=task_dir):
            actions.append({"task_id": task_id, "reason": reason})

    return actions
