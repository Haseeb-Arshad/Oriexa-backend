"""TriageAgent — assesses task clarity, complexity, and whether clarification is needed."""

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.db.enums import AgentRole
from app.llm.router import ModelTier

logger = logging.getLogger(__name__)


class TriageAgent(BaseAgent):
    """Analyses an incoming task and produces a triage assessment.

    Returns:
        clarity_score (float): 0.0-1.0 how well-defined the task is.
        complexity (str): "low" | "medium" | "high".
        needs_clarification (bool): whether the poster should be asked for more info.
        reasoning (str): brief explanation of the assessment.
    """

    def __init__(self) -> None:
        super().__init__(role=AgentRole.TRIAGE.value, model_tier=ModelTier.FAST.value)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Invoke the LLM to triage the task."""
        model = self.get_model()
        system_prompt = self.load_prompt()

        task_data = state.get("task_data", {})
        task_description = json.dumps(task_data, indent=2, default=str)

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=(
                "Analyse the following task and return a JSON object with these fields:\n"
                "- clarity_score: float between 0.0 and 1.0\n"
                "- complexity: one of \"low\", \"medium\", \"high\"\n"
                "- needs_clarification: boolean\n"
                "- task_type: one of \"frontend\", \"backend\", \"fullstack\", \"general\"\n"
                "- reasoning: brief explanation\n\n"
                "Return ONLY valid JSON, no markdown fences.\n\n"
                f"Task data:\n{task_description}"
            )),
        ]

        response = await model.ainvoke(messages)
        self.track_tokens(response)
        self.record_action(response.content)

        # Parse structured output from the LLM response
        content = response.content.strip() if isinstance(response.content, str) else str(response.content)

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Attempt to extract JSON from markdown-fenced response or arbitrary text block
            if "```json" in content:
                content = content.split("```json", 1)[1]
                content = content.split("```", 1)[0].strip()
            elif "```" in content:
                content = content.split("```", 1)[1]
                content = content.split("```", 1)[0].strip()
            
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                # Fallback: find the first { ... } block via regex
                match = re.search(r'\{.*\}', content, re.DOTALL)
                if match:
                    try:
                        result = json.loads(match.group(0))
                    except json.JSONDecodeError:
                        logger.error(
                            "TriageAgent: failed to parse ALL fallbacks as JSON: %s",
                            response.content[:500],
                        )
                        result = {
                            "clarity_score": 0.5,
                            "complexity": "medium",
                            "needs_clarification": True,
                            "reasoning": "Unable to parse triage response; defaulting to clarification.",
                        }
                else:
                    logger.error(
                        "TriageAgent: failed to parse LLM response as JSON: %s",
                        response.content[:500],
                    )
                    result = {
                        "clarity_score": 0.5,
                        "complexity": "medium",
                        "needs_clarification": True,
                        "reasoning": "Unable to parse triage response; defaulting to clarification.",
                    }

        # Normalise and validate fields
        clarity_score = float(result.get("clarity_score", 0.5))
        clarity_score = max(0.0, min(1.0, clarity_score))

        complexity = result.get("complexity", "medium")
        if complexity not in ("low", "medium", "high"):
            complexity = "medium"

        needs_clarification = bool(result.get("needs_clarification", clarity_score < 0.6))
        reasoning = str(result.get("reasoning", ""))

        task_type = result.get("task_type", "general")
        if task_type not in ("frontend", "backend", "fullstack", "general"):
            task_type = "general"

        logger.info(
            "TriageAgent result: clarity=%.2f complexity=%s needs_clarification=%s task_type=%s",
            clarity_score,
            complexity,
            needs_clarification,
            task_type,
        )

        return {
            "clarity_score": clarity_score,
            "complexity": complexity,
            "needs_clarification": needs_clarification,
            "task_type": task_type,
            "reasoning": reasoning,
            **self.get_token_usage(),
        }
