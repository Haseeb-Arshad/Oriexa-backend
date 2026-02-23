"""Git operations helper for orchestrator — handles commits after each phase."""

import subprocess
from pathlib import Path
from typing import Optional


class GitHelper:
    """Helper for git operations during task execution."""

    def __init__(self, workspace_path: str):
        """Initialize with workspace path."""
        self.workspace = Path(workspace_path)

    async def add_all(self) -> bool:
        """Stage all changes. Returns True if successful."""
        try:
            result = subprocess.run(
                ["git", "add", "."],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Git add failed: {e}")
            return False

    async def commit(self, message: str) -> bool:
        """Commit staged changes. Returns True if successful."""
        try:
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            # Return code 1 can mean "nothing to commit" which is OK
            return result.returncode in (0, 1)
        except Exception as e:
            print(f"Git commit failed: {e}")
            return False

    async def push(self, remote: str = "origin", branch: str = "main") -> bool:
        """Push to remote. Returns True if successful."""
        try:
            result = subprocess.run(
                ["git", "push", remote, branch],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Git push failed: {e}")
            return False

    async def add_commit_push(self, message: str, remote: str = "origin", branch: str = "main") -> bool:
        """Full workflow: add, commit, push."""
        add_ok = await self.add_all()
        if not add_ok:
            print("Failed to add changes")
            return False

        commit_ok = await self.commit(message)
        if not commit_ok:
            print("Failed to commit changes")
            return False

        push_ok = await self.push(remote, branch)
        if not push_ok:
            print("Failed to push changes")
            return False

        return True

    async def get_status(self) -> Optional[str]:
        """Get current git status."""
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception as e:
            print(f"Git status failed: {e}")
            return None

    async def get_commit_message(self) -> Optional[str]:
        """Get last commit message."""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--pretty=%B"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except Exception as e:
            print(f"Failed to get commit message: {e}")
            return None
