"""ClarificationAgent — generates targeted clarification questions for the task poster."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.db.enums import AgentRole
from app.llm.router import ModelTier

logger = logging.getLogger(__name__)


class ClarificationAgent(BaseAgent):
    """Generates 2-4 specific clarification questions based on the task description
    and the triage assessment.

    Returns:
        questions (list[str]): The clarification questions.
        message (str): A formatted message ready to send to the poster.
    """

    def __init__(self) -> None:
        super().__init__(role=AgentRole.CLARIFICATION.value, model_tier=ModelTier.FAST.value)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Invoke the LLM to produce clarification questions."""
        model = self.get_model()
        system_prompt = self.load_prompt()

        task_data = state.get("task_data", {})
        task_description = json.dumps(task_data, indent=2, default=str)
        triage_reasoning = state.get("triage_reasoning", "No triage reasoning available.")
        clarity_score = state.get("clarity_score", 0.5)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=(
                "Based on the task below and the triage assessment, generate 2-4 specific "
                "clarification questions that will help us fully understand the requirements.\n\n"
                "Return a JSON object with:\n"
                "- questions: array of 2-4 question strings\n"
                "- message: a polite formatted message to the task poster that includes the questions\n\n"
                "Return ONLY valid JSON, no markdown fences.\n\n"
                f"Triage assessment (clarity score: {clarity_score:.2f}):\n{triage_reasoning}\n\n"
                f"Task data:\n{task_description}"
            )),
        ]

        response = await model.ainvoke(messages)
        self.track_tokens(response)
        self.record_action(response.content)

        # Parse structured output
        try:
            result = json.loads(response.content.strip())
        except json.JSONDecodeError:
            content = response.content.strip()
            if "```json" in content:
                content = content.split("```json", 1)[1]
                content = content.split("```", 1)[0].strip()
            elif "```" in content:
                content = content.split("```", 1)[1]
                content = content.split("```", 1)[0].strip()
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                logger.error(
                    "ClarificationAgent: failed to parse LLM response as JSON: %s",
                    response.content[:500],
                )
                result = {
                    "questions": [
                        "Could you provide more details about the expected deliverables?",
                        "Are there any specific technical requirements or constraints?",
                        "What is the preferred format for the final output?",
                    ],
                    "message": (
                        "Hi! I'd like to clarify a few things before starting:\n\n"
                        "1. Could you provide more details about the expected deliverables?\n"
                        "2. Are there any specific technical requirements or constraints?\n"
                        "3. What is the preferred format for the final output?\n\n"
                        "Thank you!"
                    ),
                }

        questions = result.get("questions", [])
        if not isinstance(questions, list) or len(questions) == 0:
            questions = ["Could you clarify the task requirements in more detail?"]

        # Clamp to 2-4 questions
        if len(questions) < 2:
            questions.append("Are there any additional constraints or preferences?")
        if len(questions) > 4:
            questions = questions[:4]

        message = result.get("message", "")
        if not message:
            # Build a formatted message from the questions
            numbered = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
            message = (
                "Hi! Before I begin working on this task, I have a few questions "
                "to make sure I deliver exactly what you need:\n\n"
                f"{numbered}\n\n"
                "Looking forward to your response!"
            )

        logger.info(
            "ClarificationAgent: generated %d questions",
            len(questions),
        )

        return {
            "questions": questions,
            "message": message,
            **self.get_token_usage(),
        }
