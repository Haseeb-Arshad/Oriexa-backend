"""Tests for the preview API and dashboard endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_mock_execution(
    id: int = 999,
    oriexa_task_id: int = 42,
    status: str = "completed",
    workspace_path: str = "/tmp/oriexa-workspaces/task-999",
    total_tokens_used: int = 12345,
    total_cost_usd: float = 0.05,
    attempt_count: int = 1,
    claimed_credits: int = 500,
    error_message: str | None = None,
):
    """Create a mock OrchTaskExecution object."""
    mock = MagicMock()
    mock.id = id
    mock.oriexa_task_id = oriexa_task_id
    mock.status = status
    mock.workspace_path = workspace_path
    mock.total_tokens_used = total_tokens_used
    mock.total_cost_usd = total_cost_usd
    mock.attempt_count = attempt_count
    mock.claimed_credits = claimed_credits
    mock.error_message = error_message
    mock.task_snapshot = {
        "title": "Build a REST API",
        "description": "Create a complete REST API with authentication",
        "budget_credits": 500,
    }
    from datetime import datetime, timezone
    mock.created_at = datetime(2026, 2, 23, 12, 0, 0, tzinfo=timezone.utc)
    mock.completed_at = datetime(2026, 2, 23, 12, 30, 0, tzinfo=timezone.utc)
    mock.started_at = datetime(2026, 2, 23, 12, 0, 0, tzinfo=timezone.utc)
    return mock


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace with test files."""
    ws = tmp_path / "task-test"
    ws.mkdir()

    (ws / "main.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    (ws / "README.md").write_text("# Hello World\n\nThis is a **test**.\n", encoding="utf-8")
    (ws / "data.json").write_text('{"key": "value"}', encoding="utf-8")
    (ws / "report.html").write_text("<h1>Report</h1><p>Done.</p>", encoding="utf-8")
    (ws / "data.csv").write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")

    sub = ws / "src"
    sub.mkdir()
    (sub / "app.py").write_text("from main import hello\nprint(hello())\n", encoding="utf-8")

    return ws


class TestPreviewFileScanning:
    """Test the file tree scanning logic."""

    def test_scan_directory(self, workspace):
        from app.api.preview import _scan_directory

        tree = _scan_directory(workspace, workspace)
        names = {entry["name"] for entry in tree}
        assert "main.py" in names
        assert "README.md" in names
        assert "src" in names

        # Check file has category
        py_file = next(e for e in tree if e["name"] == "main.py")
        assert py_file["category"] == "code"
        assert py_file["language"] == "python"
        assert py_file["size"] > 0

        # Check directory has children
        src_dir = next(e for e in tree if e["name"] == "src")
        assert src_dir["type"] == "directory"
        assert len(src_dir["children"]) == 1
        assert src_dir["children"][0]["name"] == "app.py"

    def test_scan_empty_directory(self, tmp_path):
        from app.api.preview import _scan_directory

        empty = tmp_path / "empty"
        empty.mkdir()
        tree = _scan_directory(empty, empty)
        assert tree == []

    def test_scan_ignores_hidden_and_special(self, workspace):
        from app.api.preview import _scan_directory

        # Create dirs that should be ignored
        (workspace / ".git").mkdir()
        (workspace / ".git" / "config").write_text("x")
        (workspace / "node_modules").mkdir()
        (workspace / "node_modules" / "pkg").write_text("x")
        (workspace / "__pycache__").mkdir()
        (workspace / "__pycache__" / "x.pyc").write_text("x")

        tree = _scan_directory(workspace, workspace)
        names = {e["name"] for e in tree}
        assert ".git" not in names
        assert "node_modules" not in names
        assert "__pycache__" not in names


class TestPreviewCategories:
    """Test file category detection."""

    def test_code_categories(self):
        from app.api.preview import PREVIEW_CATEGORIES

        assert PREVIEW_CATEGORIES[".py"] == "code"
        assert PREVIEW_CATEGORIES[".js"] == "code"
        assert PREVIEW_CATEGORIES[".ts"] == "code"

    def test_document_categories(self):
        from app.api.preview import PREVIEW_CATEGORIES

        assert PREVIEW_CATEGORIES[".md"] == "markdown"
        assert PREVIEW_CATEGORIES[".html"] == "html"
        assert PREVIEW_CATEGORIES[".json"] == "json"
        assert PREVIEW_CATEGORIES[".csv"] == "csv"

    def test_media_categories(self):
        from app.api.preview import PREVIEW_CATEGORIES

        assert PREVIEW_CATEGORIES[".png"] == "image"
        assert PREVIEW_CATEGORIES[".pdf"] == "pdf"
        assert PREVIEW_CATEGORIES[".xlsx"] == "spreadsheet"


class TestSafePathResolution:
    """Test path traversal prevention."""

    def test_normal_path(self, workspace):
        from app.api.preview import _safe_resolve

        result = _safe_resolve(workspace, "main.py")
        assert result == workspace / "main.py"

    def test_subdirectory_path(self, workspace):
        from app.api.preview import _safe_resolve

        result = _safe_resolve(workspace, "src/app.py")
        assert result == workspace / "src" / "app.py"

    def test_traversal_blocked(self, workspace):
        from app.api.preview import _safe_resolve

        with pytest.raises(ValueError, match="traversal"):
            _safe_resolve(workspace, "../../etc/passwd")

    def test_dot_dot_blocked(self, workspace):
        from app.api.preview import _safe_resolve

        with pytest.raises(ValueError, match="traversal"):
            _safe_resolve(workspace, "../../../secret")


class TestCSVRenderer:
    """Test CSV file rendering."""

    def test_render_csv(self, workspace):
        from app.api.preview import _render_csv

        csv_file = workspace / "data.csv"
        response = _render_csv(csv_file)
        data = json.loads(response.body)
        assert data["ok"]
        assert data["data"]["headers"] == ["name", "age"]
        assert len(data["data"]["rows"]) == 2
        assert data["data"]["rows"][0] == ["Alice", "30"]


class TestDashboard:
    """Test the dashboard HTML endpoint."""

    def test_dashboard_returns_html(self):
        from app.api.dashboard import DASHBOARD_HTML

        assert "Oriexa Agent Dashboard" in DASHBOARD_HTML
        assert "highlight.js" in DASHBOARD_HTML
        assert "marked.parse" in DASHBOARD_HTML
        assert "/orchestrator/preview" in DASHBOARD_HTML

    def test_dashboard_has_file_preview_support(self):
        from app.api.dashboard import DASHBOARD_HTML

        # Check it handles all file categories
        assert "previewFile" in DASHBOARD_HTML
        assert "renderSpreadsheet" in DASHBOARD_HTML
        assert "renderNotebook" in DASHBOARD_HTML
        assert "md-preview" in DASHBOARD_HTML
        assert "html-preview-frame" in DASHBOARD_HTML
        assert "pdf-preview-frame" in DASHBOARD_HTML
        assert "image-preview" in DASHBOARD_HTML
        assert "code-preview" in DASHBOARD_HTML
