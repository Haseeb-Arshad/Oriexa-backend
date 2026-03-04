"""ReviewAgent — validates deliverables against task requirements."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.db.enums import AgentRole
from app.llm.router import ModelTier

logger = logging.getLogger(__name__)


class ReviewAgent(BaseAgent):
    """Reviews the deliverable produced by Execution/ComplexTask agents.

    No tools — uses pure LLM reasoning to evaluate the work against the
    original task requirements.

    Returns:
        score (int): 0-100 quality score.
        passed (bool): whether the deliverable meets minimum quality.
        feedback (str): detailed feedback for the execution agent if retry is needed.
    """

    def __init__(self) -> None:
        super().__init__(role=AgentRole.REVIEW.value, model_tier=ModelTier.STRONG.value)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Invoke the LLM to review the deliverable."""
        model = self.get_model()
        system_prompt = self.load_prompt()

        task_data = state.get("task_data", {})
        task_description = json.dumps(task_data, indent=2, default=str)
        plan = state.get("plan", [])
        subtask_results = state.get("subtask_results", [])
        deliverable_content = state.get("deliverable_content", "")
        files_created = state.get("files_created", [])
        files_modified = state.get("files_modified", [])
        commands_executed = state.get("commands_executed", [])
        attempt_count = state.get("attempt_count", 0)

        # Build a comprehensive review context
        plan_summary = ""
        if plan:
            plan_lines = []
            for i, subtask in enumerate(plan):
                plan_lines.append(f"  {i}. {subtask.get('title', 'Untitled')}: {subtask.get('description', '')}")
            plan_summary = "\n".join(plan_lines)

        results_summary = ""
        if subtask_results:
            result_lines = []
            for r in subtask_results:
                result_lines.append(
                    f"  [{r.get('status', '?')}] Subtask {r.get('index', '?')}: "
                    f"{r.get('title', 'Untitled')}\n"
                    f"    Result: {r.get('result', 'N/A')[:500]}\n"
                    f"    Files changed: {r.get('files_changed', [])}"
                )
            results_summary = "\n".join(result_lines)

        review_prompt = (
            "Review the following task deliverable against the original requirements.\n\n"
            f"Task requirements:\n{task_description}\n\n"
        )

        if plan_summary:
            review_prompt += f"Execution plan:\n{plan_summary}\n\n"

        if results_summary:
            review_prompt += f"Subtask execution results:\n{results_summary}\n\n"

        if deliverable_content:
            review_prompt += f"Deliverable summary:\n{deliverable_content}\n\n"

        if files_created:
            review_prompt += f"Files created: {files_created}\n\n"

        if files_modified:
            review_prompt += f"Files modified: {files_modified}\n\n"

        review_prompt += (
            f"This is attempt #{attempt_count + 1}.\n\n"
            "Evaluate the deliverable and return a JSON object with:\n"
            "- score: integer 0-100 (overall quality score)\n"
            "- passed: boolean (true if the deliverable meets the task requirements; "
            "threshold is 70)\n"
            "- feedback: string with detailed feedback. If not passed, explain "
            "specifically what needs to be fixed.\n\n"
            "Consider:\n"
            "1. Does the deliverable address all stated requirements?\n"
            "2. Is the implementation correct and complete?\n"
            "3. Are there any obvious bugs or issues?\n"
            "4. Is the code quality acceptable (if applicable)?\n"
            "5. Are edge cases handled?\n\n"
            "Return ONLY valid JSON, no markdown fences."
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=review_prompt),
        ]

        response = await model.ainvoke(messages)
        self.track_tokens(response)
        self.record_action(response.content)

        # Parse the review result
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
                    "ReviewAgent: failed to parse LLM response as JSON: %s",
                    response.content[:500],
                )
                result = {
                    "score": 50,
                    "passed": False,
                    "feedback": (
                        "Unable to parse review response. The deliverable should be "
                        "re-evaluated. Raw assessment: " + response.content[:1000]
                    ),
                }

        # Normalise and validate
        score = int(result.get("score", 50))
        score = max(0, min(100, score))

        passed = bool(result.get("passed", score >= 70))
        feedback = str(result.get("feedback", ""))

        # Consistency check: if score >= 70 but passed is False (or vice versa),
        # trust the explicit passed field but log the inconsistency
        if score >= 70 and not passed:
            logger.info(
                "ReviewAgent: score=%d but passed=False (respecting explicit flag)",
                score,
            )
        elif score < 70 and passed:
            logger.info(
                "ReviewAgent: score=%d but passed=True (respecting explicit flag)",
                score,
            )

        logger.info(
            "ReviewAgent result: score=%d passed=%s feedback_len=%d",
            score,
            passed,
            len(feedback),
        )

        return {
            "score": score,
            "passed": passed,
            "feedback": feedback,
            "attempt_count": attempt_count + 1,
            **self.get_token_usage(),
        }
