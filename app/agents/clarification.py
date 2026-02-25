"""ClarificationAgent — posts structured questions to the task poster via the messages API."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.base import BaseAgent
from app.db.enums import AgentRole
from app.llm.router import ModelTier
from app.tools import COMMUNICATION_TOOLS

logger = logging.getLogger(__name__)

# Maximum tool-call iterations
MAX_TOOL_ITERATIONS = 6


class ClarificationAgent(BaseAgent):
    """Analyses the task for ambiguities and posts structured questions to the poster.

    Uses a ReAct loop with COMMUNICATION_TOOLS (post_question, read_task_messages)
    to actually send questions via the messages API so they appear in the UI.

    Returns:
        clarification_needed (bool): Whether a question was posted.
        clarification_message_id (int | None): The message ID of the posted question.
        question_summary (str): A brief description of what was asked.
    """

    def __init__(self) -> None:
        super().__init__(role=AgentRole.CLARIFICATION.value, model_tier=ModelTier.FAST.value)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Invoke the LLM with communication tools to post clarification questions."""
        model = self.get_model()
        system_prompt = self.load_prompt()
        model_with_tools = model.bind_tools(COMMUNICATION_TOOLS)

        task_data = state.get("task_data", {})
        task_id = state.get("oriexa_task_id") or task_data.get("id")
        task_description = json.dumps(task_data, indent=2, default=str)
        triage_reasoning = state.get("triage_reasoning", "No triage reasoning available.")
        clarity_score = state.get("clarity_score", 0.5)

        clarification_prompt = (
            "Analyse the task below and post a clarification question to the poster.\n\n"
            f"Task ID: {task_id}\n"
            f"Triage assessment (clarity score: {clarity_score:.2f}):\n{triage_reasoning}\n\n"
            f"Task data:\n{task_description}\n\n"
            "Instructions:\n"
            "1. Identify the most critical ambiguity or missing requirement.\n"
            "2. Use the post_question tool to send ONE well-structured question.\n"
            "   - For binary choices (yes/no): use question_type='yes_no'\n"
            "   - For picking between 2-4 options: use question_type='multiple_choice' with options list\n"
            "   - For open-ended info: use question_type='text_input'\n"
            "3. The content should be a polite, specific question that references the task.\n"
            "4. After posting, respond with a JSON summary:\n"
            '   {"clarification_needed": true, "question_summary": "brief description of what was asked"}\n\n'
            "If the task is actually clear enough, skip posting and return:\n"
            '   {"clarification_needed": false, "question_summary": "Task is sufficiently clear"}\n'
        )

        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=clarification_prompt),
        ]

        sent_message_id: int | None = None

        # ReAct loop
        for iteration in range(MAX_TOOL_ITERATIONS):
            response = await model_with_tools.ainvoke(messages)
            self.track_tokens(response)
            messages.append(response)

            if not response.tool_calls:
                break

            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_id = tool_call["id"]

                # Inject task_id if not provided
                if "task_id" not in tool_args:
                    tool_args["task_id"] = task_id

                tool_fn = _get_tool_by_name(tool_name)
                if tool_fn is not None:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as exc:
                        tool_result = {"ok": False, "error": str(exc)}
                        logger.error(
                            "ClarificationAgent tool error: %s(%s) -> %s",
                            tool_name, tool_args, exc,
                        )
                else:
                    tool_result = {"ok": False, "error": f"Unknown tool: {tool_name}"}

                # Track the message ID from post_question
                if tool_name == "post_question" and isinstance(tool_result, dict) and tool_result.get("ok"):
                    sent_message_id = tool_result.get("message_id")

                messages.append(
                    ToolMessage(content=json.dumps(tool_result, default=str), tool_call_id=tool_id)
                )

            self.record_action(json.dumps([tc["name"] for tc in response.tool_calls]))
            if self.is_stuck():
                break

        # Parse the final response for summary
        result = _parse_result(response.content if hasattr(response, "content") else "")
        questions = result.get("questions", [])
        question_summary = result.get("question_summary", "Clarification question posted")
        clarification_needed = result.get("clarification_needed", sent_message_id is not None)

        logger.info(
            "ClarificationAgent: needed=%s message_id=%s summary=%s",
            clarification_needed, sent_message_id, question_summary,
        )

        return {
            "questions": questions if questions else [question_summary],
            "clarification_needed": clarification_needed,
            "clarification_message_id": sent_message_id,
            "question_summary": question_summary,
            **self.get_token_usage(),
        }


def _get_tool_by_name(name: str) -> Any | None:
    """Look up a tool function by name."""
    tool_map = {t.name: t for t in COMMUNICATION_TOOLS}
    return tool_map.get(name)


def _parse_result(content: str) -> dict[str, Any]:
    """Extract the result from LLM response."""
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        pass

    # Try code-fence extraction
    text = content.strip()
    for marker in ("```json", "```"):
        if marker in text:
            text = text.split(marker, 1)[1]
            text = text.split("```", 1)[0].strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
            break

    return {"clarification_needed": True, "question_summary": "Clarification question posted"}
