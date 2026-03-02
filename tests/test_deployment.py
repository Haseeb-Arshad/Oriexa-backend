"""Tests for deployment tools and graph routing with deployment node."""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.deployment import (
    _detect_framework,
    _is_deployable,
    run_full_test_suite,
)
from app.orchestrator.supervisor import (
    build_supervisor_graph,
    route_after_review,
)


class TestDetectFramework:
    """Test Python-native framework detection from package.json."""

    def test_nextjs(self, tmp_path: Path):
        pkg = {"dependencies": {"next": "14.0.0", "react": "18.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "nextjs"

    def test_vite(self, tmp_path: Path):
        pkg = {"devDependencies": {"vite": "5.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "vite"

    def test_create_react_app(self, tmp_path: Path):
        pkg = {"dependencies": {"react-scripts": "5.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "create-react-app"

    def test_vue(self, tmp_path: Path):
        pkg = {"dependencies": {"vue": "3.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "vue"

    def test_nuxt(self, tmp_path: Path):
        pkg = {"dependencies": {"nuxt": "3.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "nuxtjs"

    def test_sveltekit(self, tmp_path: Path):
        pkg = {"devDependencies": {"@sveltejs/kit": "2.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "sveltekit"

    def test_astro(self, tmp_path: Path):
        pkg = {"dependencies": {"astro": "4.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "astro"

    def test_gatsby(self, tmp_path: Path):
        pkg = {"dependencies": {"gatsby": "5.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "gatsby"

    def test_static_html(self, tmp_path: Path):
        (tmp_path / "index.html").write_text("<html></html>")
        assert _detect_framework(str(tmp_path)) == "static"

    def test_static_with_build_script(self, tmp_path: Path):
        pkg = {"scripts": {"build": "webpack"}, "dependencies": {}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert _detect_framework(str(tmp_path)) == "static"

    def test_no_framework(self, tmp_path: Path):
        # Empty directory — no package.json, no index.html
        assert _detect_framework(str(tmp_path)) is None

    def test_python_only(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("flask==3.0")
        assert _detect_framework(str(tmp_path)) is None

    def test_is_deployable_true(self, tmp_path: Path):
        (tmp_path / "index.html").write_text("<html></html>")
        assert _is_deployable(str(tmp_path)) is True

    def test_is_deployable_false(self, tmp_path: Path):
        assert _is_deployable(str(tmp_path)) is False


class TestRunFullTestSuite:
    """Test the test suite runner with mocked executor."""

    @pytest.mark.asyncio
    async def test_node_project_all_pass(self, tmp_path: Path):
        pkg = {
            "scripts": {"lint": "eslint .", "test": "jest", "build": "tsc"},
            "dependencies": {"react": "18.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "tsconfig.json").write_text("{}")

        mock_result = MagicMock(exit_code=0, stdout="OK", stderr="")

        with patch("app.tools.deployment.SandboxExecutor") as MockExecutor:
            instance = MockExecutor.return_value
            instance.execute = AsyncMock(return_value=mock_result)

            results = await run_full_test_suite(str(tmp_path))

        assert results["lint_passed"] is True
        assert results["typecheck_passed"] is True
        assert results["tests_passed"] is True
        assert results["build_passed"] is True
        assert "4/4" in results["summary"]

    @pytest.mark.asyncio
    async def test_python_project(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("flask")

        mock_result = MagicMock(exit_code=0, stdout="OK", stderr="")

        with patch("app.tools.deployment.SandboxExecutor") as MockExecutor:
            instance = MockExecutor.return_value
            instance.execute = AsyncMock(return_value=mock_result)

            results = await run_full_test_suite(str(tmp_path))

        assert results["lint_passed"] is True
        assert results["tests_passed"] is True
        assert "2/2" in results["summary"]

    @pytest.mark.asyncio
    async def test_no_project_type(self, tmp_path: Path):
        results = await run_full_test_suite(str(tmp_path))
        assert "No recognized project type" in results["summary"]

    @pytest.mark.asyncio
    async def test_node_project_lint_fails(self, tmp_path: Path):
        pkg = {"scripts": {"lint": "eslint ."}, "dependencies": {}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        install_ok = MagicMock(exit_code=0, stdout="", stderr="")
        lint_fail = MagicMock(exit_code=1, stdout="", stderr="error: no-unused-vars")

        call_count = 0

        async def mock_execute(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if "npm install" in cmd:
                return install_ok
            return lint_fail

        with patch("app.tools.deployment.SandboxExecutor") as MockExecutor:
            instance = MockExecutor.return_value
            instance.execute = AsyncMock(side_effect=mock_execute)

            results = await run_full_test_suite(str(tmp_path))

        assert results["lint_passed"] is False
        assert "0/1" in results["summary"]


class TestGraphRoutingWithDeployment:
    """Verify route_after_review now routes to deployment."""

    def test_review_passed_routes_to_deployment(self):
        state = {"review_passed": True}
        assert route_after_review(state) == "deployment"

    def test_review_failed_still_retries(self):
        state = {"review_passed": False, "attempt_count": 1, "max_attempts": 3}
        assert route_after_review(state) == "planning"

    def test_review_failed_max_attempts(self):
        state = {"review_passed": False, "attempt_count": 3, "max_attempts": 3}
        assert route_after_review(state) == "failed"


class TestGraphStructureWithDeployment:
    """Verify the graph includes the deployment node."""

    def test_graph_has_deployment_node(self):
        graph = build_supervisor_graph()
        node_names = set(graph.nodes.keys())
        assert "deployment" in node_names

    def test_graph_has_all_nodes(self):
        graph = build_supervisor_graph()
        node_names = set(graph.nodes.keys())
        expected = {
            "triage", "clarification", "wait_for_response",
            "planning", "execution", "complex_execution",
            "review", "deployment", "delivery", "failed",
        }
        assert expected.issubset(node_names)

    def test_graph_compiles(self):
        graph = build_supervisor_graph()
        compiled = graph.compile()
        assert compiled is not None


class TestTaskStateFields:
    """Verify new state fields are accepted by TaskState."""

    def test_new_fields_accepted(self):
        from app.orchestrator.state import TaskState

        # TypedDict with total=False allows all fields to be optional
        state: TaskState = {
            "execution_id": 1,
            "task_type": "frontend",
            "github_repo_url": "https://github.com/org/repo",
            "vercel_preview_url": "https://preview.vercel.app",
            "vercel_claim_url": "https://vercel.com/claim/xxx",
            "test_results": {"lint_passed": True, "summary": "1/1 passed"},
        }
        assert state["task_type"] == "frontend"
        assert state["github_repo_url"] == "https://github.com/org/repo"
        assert state["vercel_preview_url"] == "https://preview.vercel.app"
        assert state["test_results"]["lint_passed"] is True
