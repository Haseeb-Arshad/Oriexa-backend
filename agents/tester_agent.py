"""
Oriexa Tester Agent — Run Tests and Commit Results

Tests the generated codebase:
  1. Auto-installs dependencies (npm/pip)
  2. Runs the test command from the plan
  3. Commits test results (pass or fail)
  4. On failure: loops back to Coder with error context
  5. On success: advances to Deploy

Usage (called by orchestrator, not directly):
    python -m agents.tester_agent --api-key <key> --task-id <id> [--base-url <url>]
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
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
    write_progress,
)
from agents.git_ops import commit_step, push_to_remote, append_commit_log
from agents.shell_executor import (
    has_optional_dependency_issue,
    run_shell_combined,
    run_npm_install,
    run_pip_install,
    run_tests,
    append_build_log,
    log_command,
    summarize_failure_output,
)
from app.services.agent_workspaces import (
    ensure_local_workspace,
    load_swarm_state,
    write_swarm_state,
)

AGENT_NAME = "Tester"
WORKSPACE_DIR = Path(os.environ.get("AGENT_WORKSPACE_DIR", str(Path(__file__).parent.parent / "agent_works")))
MAX_TEST_RETRIES = int(os.environ.get("MAX_TEST_RETRIES", "8"))


def _failure_signature(summary: str) -> str:
    normalized = re.sub(r"\b\d+\b", "#", (summary or "").lower())
    normalized = re.sub(r"`[^`]+`", "`path`", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:220]


def _track_failure_signature(state: dict, summary: str) -> tuple[int, str]:
    signature = _failure_signature(summary)
    repeated = state.get("repeated_failure_count", 0) + 1 if state.get("last_failure_signature") == signature else 1
    state["last_failure_signature"] = signature
    state["repeated_failure_count"] = repeated
    return repeated, signature


def _build_repair_context(
    failure_kind: str,
    command: str,
    summary: str,
    output: str,
    retry_count: int,
    repeated_count: int,
) -> str:
    lower = f"{summary}\n{output}".lower()
    lines = [
        f"{failure_kind.upper()} FAILED on attempt {retry_count}.",
        f"Command: {command}",
        f"Diagnosis: {summary}",
    ]

    if repeated_count >= 2:
        lines.append(
            "Escalation: this failure signature repeated across retries. Stop patching around it and replace the incompatible dependency, config, or build approach."
        )

    if retry_count > MAX_TEST_RETRIES:
        lines.append(
            "Escalation: the soft retry limit has been exceeded. Prefer the simplest compatible implementation that passes install, build, and tests on this worker."
        )

    if "turbopack" in lower or "webpack is configured while turbopack is not" in lower or "lightningcss" in lower:
        lines.append(
            "Preferred fix: remove unstable Turbopack-only build flags, align Next.js/PostCSS/Tailwind config, or fall back to the stable webpack build path."
        )

    if "unsupported engine" in lower or "ebadengine" in lower or "node >=" in lower:
        lines.append(
            "Preferred fix: downgrade or replace packages that require a newer Node runtime than the worker provides."
        )

    if "module not found" in lower or "cannot find module" in lower:
        lines.append(
            "Preferred fix: install the missing dependency only if it is truly required; otherwise remove the import and implement the feature without that package."
        )

    lines.append("Do not give up. Return to coding, make concrete file changes, and rerun verification.")
    lines.append("Recent output:\n" + (output[-2500:] if len(output) > 2500 else output))
    return "\n".join(lines)


def _workspace_integrity_issues(task_dir: Path) -> list[str]:
    issues: list[str] = []
    pkg = task_dir / "package.json"
    lock = task_dir / "package-lock.json"

    if lock.exists() and not pkg.exists():
        issues.append("package-lock.json exists but package.json is missing")

    if not pkg.exists():
        return issues

    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        return issues + ["package.json is unreadable or invalid JSON"]

    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    if "next" in deps and not any((task_dir / candidate).exists() for candidate in ("app", "src/app", "pages")):
        issues.append("Next.js workspace is missing app/, src/app/, or pages/")

    return issues


def process_task(client: OriexaClient, task_id: int) -> dict:
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

        if state.get("status") != "testing":
            return {"action": "no_result", "reason": f"State is {state.get('status')}, not testing."}

        integrity_issues = _workspace_integrity_issues(task_dir)
        if integrity_issues:
            state["status"] = "coding"
            state["test_errors"] = "Workspace integrity failure before testing: " + "; ".join(integrity_issues)
            write_swarm_state(task_id, state, workspace_dir=task_dir)
            append_build_log(task_dir, "Workspace integrity failure: " + "; ".join(integrity_issues))
            return {"action": "tested", "task_id": task_id, "passed": False, "reason": "workspace_integrity_failed"}

        test_command = state.get("test_command")
        append_build_log(task_dir, f"=== Tester Agent starting for task #{task_id} ===")
        write_progress(task_dir, task_id, "testing", "Testing",
                       "Running automated tests to validate the implementation",
                       "Tester agent initializing...", 82.0, subtask_id=100)

        if not test_command or test_command.strip() in ("echo 'No tests defined'", 'echo "No tests defined"'):
            log_warn("No real test command provided. Will still verify build.", AGENT_NAME)
            test_command = None  # Mark as no explicit tests, but continue to build check

        # ── Detect non-runnable / interactive test commands ───────────
        # LLMs sometimes generate browser-open instructions, dev-server
        # commands, or manual instructions instead of real test runners.
        # Detect these and skip gracefully so we don't loop forever.
        if test_command:
            cmd_lower = test_command.strip().lower()
            NON_RUNNABLE_PATTERNS = [
                "open ",          # "open index.html in browser"
                "start ",         # "start http://localhost"
                "npm start",      # dev server, not tests
                "npm run dev",    # dev server
                "npm run serve",  # dev server
                "npx vite",       # dev server
                "python manage.py runserver",
                "python -m http.server",
                "live-server",
                "manually ",      # "manually test the page"
                "visit ",         # "visit http://localhost"
                "browse ",
                "click ",         # "click the button to verify"
            ]
            if any(cmd_lower.startswith(p) or p in cmd_lower for p in NON_RUNNABLE_PATTERNS):
                log_warn(
                    f"Detected non-runnable test command: '{test_command}'. "
                    "Skipping tests — will rely on build verification.",
                    AGENT_NAME,
                )
                test_command = None

        # ── Auto-install dependencies ─────────────────────────────────
        write_progress(task_dir, task_id, "testing", "Installing dependencies",
                       "Installing project dependencies before running tests",
                       "", 84.0, subtask_id=100)
        if (task_dir / "package.json").exists():
            log_think("Installing npm dependencies...", AGENT_NAME)
            rc, out = run_npm_install(task_dir)
            log_command(task_dir, "npm install", rc, out)
            if rc != 0:
                install_summary = summarize_failure_output("npm install", out)
                log_warn(f"npm install failed (rc={rc}). Attempting to proceed.", AGENT_NAME)
                write_progress(task_dir, task_id, "testing", "Dependency install failed",
                               "npm install failed before test execution; continuing so the failure can be diagnosed",
                               install_summary, 84.0, subtask_id=100,
                               metadata={"diagnosis": install_summary, "exit_code": rc})

        if (task_dir / "requirements.txt").exists():
            log_think("Installing pip dependencies...", AGENT_NAME)
            rc, out = run_pip_install(task_dir)
            log_command(task_dir, "pip install -r requirements.txt", rc, out)

        # Fix for Jest multiple config error
        if test_command and "npm test" in test_command and (task_dir / "jest.config.js").exists():
            log_think("Detected jest.config.js — forcing use of it.", AGENT_NAME)
            test_command = test_command.replace("npm test", "npm test -- --config jest.config.js")

        # ── Build verification (site projects) ────────────────────────
        # For Next.js / React / Vite projects, verify the production build
        # succeeds before running unit tests — a failing build means nothing else matters.
        pkg = task_dir / "package.json"
        is_site_project = False
        if pkg.exists():
            try:
                import json as _json
                pkg_data = _json.loads(pkg.read_text(encoding="utf-8"))
                scripts = pkg_data.get("scripts", {})
                deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
                is_site_project = any(k in deps for k in ("next", "react", "vite", "@sveltejs/kit"))
                has_build_script = "build" in scripts
            except Exception:
                has_build_script = False

            if is_site_project and has_build_script:
                log_think("Site project detected — running production build first...", AGENT_NAME)
                append_build_log(task_dir, "Running: npm run build")
                build_rc, build_out = run_shell_combined(
                    "npm run build", task_dir, timeout=7200,
                    env={"NODE_OPTIONS": "--max-old-space-size=512"},
                )
                log_command(task_dir, "npm run build", build_rc, build_out)

                if build_rc != 0 and has_optional_dependency_issue(build_out):
                    append_build_log(
                        task_dir,
                        "Detected npm optional dependency issue during build. Performing clean reinstall before retrying build.",
                    )
                    write_progress(
                        task_dir,
                        task_id,
                        "testing",
                        "Repairing broken npm install",
                        "Build failed because a native optional dependency is missing; retrying after a clean npm reinstall.",
                        "Cleaning node_modules and package-lock.json, then rerunning npm install...",
                        86.0,
                        subtask_id=100,
                    )
                    reinstall_rc, reinstall_out = run_npm_install(task_dir, retries=1, force_clean_first=True)
                    log_command(task_dir, "npm install --include=optional", reinstall_rc, reinstall_out)
                    if reinstall_rc == 0:
                        append_build_log(task_dir, "Clean npm reinstall succeeded. Retrying production build...")
                        build_rc, build_out = run_shell_combined(
                            "npm run build", task_dir, timeout=7200,
                            env={"NODE_OPTIONS": "--max-old-space-size=512"},
                        )
                        log_command(task_dir, "npm run build (retry after clean reinstall)", build_rc, build_out)
                    else:
                        build_out = (build_out + "\n\n[CLEAN REINSTALL FAILED]\n" + reinstall_out).strip()

                if build_rc != 0:
                    build_summary = summarize_failure_output("npm run build", build_out)
                    test_iteration = state.get("test_iteration", 0)
                    retry_count = test_iteration + 1
                    repeated_count, failure_signature = _track_failure_signature(state, build_summary)
                    escalated = repeated_count >= 2 or retry_count > MAX_TEST_RETRIES

                    log_warn(
                        f"Build FAILED (rc={build_rc}, attempt {retry_count}/{MAX_TEST_RETRIES}). "
                        f"Returning to Coder with {'escalated' if escalated else 'targeted'} repair guidance.",
                        AGENT_NAME,
                    )
                    write_progress(
                        task_dir,
                        task_id,
                        "testing",
                        "Repeated build failure — escalating repair strategy" if escalated else "Build failed — returning to coder",
                        "The same build issue is repeating; coder must replace incompatible libraries/configs or simplify the approach."
                        if escalated
                        else "Production build failed; the coder is being sent back with a targeted diagnosis.",
                        build_summary,
                        88.0,
                        subtask_id=100,
                        metadata={
                            "diagnosis": build_summary,
                            "exit_code": build_rc,
                            "retry": retry_count,
                            "repeated_failure_count": repeated_count,
                            "failure_signature": failure_signature,
                            "escalated": escalated,
                        },
                    )
                    state["status"] = "coding"
                    state["test_errors"] = _build_repair_context(
                        "build",
                        "npm run build",
                        build_summary,
                        build_out,
                        retry_count,
                        repeated_count,
                    )
                    state["test_iteration"] = retry_count
                    write_swarm_state(task_id, state, workspace_dir=task_dir)
                    h = commit_step(
                        task_dir,
                        f"build: retry {retry_count} - {'escalate repair strategy' if escalated else 'return to coder'}",
                    )
                    if h:
                        append_commit_log(task_dir, h, f"build: retry {retry_count}")
                        push_to_remote(task_dir)
                    return {
                        "action": "tested",
                        "task_id": task_id,
                        "passed": False,
                        "reason": "build_failed_retrying",
                    }
                else:
                    log_ok("Production build PASSED.", AGENT_NAME)
                    append_build_log(task_dir, "Build PASSED ✅")

        # ── Run tests ─────────────────────────────────────────────────
        if not test_command:
            # No explicit tests defined — build already verified above
            log_ok("No explicit tests, but build check completed. Advancing to deployment.", AGENT_NAME)
            write_progress(task_dir, task_id, "testing", "Build verified",
                           "No explicit tests defined, but build check completed",
                           "Build check passed", 95.0, subtask_id=100)
            state["status"] = "deploying"
            state["test_errors"] = ""
            state["last_failure_signature"] = ""
            state["repeated_failure_count"] = 0
            write_swarm_state(task_id, state, workspace_dir=task_dir)
            return {"action": "tested", "task_id": task_id, "passed": True}

        write_progress(task_dir, task_id, "testing", "Running tests",
                       f"Executing: {test_command}",
                       "Waiting for test results...", 88.0, subtask_id=100)
        log_think(f"Running tests: `{test_command}` in {task_dir}", AGENT_NAME)
        append_build_log(task_dir, f"Test command: {test_command}")

        rc, output = run_tests(task_dir, test_command, timeout=7200)
        log_command(task_dir, test_command, rc, output)

        # ── Save test results ─────────────────────────────────────────
        test_result = {
            "test_command": test_command,
            "exit_code": rc,
            "passed": rc == 0,
            "output_preview": output[:1000],
            "timestamp": time.time(),
            "iteration": state.get("iterations", 1),
        }

        # Append to test history
        results_file = task_dir / ".test_results.json"
        test_history = []
        if results_file.exists():
            try:
                test_history = json.loads(results_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        test_history.append(test_result)
        results_file.write_text(json.dumps(test_history, indent=2), encoding="utf-8")

        # ── Commit test results ───────────────────────────────────────
        if rc == 0:
            log_ok("Tests PASSED! Advancing to deployment.", AGENT_NAME)
            write_progress(task_dir, task_id, "testing", "Tests passed",
                           "All tests passed — advancing to deployment",
                           output[:500] if output else "", 95.0, subtask_id=100,
                           metadata={"exit_code": rc})
            state["status"] = "deploying"
            state["test_errors"] = ""
            state["last_failure_signature"] = ""
            state["repeated_failure_count"] = 0

            h = commit_step(task_dir, "test: all tests passing ✅")
            if h:
                append_commit_log(task_dir, h, "test: all tests passing")
                push_to_remote(task_dir)
                log_ok(f"Test results committed [{h}] and pushed", AGENT_NAME)

        else:
            limited_out = output[-2000:] if len(output) > 2000 else output
            test_summary = summarize_failure_output(test_command, output)
            test_iteration = state.get("test_iteration", 0)
            retry_count = test_iteration + 1
            repeated_count, failure_signature = _track_failure_signature(state, test_summary)
            escalated = repeated_count >= 2 or retry_count > MAX_TEST_RETRIES

            log_warn(
                f"Tests FAILED (exit code {rc}, attempt {retry_count}/{MAX_TEST_RETRIES}). "
                f"Returning to Coder with {'escalated' if escalated else 'targeted'} repair guidance.",
                AGENT_NAME,
            )
            write_progress(
                task_dir,
                task_id,
                "testing",
                "Repeated test failure — escalating repair strategy" if escalated else "Tests failed — retrying",
                "The same test issue is repeating; coder must replace incompatible dependencies/configs or simplify the failing feature."
                if escalated
                else "Tests failed, returning to the coder agent to fix errors.",
                test_summary,
                90.0,
                subtask_id=100,
                metadata={
                    "diagnosis": test_summary,
                    "exit_code": rc,
                    "retry": retry_count,
                    "repeated_failure_count": repeated_count,
                    "failure_signature": failure_signature,
                    "escalated": escalated,
                },
            )

            state["status"] = "coding"
            state["test_errors"] = _build_repair_context(
                "tests",
                test_command,
                test_summary,
                limited_out,
                retry_count,
                repeated_count,
            )
            state["test_iteration"] = retry_count

            h = commit_step(
                task_dir,
                f"test: retry {retry_count} - {'escalate repair strategy' if escalated else f'exit code {rc}'}",
            )
            if h:
                append_commit_log(task_dir, h, f"test: retry {retry_count} (rc={rc})")
                push_to_remote(task_dir)
                log_ok(f"Failing test results committed [{h}] and pushed", AGENT_NAME)

        write_swarm_state(task_id, state, workspace_dir=task_dir)

        return {"action": "tested", "task_id": task_id, "passed": (rc == 0)}

    except Exception as e:
        log_err(f"Exception during testing: {e}")
        log_err(traceback.format_exc().strip().splitlines()[-1])
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
