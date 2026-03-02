"""Sandbox executor — runs shell commands in an isolated subprocess."""

from __future__ import annotations

import asyncio
import logging
import platform
import time
from dataclasses import dataclass, field

from app.config import settings
from app.sandbox.policy import CommandPolicy, PolicyDecision

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 50_000


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    policy_decision: PolicyDecision | None = field(default=None)


class SandboxExecutor:
    """Executes shell commands with policy checks, timeouts, and output limits."""

    def __init__(self, policy: CommandPolicy | None = None, timeout: int | None = None):
        self.policy = policy or CommandPolicy()
        self.timeout = timeout or settings.SANDBOX_TIMEOUT

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        decision = self.policy.evaluate(command)
        if not decision.allowed:
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"BLOCKED: {decision.reason}",
                timed_out=False,
                duration_ms=0,
                policy_decision=decision,
            )

        effective_timeout = timeout or self.timeout

        # Build restricted environment
        env = self._build_env()

        start = time.monotonic()
        timed_out = False

        try:
            # On Linux, we can set resource limits via preexec_fn
            preexec = None
            if platform.system() == "Linux":
                preexec = self._linux_preexec

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                preexec_fn=preexec,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=effective_timeout
                )
            except asyncio.TimeoutError:
                timed_out = True
                proc.kill()
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=5
                    )
                except asyncio.TimeoutError:
                    stdout_bytes, stderr_bytes = b"", b""

            duration_ms = int((time.monotonic() - start) * 1000)

            stdout = stdout_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
            stderr = stderr_bytes.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

            return ExecutionResult(
                exit_code=proc.returncode or -1 if timed_out else (proc.returncode or 0),
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                duration_ms=duration_ms,
                policy_decision=decision,
            )

        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("Sandbox execution error: %s", exc)
            return ExecutionResult(
                exit_code=-1,
                stdout="",
                stderr=f"Execution error: {exc}",
                timed_out=False,
                duration_ms=duration_ms,
                policy_decision=decision,
            )

    @staticmethod
    def _build_env() -> dict[str, str]:
        """Build a restricted environment for subprocesses."""
        import os
        safe_keys = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "TMPDIR"}
        env = {k: v for k, v in os.environ.items() if k in safe_keys}
        env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        # Pass through GH_TOKEN so gh CLI can authenticate for repo creation
        gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
        if gh_token:
            env["GH_TOKEN"] = gh_token
        return env

    @staticmethod
    def _linux_preexec() -> None:
        """Set resource limits on Linux subprocesses."""
        import resource
        # CPU time: 120 seconds
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        # Memory: 512 MB
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        # File size: 50 MB
        resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
        # Max open files: 256
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
