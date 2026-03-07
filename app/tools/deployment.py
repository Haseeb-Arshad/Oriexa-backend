"""Deployment tools — GitHub repo creation, Vercel deployment, and test suite runner.

These tools are called programmatically by the deployment node (not by LLM agents).
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tarfile
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.sandbox.executor import SandboxExecutor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Framework detection (Python-native, no shell dependency)
# ---------------------------------------------------------------------------

def _detect_framework(workspace_path: str) -> str | None:
    """Detect the frontend framework from package.json or project files.

    Returns a framework identifier string or None if not deployable.
    """
    ws = Path(workspace_path)

    # Check package.json first
    pkg_json = ws / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = {
                **pkg.get("dependencies", {}),
                **pkg.get("devDependencies", {}),
            }

            if "next" in deps:
                return "nextjs"
            if "nuxt" in deps or "nuxt3" in deps:
                return "nuxtjs"
            if "@sveltejs/kit" in deps:
                return "sveltekit"
            if "astro" in deps:
                return "astro"
            if "gatsby" in deps:
                return "gatsby"
            if "vite" in deps:
                return "vite"
            if "react-scripts" in deps:
                return "create-react-app"
            if "vue" in deps:
                return "vue"
            if "react" in deps:
                return "react"

            # Generic Node.js project with a build script
            scripts = pkg.get("scripts", {})
            if "build" in scripts:
                return "static"

            return "static"
        except (json.JSONDecodeError, OSError):
            pass

    # Static HTML project
    if (ws / "index.html").exists():
        return "static"

    return None


def _is_deployable(workspace_path: str) -> bool:
    """Check if the workspace contains a deployable frontend project."""
    return _detect_framework(workspace_path) is not None


# ---------------------------------------------------------------------------
# Tool: create_github_repo
# ---------------------------------------------------------------------------

async def create_github_repo(
    repo_name: str,
    description: str,
    workspace_path: str,
    private: bool = False,
) -> dict[str, Any]:
    """Create a GitHub repository and push workspace contents to it.

    Uses the GitHub REST API directly (no gh CLI dependency).
    Falls back gracefully if token is not set.
    """
    gh_token = settings.GITHUB_TOKEN or os.environ.get("GH_TOKEN", "")
    if not gh_token:
        return {"success": False, "error": "GITHUB_TOKEN not configured"}

    executor = SandboxExecutor(timeout=60)
    ws = Path(workspace_path)

    # Ensure git is initialized
    if not (ws / ".git").exists():
        init_result = await executor.execute("git init", cwd=workspace_path)
        if init_result.exit_code != 0:
            return {"success": False, "error": f"git init failed: {init_result.stderr}"}

        # Configure git user
        await executor.execute(
            'git config user.email "agent@oriexa.dev"', cwd=workspace_path
        )
        await executor.execute(
            'git config user.name "Oriexa Agent"', cwd=workspace_path
        )

        # Initial commit if needed
        await executor.execute("git add .", cwd=workspace_path)
        await executor.execute(
            'git commit -m "Initial commit — Oriexa delivery"',
            cwd=workspace_path,
        )

    # Determine org/user prefix
    org_prefix = f"{settings.GITHUB_ORG}/" if settings.GITHUB_ORG else ""
    full_repo_name = f"{org_prefix}{repo_name}" if org_prefix else repo_name
    owner = settings.GITHUB_ORG if settings.GITHUB_ORG else None

    # Create repo via GitHub REST API
    try:
        if owner:
            # Create under an org
            api_url = f"https://api.github.com/orgs/{owner}/repos"
        else:
            api_url = "https://api.github.com/user/repos"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {gh_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "name": repo_name,
                    "description": description,
                    "private": private,
                    "auto_init": False,
                },
            )

            if resp.status_code == 201:
                repo_data = resp.json()
                repo_url = repo_data.get("html_url", f"https://github.com/{full_repo_name}")
            elif resp.status_code == 422 and "already exists" in resp.text.lower():
                repo_url = f"https://github.com/{full_repo_name}"
            elif owner and resp.status_code == 404:
                # Fallback to user repo if org fails (e.g. personal username was put in GITHUB_ORG)
                logger.warning("Org repo creation failed with 404, falling back to /user/repos")
                resp = await client.post(
                    "https://api.github.com/user/repos",
                    headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                    json={"name": repo_name, "description": description, "private": private, "auto_init": False},
                )
                if resp.status_code == 201:
                    full_repo_name = resp.json().get("full_name", repo_name)
                    repo_url = resp.json().get("html_url", f"https://github.com/{full_repo_name}")
                elif resp.status_code == 422 and "already exists" in resp.text.lower():
                    # If falling back, we might need to query the actual full name, but let's assume it's under the user
                    repo_url = f"https://github.com/{repo_name}" # simplified
                else:
                    return {"success": False, "error": f"GitHub API Fallback {resp.status_code}: {resp.text[:300]}"}
            else:
                return {"success": False, "error": f"GitHub API {resp.status_code}: {resp.text[:300]}"}
    except Exception as exc:
        return {"success": False, "error": f"GitHub API request failed: {exc}"}

    # Set authenticated remote URL (embeds token for seamless push)
    auth_url = f"https://x-access-token:{gh_token}@github.com/{full_repo_name}.git"

    # Check if remote already exists
    remote_check = await executor.execute("git remote", cwd=workspace_path)
    if "origin" in (remote_check.stdout or ""):
        # Update existing remote
        await executor.execute(
            f"git remote set-url origin {auth_url}", cwd=workspace_path
        )
    else:
        await executor.execute(
            f"git remote add origin {auth_url}", cwd=workspace_path
        )

    # Ensure we're on main branch
    await executor.execute("git branch -M main", cwd=workspace_path)

    # Push
    push_result = await executor.execute(
        "git push -u origin main --force", cwd=workspace_path, timeout=30
    )

    if push_result.exit_code != 0:
        logger.warning("Git push failed: %s", (push_result.stderr or "")[:300])
        return {
            "success": False,
            "repo_url": repo_url,
            "error": f"git push failed: {(push_result.stderr or push_result.stdout or '').strip()[:300]}",
        }

    return {"success": True, "repo_url": repo_url}


# ---------------------------------------------------------------------------
# Tool: deploy_to_vercel
# ---------------------------------------------------------------------------

async def deploy_to_vercel(workspace_path: str) -> dict[str, Any]:
    """Deploy a workspace to Vercel.

    Preferred method: Vercel CLI with VERCEL_TOKEN (production deploy).
    Fallback: Legacy tarball POST to VERCEL_DEPLOY_ENDPOINT.
    """
    vercel_token = settings.VERCEL_TOKEN
    vercel_org = settings.VERCEL_ORG_ID
    vercel_project = settings.VERCEL_PROJECT_ID

    # Preferred: use Vercel CLI
    if vercel_token:
        return await _deploy_via_vercel_cli(workspace_path, vercel_token, vercel_org, vercel_project)

    # Fallback: legacy endpoint
    endpoint = settings.VERCEL_DEPLOY_ENDPOINT
    if endpoint:
        return await _deploy_via_endpoint(workspace_path, endpoint)

    return {"success": False, "error": "Neither VERCEL_TOKEN nor VERCEL_DEPLOY_ENDPOINT configured"}


async def _deploy_via_vercel_cli(
    workspace_path: str,
    token: str,
    org_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Deploy using the Vercel CLI (vercel --prod).

    Steps:
    1. Write .vercel/project.json to link the project
    2. Run `vercel pull` to fetch project settings
    3. Run `vercel build --prod` to build
    4. Run `vercel deploy --prebuilt --prod` to deploy
    """
    executor = SandboxExecutor(timeout=180)
    ws = Path(workspace_path)

    # Ensure vercel CLI is available (install if not)
    check = await executor.execute("npx vercel --version", cwd=workspace_path, timeout=30)
    if check.exit_code != 0:
        logger.info("Installing Vercel CLI...")
        install = await executor.execute("npm install -g vercel", cwd=workspace_path, timeout=60)
        if install.exit_code != 0:
            return {"success": False, "error": f"Failed to install Vercel CLI: {install.stderr[:300]}"}

    # Write .vercel/project.json to link the workspace to the project
    if org_id and project_id:
        vercel_dir = ws / ".vercel"
        vercel_dir.mkdir(exist_ok=True)
        project_json = {"orgId": org_id, "projectId": project_id}
        (vercel_dir / "project.json").write_text(json.dumps(project_json), encoding="utf-8")

    # Set env vars for vercel CLI auth
    env_prefix = f"VERCEL_TOKEN={token}"
    if org_id:
        env_prefix += f" VERCEL_ORG_ID={org_id}"
    if project_id:
        env_prefix += f" VERCEL_PROJECT_ID={project_id}"

    # Pull project settings
    pull_result = await executor.execute(
        f"{env_prefix} npx vercel pull --yes --environment=production --token={token}",
        cwd=workspace_path,
        timeout=60,
    )
    if pull_result.exit_code != 0:
        logger.warning("vercel pull failed (non-fatal): %s", pull_result.stderr[:300])

    # Build
    build_result = await executor.execute(
        f"{env_prefix} npx vercel build --yes --prod --token={token}",
        cwd=workspace_path,
        timeout=120,
    )
    if build_result.exit_code != 0:
        # Try deploying without prebuilt if build fails
        logger.warning("vercel build failed, trying direct deploy: %s", build_result.stderr[:300])
        deploy_result = await executor.execute(
            f"{env_prefix} npx vercel --prod --yes --token={token}",
            cwd=workspace_path,
            timeout=120,
        )
    else:
        # Deploy prebuilt
        deploy_result = await executor.execute(
            f"{env_prefix} npx vercel deploy --prebuilt --yes --prod --token={token}",
            cwd=workspace_path,
            timeout=120,
        )

    output = deploy_result.stdout + deploy_result.stderr
    if deploy_result.exit_code != 0:
        return {"success": False, "error": f"Vercel deploy failed: {output[:500]}"}

    # Extract the deployment URL from output
    preview_url = ""
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("https://") and "vercel" in line:
            preview_url = line
            break

    if not preview_url:
        # Try to find any URL in output
        url_match = re.search(r"https://[^\s]+\.vercel\.app[^\s]*", output)
        if url_match:
            preview_url = url_match.group(0)

    return {
        "success": True,
        "preview_url": preview_url,
        "claim_url": "",
        "deployment_id": "",
    }


async def _deploy_via_endpoint(workspace_path: str, endpoint: str) -> dict[str, Any]:
    """Legacy: deploy via tarball POST to custom endpoint."""
    framework = _detect_framework(workspace_path)
    if not framework:
        return {"success": False, "error": "No deployable framework detected"}

    ws = Path(workspace_path)

    # Create tarball in memory
    exclude_dirs = {
        "node_modules", ".git", "__pycache__", ".next", ".nuxt",
        "dist", "build", ".venv", "venv", ".cache",
    }
    exclude_extensions = {".pyc", ".pyo", ".so", ".o"}

    tar_buffer = io.BytesIO()
    try:
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            for item in ws.rglob("*"):
                rel = item.relative_to(ws)
                if any(part in exclude_dirs for part in rel.parts):
                    continue
                if item.suffix in exclude_extensions:
                    continue
                if item.is_file():
                    tar.add(str(item), arcname=str(rel))
    except Exception as exc:
        return {"success": False, "error": f"Failed to create tarball: {exc}"}

    tar_buffer.seek(0)

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                endpoint,
                files={"file": ("project.tar.gz", tar_buffer, "application/gzip")},
                data={"framework": framework},
            )
            resp.raise_for_status()
            body = resp.json()

            return {
                "success": True,
                "preview_url": body.get("preview_url", body.get("url", "")),
                "claim_url": body.get("claim_url", ""),
                "deployment_id": body.get("deployment_id", body.get("id", "")),
            }
    except httpx.HTTPStatusError as exc:
        return {"success": False, "error": f"Deploy API returned {exc.response.status_code}: {exc.response.text[:300]}"}
    except Exception as exc:
        return {"success": False, "error": f"Deploy request failed: {exc}"}


# ---------------------------------------------------------------------------
# Tool: run_full_test_suite
# ---------------------------------------------------------------------------

async def run_full_test_suite(workspace_path: str) -> dict[str, Any]:
    """Run a comprehensive test suite on the workspace.

    Auto-detects project type (Python vs Node.js) and runs up to 4 stages:
    1. Lint
    2. Typecheck
    3. Unit tests
    4. Build

    Returns structured results. Lint/typecheck are advisory (non-blocking)
    for projects without explicit configuration.
    """
    ws = Path(workspace_path)
    executor = SandboxExecutor(timeout=120)

    is_node = (ws / "package.json").exists()
    is_python = (
        (ws / "requirements.txt").exists()
        or (ws / "pyproject.toml").exists()
        or (ws / "setup.py").exists()
    )

    results: dict[str, Any] = {
        "lint_passed": None,
        "typecheck_passed": None,
        "tests_passed": None,
        "build_passed": None,
        "summary": "",
        "details": {},
    }

    stages_run = 0
    stages_passed = 0

    if is_node:
        # Install dependencies first
        install_result = await executor.execute("npm install", cwd=workspace_path, timeout=120)
        if install_result.exit_code != 0:
            results["details"]["install"] = install_result.stderr[:500]

        # 1. Lint
        pkg_json = ws / "package.json"
        has_lint = False
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                has_lint = "lint" in pkg.get("scripts", {})
            except (json.JSONDecodeError, OSError):
                pass

        if has_lint:
            lint_result = await executor.execute("npm run lint", cwd=workspace_path, timeout=60)
            results["lint_passed"] = lint_result.exit_code == 0
            results["details"]["lint"] = (lint_result.stdout + lint_result.stderr)[:1000]
            stages_run += 1
            if results["lint_passed"]:
                stages_passed += 1

        # 2. Typecheck
        has_typecheck = (ws / "tsconfig.json").exists()
        if has_typecheck:
            tc_result = await executor.execute("npx tsc --noEmit", cwd=workspace_path, timeout=60)
            results["typecheck_passed"] = tc_result.exit_code == 0
            results["details"]["typecheck"] = (tc_result.stdout + tc_result.stderr)[:1000]
            stages_run += 1
            if results["typecheck_passed"]:
                stages_passed += 1

        # 3. Unit tests
        has_test = False
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                has_test = "test" in pkg.get("scripts", {})
            except (json.JSONDecodeError, OSError):
                pass

        if has_test:
            test_result = await executor.execute("npm test -- --passWithNoTests", cwd=workspace_path, timeout=90)
            results["tests_passed"] = test_result.exit_code == 0
            results["details"]["tests"] = (test_result.stdout + test_result.stderr)[:1000]
            stages_run += 1
            if results["tests_passed"]:
                stages_passed += 1

        # 4. Build
        has_build = False
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                has_build = "build" in pkg.get("scripts", {})
            except (json.JSONDecodeError, OSError):
                pass

        if has_build:
            build_result = await executor.execute("npm run build", cwd=workspace_path, timeout=120)
            results["build_passed"] = build_result.exit_code == 0
            results["details"]["build"] = (build_result.stdout + build_result.stderr)[:1000]
            stages_run += 1
            if results["build_passed"]:
                stages_passed += 1

    elif is_python:
        # 1. Lint (flake8 if available)
        lint_result = await executor.execute(
            "python -m flake8 . --max-line-length=120 --exclude=venv,.venv,node_modules",
            cwd=workspace_path, timeout=30,
        )
        results["lint_passed"] = lint_result.exit_code == 0
        results["details"]["lint"] = (lint_result.stdout + lint_result.stderr)[:1000]
        stages_run += 1
        if results["lint_passed"]:
            stages_passed += 1

        # 2. Typecheck (mypy if config exists)
        has_mypy = (ws / "mypy.ini").exists() or (ws / "setup.cfg").exists()
        if has_mypy:
            tc_result = await executor.execute("python -m mypy .", cwd=workspace_path, timeout=60)
            results["typecheck_passed"] = tc_result.exit_code == 0
            results["details"]["typecheck"] = (tc_result.stdout + tc_result.stderr)[:1000]
            stages_run += 1
            if results["typecheck_passed"]:
                stages_passed += 1

        # 3. Unit tests (pytest)
        test_result = await executor.execute("python -m pytest -x -q", cwd=workspace_path, timeout=90)
        results["tests_passed"] = test_result.exit_code == 0
        results["details"]["tests"] = (test_result.stdout + test_result.stderr)[:1000]
        stages_run += 1
        if results["tests_passed"]:
            stages_passed += 1

    else:
        results["summary"] = "No recognized project type (Node.js or Python) detected"
        return results

    results["summary"] = f"{stages_passed}/{stages_run} stages passed"
    return results


# Exported list for tool registration
DEPLOYMENT_TOOLS = [create_github_repo, deploy_to_vercel, run_full_test_suite]
