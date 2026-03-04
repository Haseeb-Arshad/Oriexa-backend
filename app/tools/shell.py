"""Shell execution tools — runs sandboxed commands in a task workspace."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any

from langchain_core.tools import tool

from app.sandbox.executor import SandboxExecutor
from app.sandbox.policy import CommandPolicy

logger = logging.getLogger(__name__)

_executor: SandboxExecutor | None = None


def _get_executor() -> SandboxExecutor:
    """Lazy-initialise a shared SandboxExecutor singleton."""
    global _executor
    if _executor is None:
        _executor = SandboxExecutor(policy=CommandPolicy())
    return _executor


@tool
async def execute_command(
    command: Annotated[str, "The shell command to execute"],
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    timeout: Annotated[int | None, "Optional timeout in seconds (default: sandbox default)"] = None,
) -> str:
    """Execute a shell command inside the sandboxed task workspace.

    The command is validated against the policy allowlist before execution.
    Blocked or dangerous commands are rejected immediately.
    Output is truncated to 50 000 characters per stream.

    Returns a structured summary including exit code, stdout, stderr,
    and whether the command timed out.
    """
    executor = _get_executor()

    logger.info(
        "execute_command: command=%r cwd=%s timeout=%s",
        command, workspace_path, timeout,
    )

    result = await executor.execute(
        command=command,
        cwd=workspace_path,
        timeout=timeout,
    )

    # Build a human-/LLM-readable summary
    parts: list[str] = []

    if result.timed_out:
        parts.append(f"[TIMED OUT after {result.duration_ms}ms]")

    if result.policy_decision and not result.policy_decision.allowed:
        parts.append(f"[POLICY BLOCKED] {result.policy_decision.reason}")
        return "\n".join(parts)

    parts.append(f"Exit code: {result.exit_code}")
    parts.append(f"Duration: {result.duration_ms}ms")

    if result.stdout.strip():
        stdout_preview = result.stdout.strip()
        if len(stdout_preview) > 8000:
            stdout_preview = stdout_preview[:4000] + "\n... [truncated] ...\n" + stdout_preview[-4000:]
        parts.append(f"--- stdout ---\n{stdout_preview}")

    if result.stderr.strip():
        stderr_preview = result.stderr.strip()
        if len(stderr_preview) > 4000:
            stderr_preview = stderr_preview[:2000] + "\n... [truncated] ...\n" + stderr_preview[-2000:]
        parts.append(f"--- stderr ---\n{stderr_preview}")

    if not result.stdout.strip() and not result.stderr.strip():
        parts.append("(no output)")

    return "\n".join(parts)


# Maximum parallel commands
MAX_PARALLEL = 4


@tool
async def execute_parallel(
    commands: Annotated[
        list[dict[str, str]],
        'List of commands to run concurrently. Each item: {"command": "...", "description": "..."}',
    ],
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    timeout: Annotated[int, "Timeout in seconds for each command"] = 60,
) -> dict[str, Any]:
    """Run multiple independent shell commands concurrently via asyncio.gather.

    Use this when you have 2-4 independent tasks (e.g. installing deps, creating
    directories, running separate test suites) that don't depend on each other.
    Maximum 4 parallel commands.

    Returns {results: [{command, description, exit_code, stdout, stderr, duration_ms}]}.
    """
    if not commands:
        return {"results": [], "error": "No commands provided."}

    if len(commands) > MAX_PARALLEL:
        commands = commands[:MAX_PARALLEL]

    executor = _get_executor()

    async def _run_one(cmd_spec: dict[str, str]) -> dict[str, Any]:
        command = cmd_spec.get("command", "")
        description = cmd_spec.get("description", command)

        if not command.strip():
            return {
                "command": command,
                "description": description,
                "exit_code": -1,
                "stdout": "",
                "stderr": "Empty command",
                "duration_ms": 0,
            }

        start = time.monotonic()
        result = await executor.execute(
            command=command,
            cwd=workspace_path,
            timeout=timeout,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        if result.policy_decision and not result.policy_decision.allowed:
            return {
                "command": command,
                "description": description,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"[POLICY BLOCKED] {result.policy_decision.reason}",
                "duration_ms": duration_ms,
            }

        stdout = result.stdout.strip()
        if len(stdout) > 4000:
            stdout = stdout[:2000] + "\n... [truncated] ...\n" + stdout[-2000:]
        stderr = result.stderr.strip()
        if len(stderr) > 2000:
            stderr = stderr[:1000] + "\n... [truncated] ...\n" + stderr[-1000:]

        return {
            "command": command,
            "description": description,
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "timed_out": result.timed_out,
        }

    logger.info(
        "execute_parallel: running %d commands in %s",
        len(commands), workspace_path,
    )

    results = await asyncio.gather(*[_run_one(c) for c in commands])

    return {"results": list(results)}
