"""
Oriexa Deploy Agent — Vercel Deployment with Smoke Testing

Handles deployment pipeline:
  1. Runs Vercel production deployment
  2. Waits for propagation
  3. Smoke tests the deployed URL
  4. Commits deploy results
  5. Submits deliverable to Oriexa API

Usage (called by orchestrator, not directly):
    python -m agents.deploy_agent --api-key <key> --task-id <id> [--base-url <url>]
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
import shutil
from collections.abc import Iterable
from pathlib import Path

# Add parent path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import (
    BASE_URL,
    OriexaClient,
    log_err,
    log_ok,
    log_think,
    log_warn,
    smart_llm_call,
    write_progress,
)
from agents.git_ops import (
    commit_step,
    push_to_remote,
    append_commit_log,
    has_meaningful_implementation,
    verify_remote_has_main,
    verify_remote_head_matches_local,
)
from agents.shell_executor import run_shell_combined, append_build_log, log_command
from app.services.agent_workspaces import (
    cleanup_workspace,
    ensure_local_workspace,
    load_swarm_state,
    write_swarm_state,
)

import subprocess
import httpx

AGENT_NAME = "Deployer"
WORKSPACE_DIR = Path(os.environ.get("AGENT_WORKSPACE_DIR", str(Path(__file__).parent.parent / "agent_works")))

VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN")
VERCEL_ORG_ID = os.environ.get("VERCEL_ORG_ID")
VERCEL_PROJECT_ID = os.environ.get("VERCEL_PROJECT_ID")
VERCEL_PUBLIC_SCOPE = os.environ.get("VERCEL_PUBLIC_SCOPE", "").strip()
VERCEL_USE_LINKED_PROJECT = os.environ.get("VERCEL_USE_LINKED_PROJECT", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
EMOJI_RE = re.compile(r"[\U0001F1E0-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]", re.UNICODE)
README_EXCLUDED_DIRS = {
    ".git",
    ".next",
    ".vercel",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "coverage",
    "venv",
    ".venv",
}


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", EMOJI_RE.sub("", (value or "")).strip())


def _truncate_text(value: str | None, limit: int = 280) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = item.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _extract_list_items(text: str | None, *, limit: int = 6) -> list[str]:
    if not text:
        return []

    items: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^[-*•]\s+", line):
            items.append(re.sub(r"^[-*•]\s+", "", line).strip())
            continue
        if re.match(r"^\d+[.)]\s+", line):
            items.append(re.sub(r"^\d+[.)]\s+", "", line).strip())

    if items:
        return _dedupe(items)[:limit]

    normalized = re.sub(r"[\r\n]+", " ", text)
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    return _dedupe(_clean_text(sentence) for sentence in sentences if _clean_text(sentence))[:limit]


def _load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _detect_tech_stack(task_dir: Path) -> list[str]:
    stack: list[str] = []
    package_json = _load_json_file(task_dir / "package.json")
    deps = {
        **(package_json.get("dependencies") or {}),
        **(package_json.get("devDependencies") or {}),
    }

    if package_json:
        stack.append("Node.js")
    if "next" in deps:
        stack.append("Next.js")
    if "react" in deps:
        stack.append("React")
    if "typescript" in deps:
        stack.append("TypeScript")
    if "tailwindcss" in deps or "@tailwindcss/postcss" in deps:
        stack.append("Tailwind CSS")
    if (task_dir / "pyproject.toml").exists() or (task_dir / "requirements.txt").exists():
        stack.append("Python")

    return _dedupe(stack) or ["Project-specific application stack"]


def _detect_install_command(task_dir: Path) -> str:
    if (task_dir / "pnpm-lock.yaml").exists():
        return "pnpm install"
    if (task_dir / "yarn.lock").exists():
        return "yarn install"
    if (task_dir / "package.json").exists():
        return "npm install"
    if (task_dir / "requirements.txt").exists():
        return "pip install -r requirements.txt"
    if (task_dir / "pyproject.toml").exists():
        return "pip install -e ."
    return "Review the project-specific setup files before installation."


def _detect_run_commands(task_dir: Path) -> list[str]:
    package_json = _load_json_file(task_dir / "package.json")
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}

    commands: list[str] = []
    if "dev" in scripts:
        commands.append("npm run dev")
    if "build" in scripts:
        commands.append("npm run build")
    if "test" in scripts:
        commands.append("npm test")
    if "lint" in scripts:
        commands.append("npm run lint")

    if commands:
        return commands

    if (task_dir / "requirements.txt").exists() or (task_dir / "pyproject.toml").exists():
        return ["python -m pytest"]
    return []


def _collect_project_structure(task_dir: Path, *, limit: int = 8) -> list[str]:
    entries: list[str] = []
    for path in sorted(task_dir.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        name = path.name
        if name in README_EXCLUDED_DIRS or name.startswith("."):
            continue
        suffix = "/" if path.is_dir() else ""
        entries.append(f"{name}{suffix}")
        if len(entries) >= limit:
            break
    return entries


def _collect_scope_points(state: dict, requirements: str) -> list[str]:
    completed_steps = state.get("completed_steps") or []
    step_points = [
        step.get("description") or step.get("commit_message") or ""
        for step in completed_steps
        if isinstance(step, dict)
    ]
    if step_points:
        return _dedupe(_clean_text(point) for point in step_points if _clean_text(point))[:6]
    return _extract_list_items(requirements)


def _render_readme(
    *,
    task_id: int,
    title: str,
    description: str,
    requirements: str,
    repo_url: str,
    task_dir: Path,
    state: dict,
) -> str:
    project_title = _clean_text(title) or f"Task {task_id}"
    overview = _truncate_text(description or requirements or f"Delivery for Oriexa task #{task_id}.", 420)
    scope_points = _collect_scope_points(state, requirements)
    tech_stack = _detect_tech_stack(task_dir)
    install_command = _detect_install_command(task_dir)
    run_commands = _detect_run_commands(task_dir)
    structure = _collect_project_structure(task_dir)
    package_json = _load_json_file(task_dir / "package.json")
    scripts = package_json.get("scripts") if isinstance(package_json.get("scripts"), dict) else {}
    deployment_url = _clean_text(state.get("vercel_url") or "")
    smoke_test = state.get("smoke_test") if isinstance(state.get("smoke_test"), dict) else {}
    smoke_details = _truncate_text(smoke_test.get("details") if isinstance(smoke_test, dict) else "", 220)
    deployment_mode = _clean_text(state.get("deployment_mode") or "")

    lines = [
        f"# {project_title}",
        "",
        f"Professional delivery for Oriexa task #{task_id}.",
        "",
        "## Overview",
        overview,
        "",
        "## Scope",
    ]

    if scope_points:
        lines.extend(f"- {point}" for point in scope_points)
    else:
        lines.append("- Core implementation completed and prepared for deployment.")

    lines.extend([
        "",
        "## Tech Stack",
        *[f"- {item}" for item in tech_stack],
        "",
        "## Getting Started",
        "```bash",
        install_command,
    ])

    for command in run_commands[:3]:
        lines.append(command)

    lines.extend([
        "```",
        "",
    ])

    if scripts:
        lines.append("## Available Scripts")
        for name, command in scripts.items():
            lines.append(f"- `{name}`: {_truncate_text(str(command), 140)}")
        lines.append("")

    if structure:
        lines.extend([
            "## Project Structure",
            "```text",
            *structure,
            "```",
            "",
        ])

    lines.extend([
        "## Repository",
        f"- GitHub: {repo_url}",
        f"- Task ID: {task_id}",
        "",
        "## Deployment",
    ])

    if deployment_url:
        lines.append(f"- Vercel URL: {deployment_url}")
        if smoke_test:
            lines.append(
                f"- Smoke test: {'Passed' if smoke_test.get('passed') else 'Warning'}"
                + (f" ({smoke_details})" if smoke_details else "")
            )
        if deployment_mode:
            lines.append(f"- Deployment mode: {deployment_mode}")
        if _is_private_or_protected_smoke_failure(smoke_details):
            lines.append("- Visibility: Deployment exists but is access-protected/private.")
    else:
        lines.append("- Deployment details will be written here after the deploy step completes.")

    lines.extend([
        "",
        "## Notes",
        "- This README is refreshed automatically during the deploy pipeline and finalized after delivery metadata is known.",
        "- Keep this document aligned with the shipped implementation and setup commands.",
    ])

    return "\n".join(lines).strip() + "\n"


def _refresh_readme(
    task_dir: Path,
    *,
    task_id: int,
    title: str,
    description: str,
    requirements: str,
    repo_url: str,
    state: dict,
) -> bool:
    readme_path = task_dir / "README.md"
    content = _render_readme(
        task_id=task_id,
        title=title,
        description=description,
        requirements=requirements,
        repo_url=repo_url,
        task_dir=task_dir,
        state=state,
    )
    previous = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    if previous == content:
        return False
    readme_path.write_text(content, encoding="utf-8")
    return True


def _is_private_or_protected_smoke_failure(details: str | None) -> bool:
    lowered = (details or "").lower()
    return (
        "protected/private" in lowered
        or "deployment is protected" in lowered
        or "http 401" in lowered
        or "http 403" in lowered
    )


# ═══════════════════════════════════════════════════════════════════════════
# VERCEL DEPLOYMENT
# ═══════════════════════════════════════════════════════════════════════════

def run_vercel_deploy(
    task_dir: Path,
    *,
    production: bool = True,
    force_unlinked: bool = False,
) -> str | None:
    """
    Run Vercel CLI deployment flow.

    Production mode:
      - vercel pull (linked only)
      - vercel build --prod
      - vercel deploy --prebuilt --prod

    Preview mode:
      - vercel build
      - vercel deploy --prebuilt --public
    """
    mode = "production" if production else "preview"
    log_think(f"Executing Vercel {mode} deployment...", AGENT_NAME)
    append_build_log(task_dir, "Starting Vercel deployment...")

    if not VERCEL_TOKEN:
        log_warn("VERCEL_TOKEN missing. Deployment requires token-based production flow.", AGENT_NAME)
        return None

    env = os.environ.copy()
    use_linked_project = VERCEL_USE_LINKED_PROJECT and not force_unlinked
    if use_linked_project and VERCEL_ORG_ID and VERCEL_PROJECT_ID:
        env["VERCEL_ORG_ID"] = VERCEL_ORG_ID
        env["VERCEL_PROJECT_ID"] = VERCEL_PROJECT_ID
        log_think("Using linked Vercel project mode (VERCEL_USE_LINKED_PROJECT=true).", AGENT_NAME)
    else:
        env.pop("VERCEL_ORG_ID", None)
        env.pop("VERCEL_PROJECT_ID", None)
        stale_link_dir = task_dir / ".vercel"
        if stale_link_dir.exists():
            try:
                shutil.rmtree(stale_link_dir, ignore_errors=True)
            except Exception:
                pass
        log_think("Using unlinked Vercel deployment mode (framework auto-detection).", AGENT_NAME)

    def _run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
        proc = subprocess.run(
            cmd,
            cwd=str(task_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        output = (proc.stdout + "\n" + proc.stderr).strip()
        clean_output = ANSI_ESCAPE_RE.sub("", output)
        safe_cmd = " ".join("--token=$VERCEL_TOKEN" if VERCEL_TOKEN in part else part for part in cmd)
        log_command(task_dir, safe_cmd, proc.returncode, clean_output)
        return proc.returncode, clean_output

    fallback_scope = VERCEL_PUBLIC_SCOPE

    try:
        rc, _ = _run(["vercel", "--version"], timeout=30)
        if rc != 0:
            log_warn("vercel CLI not available. Installing globally...", AGENT_NAME)
            install = subprocess.run(
                ["npm", "install", "-g", "vercel"],
                cwd=str(task_dir),
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            install_out = ANSI_ESCAPE_RE.sub("", (install.stdout + "\n" + install.stderr).strip())
            log_command(task_dir, "npm install -g vercel", install.returncode, install_out)
            if install.returncode != 0:
                append_build_log(task_dir, f"Vercel CLI install failed: {install_out[:600]}")
                return None
    except FileNotFoundError:
        log_warn("npm not found, cannot install vercel CLI.", AGENT_NAME)
        return None
    except Exception as e:
        log_warn(f"Failed to verify/install vercel CLI: {e}", AGENT_NAME)
        return None

    commands: list[list[str]] = []
    if use_linked_project:
        if production:
            commands.append(["vercel", "pull", "--yes", "--environment=production", f"--token={VERCEL_TOKEN}"])
            commands.append(["vercel", "build", "--prod", f"--token={VERCEL_TOKEN}"])
            commands.append(["vercel", "deploy", "--prebuilt", "--prod", "--public", "--yes", f"--token={VERCEL_TOKEN}"])
        else:
            commands.append(["vercel", "pull", "--yes", "--environment=preview", f"--token={VERCEL_TOKEN}"])
            commands.append(["vercel", "build", f"--token={VERCEL_TOKEN}"])
            commands.append(["vercel", "deploy", "--prebuilt", "--public", "--yes", f"--token={VERCEL_TOKEN}"])
    else:
        deploy_cmd = ["vercel", "deploy", "--public", "--yes", f"--token={VERCEL_TOKEN}"]
        if production:
            deploy_cmd.append("--prod")
        if fallback_scope:
            deploy_cmd.append(f"--scope={fallback_scope}")
            log_think(f"Using fallback public scope: {fallback_scope}", AGENT_NAME)
        commands.append(deploy_cmd)

    last_output = ""
    for i, cmd in enumerate(commands):
        rc, out = _run(cmd, timeout=900 if i == 1 else 600)
        last_output = out
        if rc != 0:
            personal_scope_error = "you cannot set your personal account as the scope" in out.lower()
            if personal_scope_error and any(part.startswith("--scope=") for part in cmd):
                retry_cmd = [part for part in cmd if not part.startswith("--scope=")]
                append_build_log(task_dir, "Vercel rejected personal-account scope; retrying without --scope.")
                rc, out = _run(retry_cmd, timeout=900 if i == 1 else 600)
                last_output = out
                if rc == 0:
                    continue
            if i == 0 and not use_linked_project and "pull" in cmd:
                log_warn("vercel pull failed in unlinked mode; continuing with build/deploy.", AGENT_NAME)
                continue
            append_build_log(task_dir, f"Vercel command failed: {' '.join(cmd[:3])} -> {out[:600]}")
            return None

    prod_match = re.search(r"Production:\s*(https://[^\s]+)", last_output)
    if prod_match:
        deployed_url = prod_match.group(1).rstrip(").,")
        log_ok(f"Vercel deploy succeeded: {deployed_url}", AGENT_NAME)
        return deployed_url

    urls = re.findall(r"https://[^\s)]+", last_output)
    vercel_urls = [u.rstrip(").,") for u in urls if "vercel.app" in u]
    if vercel_urls:
        log_ok(f"Vercel deploy succeeded: {vercel_urls[0]}", AGENT_NAME)
        return vercel_urls[0]

    append_build_log(task_dir, f"Vercel deploy output missing vercel.app URL: {last_output[:600]}")
    log_warn("Vercel deploy completed but no public URL was found in output.", AGENT_NAME)
    return None

def smoke_test(url: str, retries: int = 3, wait: int = 10) -> tuple[bool, str]:
    """
    Hit the deployed URL and verify it's alive.

    Returns:
        (passed: bool, details: str)
    """
    log_think(f"Smoke testing: {url} (max {retries} attempts)...", AGENT_NAME)

    for attempt in range(1, retries + 1):
        try:
            log_think(f"  Attempt {attempt}/{retries}...", AGENT_NAME)
            time.sleep(wait if attempt == 1 else 5)

            resp = httpx.get(url, timeout=15.0, follow_redirects=True)
            status = resp.status_code
            body_len = len(resp.text)

            # Explicitly detect private/protected deployments.
            if status in (401, 403):
                return (
                    False,
                    f"HTTP {status} — deployment is protected/private. Disable Vercel deployment protection to make it public.",
                )

            if status == 200 and body_len > 100:
                # Check it's not an error page
                lower = resp.text.lower()
                if "application error" not in lower and "internal server error" not in lower:
                    return True, f"HTTP {status}, {body_len} bytes — site is live"

            log_warn(
                f"  Attempt {attempt}: HTTP {status}, body={body_len} bytes",
                AGENT_NAME
            )
        except Exception as e:
            log_warn(f"  Attempt {attempt} failed: {e}", AGENT_NAME)

    return False, f"Smoke test failed after {retries} attempts"


def _smoke_test_curl(url: str, retries: int, wait: int) -> tuple[bool, str]:
    """Fallback smoke test using curl."""
    for attempt in range(1, retries + 1):
        time.sleep(wait if attempt == 1 else 5)
        rc, out = run_shell_combined(
            f'curl -s -o /dev/null -w "%{{http_code}}" {url}',
            Path("."), timeout=15
        )
        if rc == 0 and out.strip() == "200":
            return True, f"HTTP 200 — site is live (curl)"
    return False, f"Smoke test failed after {retries} attempts (curl)"


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PROCESS
# ═══════════════════════════════════════════════════════════════════════════

def process_task(client: OriexaClient, task_id: int) -> dict:
    task_dir: Path | None = None
    state_file: Path | None = None
    state: dict | None = None
    try:
        task_dir, _, rehydrated = ensure_local_workspace(
            task_id,
            workspace_root=WORKSPACE_DIR,
        )
        state_file = task_dir / ".swarm_state.json"
        if rehydrated:
            log_think(f"Rehydrated workspace from GitHub for task #{task_id}", AGENT_NAME)

        state = load_swarm_state(task_id, workspace_dir=task_dir)
        if not state:
            return {"action": "error", "error": f"State file not found for task {task_id}"}

        if state.get("status") != "deploying":
            return {"action": "no_result", "reason": f"State is {state.get('status')}, not deploying."}

        task_data = {}
        try:
            task_data = client.get_task(task_id) or {}
        except Exception:
            task_data = {}

        task_title = task_data.get("title") or f"Task #{task_id}"
        task_desc = task_data.get("description") or ""
        task_reqs = task_data.get("requirements") or ""
        repo_url = state.get("repo_url", "No Repo URL Provided")
        iterations = state.get("iterations", 1)
        append_build_log(task_dir, f"=== Deploy Agent starting for task #{task_id} ===")
        write_progress(task_dir, task_id, "deploying", "Deploying to Vercel",
                       "Running Vercel production deployment",
                       "Initializing deployment pipeline...", 96.0, subtask_id=101)

        def fail(error_message: str) -> dict:
            append_build_log(task_dir, f"DEPLOY FAILED: {error_message}")
            write_progress(
                task_dir,
                task_id,
                "failed",
                "Deployment failed",
                error_message,
                "Deployment halted. Manual intervention required.",
                100.0,
                subtask_id=101,
            )
            state["status"] = "failed"
            state["error"] = error_message
            state["failed_at"] = time.time()
            write_swarm_state(task_id, state, workspace_dir=task_dir)
            return {"action": "error", "error": error_message}

        if not repo_url or "No Repo URL" in repo_url:
            return fail("Missing GitHub repository URL; deployment blocked")
        if not has_meaningful_implementation(task_dir):
            return fail("No meaningful implementation files in repository; deployment blocked")

        write_progress(
            task_dir,
            task_id,
            "deploying",
            "Refreshing project documentation",
            "Updating README before deployment",
            "Preparing professional repository documentation...",
            95.0,
            subtask_id=101,
            metadata={"repo": repo_url},
        )
        readme_changed = _refresh_readme(
            task_dir,
            task_id=task_id,
            title=task_title,
            description=task_desc,
            requirements=task_reqs,
            repo_url=repo_url,
            state=state,
        )
        if readme_changed:
            h = commit_step(task_dir, "docs: refresh project README", files=["README.md"])
            if h:
                append_commit_log(task_dir, h, "docs: refresh project README")
                push_to_remote(task_dir)
                append_build_log(task_dir, f"README updated and pushed [{h}]")

        if not verify_remote_has_main(task_dir):
            push_to_remote(task_dir)
            if not verify_remote_has_main(task_dir):
                return fail("GitHub remote main branch missing; deployment blocked")
        if not verify_remote_head_matches_local(task_dir):
            push_to_remote(task_dir)
            if not verify_remote_head_matches_local(task_dir):
                return fail("GitHub remote main is behind local HEAD; deployment blocked")

        # ── Deploy to Vercel ──────────────────────────────────────────
        vercel_url = run_vercel_deploy(task_dir, production=True)
        deploy_passed = False

        if vercel_url:
            log_ok(f"Vercel Deployment URL: {vercel_url}", AGENT_NAME)
            state["vercel_url"] = vercel_url
            state["deployment_mode"] = "production"
            write_progress(task_dir, task_id, "deploying", "Smoke testing deployment",
                           f"Verifying deployment is live at {vercel_url}",
                           "Running smoke test...", 97.0, subtask_id=101,
                           metadata={"url": vercel_url})

            # ── Smoke Test ────────────────────────────────────────────
            passed, details = smoke_test(vercel_url)
            deploy_passed = passed

            if passed:
                log_ok(f"Smoke test PASSED: {details}", AGENT_NAME)
                write_progress(task_dir, task_id, "deploying", "Deployment live",
                               f"Site is live and responding — {vercel_url}",
                               details, 99.0, subtask_id=101,
                               metadata={"url": vercel_url, "smoke_test": "passed"})
                state["vercel_url"] = vercel_url
                state["smoke_test"] = {"passed": True, "details": details}
                append_build_log(task_dir, f"Smoke test PASSED: {details}")
            else:
                log_warn(f"Smoke test FAILED: {details}", AGENT_NAME)
                state["smoke_test"] = {"passed": False, "details": details}
                append_build_log(task_dir, f"Smoke test FAILED: {details}")

                is_protection_error = _is_private_or_protected_smoke_failure(details)
                if is_protection_error:
                    log_warn("Production deployment appears protected. Retrying with unlinked public preview deployment...", AGENT_NAME)
                    write_progress(
                        task_dir,
                        task_id,
                        "deploying",
                        "Retrying with public preview",
                        "Production URL is protected; creating public preview deployment",
                        "Switching to unlinked preview deploy mode...",
                        98.0,
                        subtask_id=101,
                    )
                    preview_url = run_vercel_deploy(task_dir, production=False, force_unlinked=True)
                    if preview_url:
                        passed_preview, preview_details = smoke_test(preview_url)
                        if passed_preview:
                            log_ok(f"Public preview smoke test PASSED: {preview_details}", AGENT_NAME)
                            state["vercel_url"] = preview_url
                            state["smoke_test"] = {"passed": True, "details": preview_details}
                            state["deployment_mode"] = "preview_public_fallback"
                            deploy_passed = True
                        else:
                            state["vercel_url"] = preview_url
                            state["smoke_test"] = {"passed": False, "details": preview_details}
                            state["deployment_mode"] = "preview_protected_fallback"
                            append_build_log(
                                task_dir,
                                "Preview fallback also appears protected/private; continuing to delivery because a Vercel deployment URL exists.",
                            )
                    else:
                        state["deployment_mode"] = "production_private"
                        append_build_log(
                            task_dir,
                            "Public preview fallback failed, but the protected production deployment will still be delivered.",
                        )

                    # Continue without retrying protected production deploy.
                    vercel_url = state.get("vercel_url")

                    if not vercel_url:
                        return fail("Deployment completed without a Vercel URL")
                else:

                    # Retry deploy once for non-protection transient failures.
                    log_think("Retrying Vercel deployment...", AGENT_NAME)
                    vercel_url_retry = run_vercel_deploy(task_dir, production=True)
                    if vercel_url_retry:
                        time.sleep(15)
                        passed2, details2 = smoke_test(vercel_url_retry)
                        if passed2:
                            log_ok(f"Retry smoke test PASSED: {details2}", AGENT_NAME)
                            state["vercel_url"] = vercel_url_retry
                            state["smoke_test"] = {"passed": True, "details": details2}
                            vercel_url = vercel_url_retry
                            deploy_passed = True
                        else:
                            log_warn("Retry smoke test also FAILED.", AGENT_NAME)
                            state["vercel_url"] = vercel_url_retry
                            return fail(
                                "Deployment URL is not publicly reachable after retry "
                                f"({details2})."
                            )
                    else:
                        return fail(
                            "Deployment URL is not publicly reachable "
                            f"({details})."
                        )
        else:
            log_warn("Vercel deployment skipped or failed.", AGENT_NAME)
            vercel_url = None

        if not vercel_url:
            return fail("Vercel deployment failed; deliverable submission blocked")

        # ── Commit deploy results ─────────────────────────────────────
        final_readme_changed = _refresh_readme(
            task_dir,
            task_id=task_id,
            title=task_title,
            description=task_desc,
            requirements=task_reqs,
            repo_url=repo_url,
            state=state,
        )
        deploy_summary = {
            "vercel_url": state.get("vercel_url"),
            "smoke_test": state.get("smoke_test"),
            "deployment_mode": state.get("deployment_mode"),
            "deployed_at": time.time(),
        }
        deploy_file = task_dir / ".deploy_results.json"
        deploy_file.write_text(json.dumps(deploy_summary, indent=2), encoding="utf-8")

        h = commit_step(task_dir, f"chore: deploy to Vercel — {state.get('vercel_url', 'skipped')}")
        if h:
            append_commit_log(task_dir, h, "chore: deploy to Vercel")
            push_to_remote(task_dir)
            log_ok(f"Deploy results committed [{h}] and pushed", AGENT_NAME)
            if final_readme_changed:
                append_build_log(task_dir, f"Final README refreshed with deployment details [{h}]")

        # ── Craft deliverable ─────────────────────────────────────────
        vercel_live = state.get("vercel_url")
        smoke_details = (state.get("smoke_test") or {}).get("details", "")

        task_desc = (task_data.get("description") or "")[:600]
        task_reqs = (task_data.get("requirements") or "")[:400]

        # Collect what was implemented from the plan + completed steps
        plan = state.get("plan") or {}
        project_type = plan.get("project_type") or "project"
        completed_steps = state.get("completed_steps") or []
        step_descriptions = [
            s.get("description") or s.get("commit_message") or ""
            for s in completed_steps
            if s.get("description") or s.get("commit_message")
        ]

        steps_text = "\n".join(f"- {d}" for d in step_descriptions) if step_descriptions else "Full implementation completed."

        # Generate a natural language summary using LLM
        log_think("Generating natural language deliverable summary...", AGENT_NAME)
        llm_summary = ""
        try:
            llm_summary = smart_llm_call(
                system=(
                    "You are a technical project manager writing a clear, friendly delivery summary for a non-technical client. "
                    "Your job is to explain WHAT was built and WHAT features were implemented — in plain English. "
                    "Do NOT include any code, commands, installation steps, or developer jargon. "
                    "Write as if explaining to a business owner what they now have. "
                    "Be warm, clear, and concise. Use short bullet points for features. "
                    "Output only the summary text — no preamble, no markdown headers, just paragraphs and bullet points."
                ),
                user=(
                    f"Task: {task_title}\n"
                    f"Description: {task_desc}\n"
                    f"Requirements: {task_reqs}\n\n"
                    f"Project type: {project_type}\n"
                    f"Implementation steps completed:\n{steps_text}\n\n"
                    "Write a 2–3 sentence overview of what was built, followed by a bullet list of the key features and "
                    "functionality that is now available. Keep it friendly and jargon-free."
                ),
                max_tokens=600,
                complexity="routine",
            )
        except Exception as e:
            log_warn(f"LLM summary failed: {e} — using fallback", AGENT_NAME)

        # Build the final deliverable text
        delivery_lines = [
            "## Delivery Complete",
            "",
            f"**GitHub Repository**: {repo_url}",
            "**Project README**: `README.md` in the repository has been refreshed with the final project scope, setup, and deployment details.",
        ]

        if vercel_live and not vercel_live.startswith("Deployment skipped"):
            delivery_lines.append(f"**Live Deployment**: {vercel_live}")
            if deploy_passed:
                delivery_lines.append("**Smoke Test**: Passed — site is live and responding correctly")
            else:
                delivery_lines.append("**Smoke Test**: Warning — deployed but smoke test had issues")
        else:
            delivery_lines.append(
                "**Live Deployment**: Not available (no VERCEL_TOKEN configured)"
            )

        if vercel_live and not deploy_passed and _is_private_or_protected_smoke_failure(smoke_details):
            delivery_lines.append("**Access Note**: The Vercel deployment exists but is protected/private, so the URL may require access.")

        delivery_lines.append("")

        if llm_summary.strip():
            delivery_lines.append("### About this delivery")
            delivery_lines.append(llm_summary.strip())
        else:
            # Fallback: plain-text list of completed steps
            delivery_lines.append("### What was implemented")
            for d in (step_descriptions or ["Full implementation completed and pushed to GitHub."]):
                delivery_lines.append(f"- {d}")

        content = "\n".join(delivery_lines)

        # ── Submit Deliverable ────────────────────────────────────────
        try:
            client.submit_deliverable(task_id, content)
            log_ok(f"Deliverable submitted for task #{task_id}!", AGENT_NAME)
        except Exception as e:
            if "already have a submitted deliverable" in str(e).lower() or "409" in str(e):
                log_warn(f"Already submitted deliverable for task #{task_id}", AGENT_NAME)
            else:
                raise e

        # Mark finished
        write_progress(task_dir, task_id, "complete", "Delivery complete",
                       "Deliverable submitted to Oriexa — task is complete",
                       f"GitHub: {repo_url}", 100.0, subtask_id=101,
                       metadata={"repo": repo_url, "vercel": state.get("vercel_url")})
        state["status"] = "delivered"
        write_swarm_state(task_id, state, workspace_dir=task_dir)

        # ── Cleanup Workspace (Backend requirement) ─────────────────────
        try:
            if cleanup_workspace(task_id, reason="delivered", workspace_dir=task_dir):
                log_ok(f"Cleaned up local repository workspace: {task_dir}", AGENT_NAME)
            else:
                log_warn(f"Workspace cleanup skipped for task #{task_id}", AGENT_NAME)
        except Exception as e:
            log_warn(f"Failed to clean up workspace {task_dir}: {e}", AGENT_NAME)

        return {
            "action": "delivered",
            "task_id": task_id,
            "repo": repo_url,
            "vercel": state.get("vercel_url"),
            "smoke_test_passed": deploy_passed,
        }

    except Exception as e:
        log_err(f"Exception during deployment: {e}")
        log_err(traceback.format_exc().strip().splitlines()[-1])
        try:
            if task_dir:
                if state is None:
                    state = load_swarm_state(task_id, workspace_dir=task_dir) or {}
                state["status"] = "failed"
                state["error"] = str(e)
                state["failed_at"] = time.time()
                write_swarm_state(task_id, state, workspace_dir=task_dir)
                write_progress(
                    task_dir,
                    task_id,
                    "failed",
                    "Deployment failed",
                    str(e),
                    "Unhandled deploy exception",
                    100.0,
                    subtask_id=101,
                )
        except Exception:
            pass
        return {"action": "error", "error": str(e)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--task-id", type=int, required=True)
    args = parser.parse_args()

    client = OriexaClient(args.base_url, args.api_key)
    result = process_task(client, args.task_id)
    print(f"\n__RESULT__:{json.dumps(result)}")

if __name__ == "__main__":
    main()

