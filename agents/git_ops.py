"""
Oriexa Git Operations Module

Centralized git operations used by all agents in the swarm.
Handles repo init, GitHub creation, incremental commits, and push.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

from app.services.agent_workspaces import (
    ensure_authenticated_remote,
    expected_repo_url,
    remote_repo_exists,
    update_workspace_metadata,
)

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "Haseeb-Arshad")
COMMIT_PUSH_INTERVAL = int(os.environ.get("COMMIT_PUSH_INTERVAL", "3"))  # push every N commits
HOUSEKEEPING_FILES = {
    ".gitignore",
    ".build_log",
    ".dispatch_log",
    ".swarm_state.json",
    ".agent_lock",
    ".test_results.json",
    ".deploy_results.json",
    "progress.jsonl",
    ".implementation_plan.json",
}
HOUSEKEEPING_PREFIXES = (".llm_debug_",)


# ═══════════════════════════════════════════════════════════════════════════
# SHELL HELPER
# ═══════════════════════════════════════════════════════════════════════════

def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> tuple[int, str]:
    """Run a git command and return (return_code, combined_output)."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace"
        )
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        return -1, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return -1, str(e)


def _normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").strip().strip('"')


def _is_housekeeping_path(path: str) -> bool:
    normalized = _normalize_repo_path(path)
    if not normalized or normalized.startswith(".git/"):
        return True
    if normalized in HOUSEKEEPING_FILES:
        return True
    name = normalized.rsplit("/", 1)[-1]
    return any(name.startswith(prefix) for prefix in HOUSEKEEPING_PREFIXES)


def _extract_meaningful_paths(status_output: str) -> list[str]:
    paths: list[str] = []
    for line in status_output.splitlines():
        entry = line[3:].strip() if len(line) >= 4 else line.strip()
        if not entry:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1].strip()
        normalized = _normalize_repo_path(entry)
        if normalized and not _is_housekeeping_path(normalized):
            paths.append(normalized)
    return paths


# ═══════════════════════════════════════════════════════════════════════════
# GITIGNORE TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_GITIGNORE = """\
# Dependencies
node_modules/
venv/
__pycache__/
*.pyc

# Build
.next/
dist/
build/
*.egg-info/

# Environment
.env
.env.local
.env.*.local

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Oriexa Agent State
.swarm_state.json
.agent_lock
.dispatch_log
.build_log
.test_results.json
"""


# ═══════════════════════════════════════════════════════════════════════════
# CORE GIT OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def init_repo(task_dir: Path) -> bool:
    """
    Initialize a git repo in the task directory.
    Creates .gitignore and makes an initial empty commit.
    Returns True on success.
    """
    task_dir.mkdir(parents=True, exist_ok=True)

    # Skip if already initialized
    if (task_dir / ".git").exists():
        return True

    # Write .gitignore
    gitignore_path = task_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(DEFAULT_GITIGNORE, encoding="utf-8")

    # git init
    rc, out = _run(["git", "init"], task_dir)
    if rc != 0:
        return False

    # Configure git user for this repo (so commits work in CI/automation)
    _run(["git", "config", "user.email", "agent@oriexa.dev"], task_dir)
    _run(["git", "config", "user.name", "Oriexa Agent"], task_dir)

    # Initial commit
    _run(["git", "add", ".gitignore"], task_dir)
    rc, out = _run(["git", "commit", "-m", "chore: initialize repository"], task_dir)
    _run(["git", "branch", "-M", "main"], task_dir)

    return rc == 0


def create_github_repo(task_id: int, task_dir: Path, task_title: str | None = None) -> str | None:
    """
    Create a GitHub repo for the task using the GitHub REST API.
    Returns the repo URL on success, None on failure.
    Handles 'name already exists' by linking to the existing repo.
    """
    gh_token = os.environ.get("GITHUB_TOKEN", os.environ.get("GH_TOKEN", ""))
    repo_url = expected_repo_url(task_id, task_title)
    repo_name = repo_url.rstrip("/").split("/")[-1]
    # Authenticated URL for seamless push (no credential prompts)
    auth_url = f"https://x-access-token:{gh_token}@github.com/{GITHUB_USERNAME}/{repo_name}.git"

    # Check if remote already exists
    rc, out = _run(["git", "remote"], task_dir)
    if "origin" in out:
        ensure_authenticated_remote(task_dir, repo_url)
        if remote_repo_exists(repo_url) and not has_meaningful_implementation(task_dir):
            update_workspace_metadata(task_id, workspace_dir=task_dir, repo_url=repo_url)
            return repo_url

        rc, _ = _run(["git", "push", "-u", "origin", "main"], task_dir)
        if rc == 0:
            update_workspace_metadata(task_id, workspace_dir=task_dir, repo_url=repo_url)
            return repo_url
        return None

    if not gh_token:
        # No token — can't create repo via API, try linking directly
        _run(["git", "remote", "add", "origin", f"{repo_url}.git"], task_dir)
        if remote_repo_exists(repo_url) and not has_meaningful_implementation(task_dir):
            update_workspace_metadata(task_id, workspace_dir=task_dir, repo_url=repo_url)
            return repo_url

        rc, _ = _run(["git", "push", "-u", "origin", "main"], task_dir)
        if rc == 0:
            update_workspace_metadata(task_id, workspace_dir=task_dir, repo_url=repo_url)
            return repo_url
        return None

    repo_exists = remote_repo_exists(repo_url)

    # Create repo via GitHub REST API
    if not repo_exists:
        try:
            resp = httpx.post(
                "https://api.github.com/user/repos",
                headers={
                    "Authorization": f"Bearer {gh_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "name": repo_name,
                    "description": f"Oriexa delivery for task #{task_id}",
                    "private": False,
                    "auto_init": False,
                },
                timeout=30.0,
            )

            if resp.status_code == 201:
                repo_exists = True
            elif resp.status_code == 422 and "name already exists" in resp.text.lower():
                repo_exists = True
            else:
                return None
        except Exception:
            return None

    # Add authenticated remote and push
    _run(["git", "remote", "add", "origin", auth_url], task_dir)
    if repo_exists and remote_repo_exists(repo_url) and not has_meaningful_implementation(task_dir):
        update_workspace_metadata(task_id, workspace_dir=task_dir, repo_url=repo_url)
        return repo_url

    rc, _ = _run(["git", "push", "-u", "origin", "main"], task_dir, timeout=30)
    if rc == 0:
        update_workspace_metadata(task_id, workspace_dir=task_dir, repo_url=repo_url)
        return repo_url
    return None


def commit_step(
    task_dir: Path,
    message: str,
    files: list[str] | None = None,
    push: bool = False,
) -> str | None:
    """
    Stage and commit changes with a descriptive message.
    
    Args:
        task_dir: Path to the task workspace
        message: Commit message (e.g. "feat: add API routes")
        files: Specific files to stage, or None for all changes
        push: Whether to push after committing
    
    Returns:
        The short commit hash on success, None on failure.
    """
    # Stage files
    if files:
        for f in files:
            _run(["git", "add", f], task_dir)
    else:
        _run(["git", "add", "-A"], task_dir)

    # Check if there's anything to commit
    rc, status = _run(["git", "status", "--porcelain"], task_dir)
    if not status.strip():
        return None  # Nothing to commit
    if not _extract_meaningful_paths(status):
        return None

    # Commit
    rc, out = _run(["git", "commit", "-m", message], task_dir)
    if rc != 0:
        return None

    # Get commit hash
    rc, hash_out = _run(["git", "rev-parse", "--short", "HEAD"], task_dir)
    commit_hash = hash_out.strip() if rc == 0 else "unknown"

    # Push if requested
    if push:
        push_to_remote(task_dir)

    return commit_hash


def push_to_remote(task_dir: Path, force: bool = False) -> bool:
    """Push to origin/main. Returns True on success."""
    ensure_authenticated_remote(task_dir)
    cmd = ["git", "push", "-u", "origin", "main"]
    if force:
        cmd.append("--force")
    rc, out = _run(cmd, task_dir, timeout=30)
    return rc == 0


def has_meaningful_implementation(task_dir: Path) -> bool:
    """Return True when repo contains non-housekeeping tracked files."""
    rc, out = _run(["git", "ls-files"], task_dir, timeout=30)
    if rc != 0:
        return False

    tracked = [line.strip() for line in out.splitlines() if line.strip()]
    meaningful = []
    for rel in tracked:
        p = _normalize_repo_path(rel)
        if not p or _is_housekeeping_path(p):
            continue
        meaningful.append(p)

    return len(meaningful) > 0


def verify_remote_has_main(task_dir: Path) -> bool:
    """Return True when remote origin has a main branch reference."""
    rc, out = _run(["git", "ls-remote", "--heads", "origin", "main"], task_dir, timeout=30)
    return rc == 0 and bool(out.strip())


def verify_remote_head_matches_local(task_dir: Path) -> bool:
    """Return True when remote origin/main points at the current local HEAD."""
    rc, local_head = _run(["git", "rev-parse", "HEAD"], task_dir, timeout=30)
    if rc != 0:
        return False

    rc, remote_head = _run(["git", "ls-remote", "--heads", "origin", "main"], task_dir, timeout=30)
    if rc != 0 or not remote_head.strip():
        return False

    remote_sha = remote_head.split()[0].strip()
    return bool(remote_sha) and remote_sha == local_head.strip()


def get_repo_url(task_id: int, task_title: str | None = None) -> str:
    """Return the expected GitHub URL for a task."""
    return expected_repo_url(task_id, task_title)


def get_commit_count(task_dir: Path) -> int:
    """Return the number of commits in the repo."""
    rc, out = _run(["git", "rev-list", "--count", "HEAD"], task_dir)
    try:
        return int(out.strip()) if rc == 0 else 0
    except ValueError:
        return 0


def should_push(task_dir: Path) -> bool:
    """Check if we've accumulated enough commits to warrant a push."""
    # Count commits not yet pushed
    rc, out = _run(["git", "rev-list", "--count", "HEAD", "--not", "--remotes"], task_dir)
    try:
        unpushed = int(out.strip()) if rc == 0 else 0
        return unpushed >= COMMIT_PUSH_INTERVAL
    except ValueError:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# COMMIT LOG TRACKING
# ═══════════════════════════════════════════════════════════════════════════

def append_commit_log(task_dir: Path, commit_hash: str, message: str):
    """Append a commit entry to the state file's commit_log."""
    state_file = task_dir / ".swarm_state.json"
    if not state_file.exists():
        return

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return

    if "commit_log" not in state:
        state["commit_log"] = []

    state["commit_log"].append({
        "hash": commit_hash,
        "message": message,
        "timestamp": time.time(),
    })

    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
