"""Command policy engine — allowlist/blocklist evaluation for shell commands."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


class CommandPolicy:
    """Evaluates shell commands against an allowlist and blocklist."""

    def __init__(
        self,
        allowed_commands: list[str] | None = None,
        blocked_patterns: list[str] | None = None,
    ):
        if allowed_commands is None:
            allowed_commands = [
                c.strip()
                for c in settings.ALLOWED_COMMANDS.split(",")
                if c.strip()
            ]
        if blocked_patterns is None:
            blocked_patterns = [
                p.strip()
                for p in settings.BLOCKED_PATTERNS.split(",")
                if p.strip()
            ]
        self._allowed = set(allowed_commands)
        self._blocked = [re.compile(re.escape(p)) for p in blocked_patterns]
        # Dangerous shell patterns that bypass simple command extraction
        self._dangerous_patterns = [
            re.compile(r"curl\s.*\|\s*(?:ba)?sh"),  # curl | sh
            re.compile(r"wget\s.*\|\s*(?:ba)?sh"),  # wget | sh
            re.compile(r">\s*/etc/"),                # write to /etc
            re.compile(r">\s*/dev/"),                # write to /dev
            re.compile(r"\brm\s+-rf\s+/\s*$"),       # rm -rf /
            re.compile(r"\brm\s+-rf\s+/[^/]"),       # rm -rf /anything
        ]

    def evaluate(self, command: str) -> PolicyDecision:
        command = command.strip()
        if not command:
            return PolicyDecision(allowed=False, reason="Empty command")

        # Check blocked patterns first
        for pattern in self._blocked:
            if pattern.search(command):
                return PolicyDecision(
                    allowed=False,
                    reason=f"Command matches blocked pattern: {pattern.pattern}",
                )

        # Check dangerous shell patterns
        for pattern in self._dangerous_patterns:
            if pattern.search(command):
                return PolicyDecision(
                    allowed=False,
                    reason=f"Command matches dangerous pattern: {pattern.pattern}",
                )

        # Extract base command (first token)
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        if not tokens:
            return PolicyDecision(allowed=False, reason="Could not parse command")

        base_cmd = tokens[0].split("/")[-1]  # handle /usr/bin/python -> python

        # Check for piped commands — each must be allowed
        if "|" in command:
            parts = command.split("|")
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                try:
                    part_tokens = shlex.split(part)
                except ValueError:
                    part_tokens = part.split()
                if part_tokens:
                    part_cmd = part_tokens[0].split("/")[-1]
                    if part_cmd not in self._allowed:
                        return PolicyDecision(
                            allowed=False,
                            reason=f"Piped command '{part_cmd}' is not in allowlist",
                        )

        if base_cmd not in self._allowed:
            return PolicyDecision(
                allowed=False,
                reason=f"Command '{base_cmd}' is not in allowlist. Allowed: {', '.join(sorted(self._allowed))}",
            )

        return PolicyDecision(allowed=True, reason="Command allowed")
