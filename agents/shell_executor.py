"""
Oriexa Shell Executor Module

Shared shell execution utilities used by all agents in the swarm.
Provides subprocess wrappers with logging, streaming output,
timeout handling, and retry logic.
"""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Generator


OPTIONAL_DEPENDENCY_MARKERS = (
    "cannot find native binding",
    "npm has a bug related to optional dependencies",
    "optional dependencies",
    "@tailwindcss/oxide",
    "@tailwindcss/oxide-linux",
    "@rollup/rollup-",
)


# ═══════════════════════════════════════════════════════════════════════════
# CORE SHELL EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

def run_shell(
    cmd: str | list[str],
    cwd: Path,
    timeout: int = 7200,
    shell: bool = True,
    env: dict | None = None,
) -> tuple[int, str, str]:
    """
    Execute a shell command and capture output.
    
    Args:
        cmd: Command string or list
        cwd: Working directory
        timeout: Max seconds before killing
        shell: Whether to use shell=True
        env: Optional environment variables (merged with os.environ)
    
    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    run_env = {**os.environ}
    if env:
        run_env.update(env)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            encoding="utf-8",
            errors="replace",
            env=run_env,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def run_shell_combined(
    cmd: str | list[str],
    cwd: Path,
    timeout: int = 7200,
    shell: bool = True,
    env: dict | None = None,
) -> tuple[int, str]:
    """
    Execute a shell command and return combined stdout+stderr.
    Convenience wrapper around run_shell.
    """
    rc, stdout, stderr = run_shell(cmd, cwd, timeout, shell, env)
    combined = (stdout + "\n" + stderr).strip()
    return rc, combined


def stream_shell(
    cmd: str,
    cwd: Path,
    timeout: int = 7200,
) -> Generator[str, None, int]:
    """
    Execute a shell command and yield stdout/stderr lines in real-time.
    Useful for long-running processes like npm install, npm run build.
    
    Yields each line of output as it arrives.
    Returns the exit code after the process completes.
    
    Usage:
        gen = stream_shell("npm install", task_dir)
        for line in gen:
            print(line)
        # After iteration, gen.value has the exit code (or use try/return)
    """
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        start = time.monotonic()
        for line in proc.stdout:
            if time.monotonic() - start > timeout:
                proc.kill()
                yield f"[TIMEOUT] Process killed after {timeout}s"
                return -1
            yield line.rstrip()

        proc.wait(timeout=10)
        return proc.returncode

    except Exception as e:
        yield f"[ERROR] {e}"
        return -1


def has_optional_dependency_issue(output: str | None) -> bool:
    lowered = (output or "").lower()
    return any(marker in lowered for marker in OPTIONAL_DEPENDENCY_MARKERS)


def cleanup_npm_artifacts(task_dir: Path) -> None:
    for directory in ("node_modules", ".next"):
        path = task_dir / directory
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    for filename in ("package-lock.json", ".package-lock.json"):
        path = task_dir / filename
        if path.exists():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# SPECIALIZED RUNNERS
# ═══════════════════════════════════════════════════════════════════════════

def run_npm_install(
    task_dir: Path,
    retries: int = 2,
    *,
    force_clean_first: bool = False,
) -> tuple[int, str]:
    """
    Run npm install with retry logic.
    Returns (return_code, output).
    """
    def _read_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _extract_major(version_spec: str | None) -> int | None:
        if not version_spec:
            return None
        match = re.search(r"(\d+)", str(version_spec))
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _npm_health_issues() -> list[str]:
        pkg = task_dir / "package.json"
        if not pkg.exists():
            return []

        pkg_data = _read_json(pkg)
        deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
        issues: list[str] = []

        react_major = _extract_major(deps.get("react"))
        react_dom_major = _extract_major(deps.get("react-dom"))
        if react_major and react_dom_major and react_major != react_dom_major:
            issues.append(
                f"package.json requests mismatched React majors: react={deps.get('react')} react-dom={deps.get('react-dom')}"
            )

        if "next" not in deps:
            return issues

        required_paths = [
            task_dir / "node_modules" / "next" / "package.json",
            task_dir / "node_modules" / "react" / "package.json",
            task_dir / "node_modules" / "react" / "index.js",
            task_dir / "node_modules" / "react-dom" / "package.json",
            task_dir / "node_modules" / "react-dom" / "index.js",
            task_dir / "node_modules" / "next" / "dist" / "compiled" / "@opentelemetry" / "api" / "package.json",
            task_dir / "node_modules" / "next" / "dist" / "compiled" / "@napi-rs" / "triples" / "package.json",
        ]

        for path in required_paths:
            if not path.exists():
                issues.append(f"missing required Next.js runtime artifact: {path.relative_to(task_dir)}")

        return issues

    # Cap Node.js heap to 512 MB to prevent OOM kills on small droplets
    node_env = {"NODE_OPTIONS": "--max-old-space-size=512"}
    force_clean = force_clean_first
    for attempt in range(retries + 1):
        if force_clean:
            cleanup_npm_artifacts(task_dir)
            time.sleep(1)

        install_cmd = "npm install --include=optional"
        rc, output = run_shell_combined(install_cmd, task_dir, timeout=7200, env=node_env)
        optional_dep_issue = has_optional_dependency_issue(output)
        if rc == 0:
            issues = _npm_health_issues()
            if not issues and not optional_dep_issue:
                return rc, output
            if optional_dep_issue:
                issues.append(
                    "npm reported a missing native optional dependency. A clean reinstall is required."
                )
            output = output + "\n\n[NPM HEALTH CHECK FAILED]\n" + "\n".join(f"- {issue}" for issue in issues)

        if attempt < retries:
            force_clean = True
            time.sleep(2)
            continue

        if rc == 0:
            return rc, output

    return rc, output


def run_pip_install(task_dir: Path, requirements: str = "requirements.txt") -> tuple[int, str]:
    """
    Run pip install for a requirements file.
    Returns (return_code, output).
    """
    req_file = task_dir / requirements
    if not req_file.exists():
        return 0, "No requirements file found, skipping."

    return run_shell_combined(f"pip install -r {requirements}", task_dir, timeout=7200)


def run_npx_create(
    template: str,
    task_dir: Path,
    args: list[str] | None = None,
) -> tuple[int, str]:
    """
    Scaffold a project using npx create-* commands.
    
    Args:
        template: Template name (e.g. 'next-app', 'vite', 'react-app')
        task_dir: Directory to scaffold into
        args: Additional arguments
    
    Examples:
        run_npx_create("next-app", task_dir, ["--typescript", "--eslint"])
        run_npx_create("vite", task_dir, ["--template", "react-ts"])
    """
    task_dir.mkdir(parents=True, exist_ok=True)

    extra = " ".join(args) if args else ""
    cmd = f"npx -y create-{template}@latest ./ {extra} --yes"

    return run_shell_combined(cmd, task_dir, timeout=7200)


def run_tests(
    task_dir: Path,
    test_command: str,
    timeout: int = 7200,
) -> tuple[int, str]:
    """
    Run the test command for a task.
    Returns (return_code, output).
    Output is trimmed to last 3000 chars to avoid massive payloads.
    """
    rc, output = run_shell_combined(test_command, task_dir, timeout=timeout)

    # Trim output to avoid massive state files
    if len(output) > 3000:
        output = f"... (trimmed {len(output) - 3000} chars) ...\n" + output[-3000:]

    return rc, output


def summarize_failure_output(command: str, output: str) -> str:
    """Return a short, actionable diagnosis for a failed shell command."""
    text = (output or "").replace("\r\n", "\n")
    compact_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lowered = text.lower()

    def _match(pattern: str) -> re.Match[str] | None:
        return re.search(pattern, text, re.IGNORECASE | re.DOTALL)

    def _line_containing(snippet: str) -> str | None:
        snippet_lower = snippet.lower()
        for line in compact_lines:
            if snippet_lower in line.lower():
                return line
        return None

    if "eresolve unable to resolve dependency tree" in lowered:
        found = _line_containing("found:")
        missing = _line_containing("could not resolve dependency:")
        parts = ["Dependency resolution failed during npm install."]
        if found:
            parts.append(found)
        if missing:
            parts.append(missing)
        parts.append("Align the package versions in package.json before retrying.")
        return " ".join(parts)

    if "unsupported engine" in lowered or "ebadengine" in lowered:
        package = _line_containing("package:")
        required = _line_containing("required:")
        current = _line_containing("current:")
        parts = ["Node runtime is incompatible with one or more installed packages."]
        if package:
            parts.append(package)
        if required:
            parts.append(required)
        if current:
            parts.append(current)
        parts.append("Downgrade or replace the package, or align the runtime version before retrying.")
        return " ".join(parts)

    if "webpack is configured while turbopack is not" in lowered:
        return (
            "Build is running with Turbopack while the project still has webpack-specific config. "
            "Remove the `--turbopack` build flag, add matching Turbopack config, or fall back to the stable webpack build."
        )

    if "lightningcss/node/index.js" in lowered:
        return (
            "Lightning CSS failed to load during the Next.js build. "
            "This usually points to a Node or Turbopack toolchain mismatch; align dependency versions or use the stable non-Turbopack build path."
        )

    if has_optional_dependency_issue(text):
        return (
            "npm optional dependency recovery is required because a native package was missing at runtime. "
            "Delete node_modules and package-lock.json, rerun npm install, and retry the build."
        )

    if "turbopack build failed" in lowered and "module not found" in lowered:
        return (
            "Turbopack failed on a missing module or incompatible toolchain. "
            "Fix or remove the import, or switch the build back to the stable webpack path before retrying."
        )

    module_match = _match(r"Cannot find module ['\"]([^'\"]+)['\"]")
    if module_match:
        missing_module = module_match.group(1)
        importer = _line_containing("Require stack:")
        parts = [f"Missing dependency or import: `{missing_module}`."]
        if importer:
            parts.append(importer)
        parts.append("Add the package or remove the broken import, then rebuild.")
        return " ".join(parts)

    package_match = _match(r"Module not found:.*?[\"']([^\"']+)[\"']")
    if package_match:
        missing_package = package_match.group(1)
        return (
            f"Bundler could not resolve `{missing_package}`. "
            "Install it or fix the import path before retrying."
        )

    file_match = _match(r"(\.?/?[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)[(:]\d+")
    if file_match:
        file_path = file_match.group(1).lstrip("./")
        error_line = next(
            (
                line
                for line in compact_lines
                if "error" in line.lower() or "failed" in line.lower()
            ),
            None,
        )
        if error_line:
            return f"Build error in `{file_path}`. {error_line}"
        return f"Build or test output points to `{file_path}` as the likely source of failure."

    if "next: not found" in lowered:
        return (
            "The `next` binary is unavailable, which usually means `npm install` did not complete "
            "or Next.js is missing from dependencies."
        )

    if "command timed out" in lowered:
        return f"`{command}` timed out. The agent should inspect the hanging step before retrying."

    priority_markers = (
        "npm ERR!",
        "Error:",
        "ERROR:",
        "Failed to compile.",
        "Build failed",
        "Tests failed",
    )
    for marker in priority_markers:
        matched = _line_containing(marker)
        if matched:
            return matched

    if compact_lines:
        return compact_lines[-1]

    return f"`{command}` failed without any captured diagnostic output."


# ═══════════════════════════════════════════════════════════════════════════
# BUILD LOG
# ═══════════════════════════════════════════════════════════════════════════

def append_build_log(task_dir: Path, entry: str):
    """Append an entry to the task's build log."""
    log_file = task_dir / ".build_log"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {entry}\n")


def log_command(task_dir: Path, cmd: str, rc: int, output: str):
    """Log a command execution to the build log."""
    status = "OK" if rc == 0 else f"FAIL(rc={rc})"
    trimmed = output[:500] if len(output) > 500 else output
    log_entry = f"{status} $ {cmd}\n{trimmed}"
    if rc != 0:
        log_entry += f"\nDiagnosis: {summarize_failure_output(cmd, output)}"
    append_build_log(task_dir, log_entry)
