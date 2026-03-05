"""
Oriexa Shell Executor Module

Shared shell execution utilities used by all agents in the swarm.
Provides subprocess wrappers with logging, streaming output,
timeout handling, and retry logic.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Generator


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


# ═══════════════════════════════════════════════════════════════════════════
# SPECIALIZED RUNNERS
# ═══════════════════════════════════════════════════════════════════════════

def run_npm_install(task_dir: Path, retries: int = 2) -> tuple[int, str]:
    """
    Run npm install with retry logic.
    Returns (return_code, output).
    """
    # Cap Node.js heap to 512 MB to prevent OOM kills on small droplets
    node_env = {"NODE_OPTIONS": "--max-old-space-size=512"}
    for attempt in range(retries + 1):
        rc, output = run_shell_combined("npm install", task_dir, timeout=7200, env=node_env)
        if rc == 0:
            return rc, output

        if attempt < retries:
            # Clean and retry
            run_shell_combined("rm -rf node_modules package-lock.json", task_dir)
            time.sleep(2)

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
    append_build_log(task_dir, f"{status} $ {cmd}\n{trimmed}")
