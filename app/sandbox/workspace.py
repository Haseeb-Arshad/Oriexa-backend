"""Per-task workspace creation and cleanup."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Creates isolated directories per task under WORKSPACE_ROOT."""

    def __init__(self, root: str | None = None):
        self.root = Path(root or settings.WORKSPACE_ROOT)

    def create(self, execution_id: int) -> Path:
        workspace = self.root / f"task-{execution_id}"
        workspace.mkdir(parents=True, exist_ok=True)
        logger.info("Created workspace: %s", workspace)
        return workspace

    def cleanup(self, execution_id: int) -> None:
        workspace = self.root / f"task-{execution_id}"
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
            logger.info("Cleaned up workspace: %s", workspace)

    def get_path(self, execution_id: int) -> Path:
        return self.root / f"task-{execution_id}"

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
