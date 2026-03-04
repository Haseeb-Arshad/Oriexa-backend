"""ResearchAgent — lightweight specialist for research, code review, and test generation."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.db.enums import AgentRole
from app.llm.router import ModelTier

logger = logging.getLogger(__name__)

# Specialist system prompts
SPECIALIST_PROMPTS = {
    "research": (
        "You are a research specialist. Analyse the provided context and query. "
        "Identify relevant patterns, suggest approaches, and provide actionable insights. "
        "Be concise and specific. Focus on what will help the developer make decisions."
    ),
    "code_review": (
        "You are a code review specialist. Review the provided code for:\n"
        "- Correctness and potential bugs\n"
        "- Security vulnerabilities (injection, auth issues, data exposure)\n"
        "- Performance concerns\n"
        "- Code quality and maintainability\n"
        "Be specific with file/line references. Prioritize by severity."
    ),
    "test_generation": (
        "You are a test strategy specialist. Given the code and requirements, suggest:\n"
        "- Key test cases (happy path, error cases, edge cases)\n"
        "- Test structure and organization\n"
        "- Mocking strategies for external dependencies\n"
        "Be specific with test names, inputs, and expected outputs."
    ),
}


class ResearchAgent(BaseAgent):
    """Lightweight specialist agent that runs a single LLM call for focused analysis.

    Used by the consult_specialist tool to provide quick expert input without
    creating a full task on the platform.
    """

    def __init__(self, specialist_type: str = "research") -> None:
        super().__init__(role=AgentRole.PLANNING.value, model_tier=ModelTier.FAST.value)
        self.specialist_type = specialist_type

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run a single LLM call with specialist context."""
        model = self.get_model()

        system_prompt = SPECIALIST_PROMPTS.get(
            self.specialist_type,
            SPECIALIST_PROMPTS["research"],
        )

        query = state.get("query", "")
        context = state.get("context", "")

        user_prompt = f"Query: {query}\n\nContext:\n{context}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        response = await model.ainvoke(messages)
        self.track_tokens(response)

        analysis = response.content if hasattr(response, "content") else str(response)

        logger.info(
            "ResearchAgent(%s): produced %d chars of analysis",
            self.specialist_type, len(analysis),
        )

        return {
            "analysis": analysis,
            **self.get_token_usage(),
        }
