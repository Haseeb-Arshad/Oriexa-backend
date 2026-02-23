"""BaseAgent — abstract base class for all Oriexa orchestrator agents."""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from langchain_openai import ChatOpenAI

from app.db.enums import AgentRole
from app.llm.router import ModelTier, get_model

logger = logging.getLogger(__name__)

# Project root: three levels up from this file (app/agents/base.py -> project root)
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Loop detection window size
LOOP_DETECTION_WINDOW = 3


class BaseAgent(ABC):
    """Abstract base class that every orchestrator agent inherits from.

    Provides:
    - LLM access via the tiered model router
    - Prompt loading from prompts/{role}.md
    - Token tracking (prompt + completion)
    - Loop detection based on action hashes
    """

    def __init__(self, role: str, model_tier: str) -> None:
        """Initialise the agent.

        Args:
            role: The agent role value (an ``AgentRole`` enum member value).
            model_tier: The model tier value (a ``ModelTier`` enum member value).
        """
        self.role: str = role
        self.model_tier: str = model_tier

        # Token accumulators
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0

        # Loop detection: rolling window of action hashes
        self._action_hashes: list[str] = []

    # ------------------------------------------------------------------
    # LLM access
    # ------------------------------------------------------------------

    def get_model(self) -> ChatOpenAI:
        """Return the LLM instance for this agent's tier."""
        return get_model(self.model_tier)

    # ------------------------------------------------------------------
    # Prompt loading
    # ------------------------------------------------------------------

    def load_prompt(self) -> str:
        """Load the system prompt from ``prompts/{role}.md`` relative to project root.

        Returns the prompt text, or a sensible fallback if the file is missing.
        """
        prompt_path = PROJECT_ROOT / "prompts" / f"{self.role}.md"
        try:
            return prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "Prompt file not found at %s — using default prompt for role '%s'",
                prompt_path,
                self.role,
            )
            return (
                f"You are a Oriexa {self.role} agent. "
                f"Carry out your assigned responsibilities carefully and thoroughly."
            )
        except OSError as exc:
            logger.error("Failed to read prompt file %s: %s", prompt_path, exc)
            return (
                f"You are a Oriexa {self.role} agent. "
                f"Carry out your assigned responsibilities carefully and thoroughly."
            )

    # ------------------------------------------------------------------
    # Token tracking
    # ------------------------------------------------------------------

    def track_tokens(self, response: Any) -> None:
        """Extract and accumulate token usage from a LangChain AIMessage response.

        Works with responses that carry a ``response_metadata`` dict containing
        ``token_usage`` (OpenAI-style) or ``usage`` (Anthropic-style) blocks.
        """
        metadata = getattr(response, "response_metadata", {}) or {}

        # OpenAI / OpenRouter style
        usage = metadata.get("token_usage") or metadata.get("usage") or {}

        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)

    def get_token_usage(self) -> dict[str, int]:
        """Return accumulated token counts."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }

    # ------------------------------------------------------------------
    # Loop detection
    # ------------------------------------------------------------------

    def record_action(self, action_repr: str) -> None:
        """Record an action hash for loop detection.

        Args:
            action_repr: A string representation of the action taken (e.g. the
                LLM response content or tool-call signature).
        """
        h = hashlib.sha256(action_repr.encode("utf-8")).hexdigest()[:16]
        self._action_hashes.append(h)
        # Keep only the most recent window
        if len(self._action_hashes) > LOOP_DETECTION_WINDOW:
            self._action_hashes = self._action_hashes[-LOOP_DETECTION_WINDOW:]

    def is_stuck(self) -> bool:
        """Return ``True`` if the last N actions are all identical (looping)."""
        if len(self._action_hashes) < LOOP_DETECTION_WINDOW:
            return False
        window = self._action_hashes[-LOOP_DETECTION_WINDOW:]
        return len(set(window)) == 1

    # ------------------------------------------------------------------
    # Abstract run method
    # ------------------------------------------------------------------

    @abstractmethod
    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent's logic on the given task state.

        Args:
            state: The current ``TaskState`` dict.

        Returns:
            A dict of state updates to merge back into ``TaskState``.
        """
        ...
