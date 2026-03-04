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

# Maximum tool-call iterations (enough for 3 questions + summary)
MAX_TOOL_ITERATIONS = 8


class ClarificationAgent(BaseAgent):
    """Analyses the task for ambiguities and posts structured questions to the poster.

    Uses a ReAct loop with COMMUNICATION_TOOLS (post_question, read_task_messages)
    to actually send questions via the messages API so they appear in the UI.

    Supports posting 1-3 questions per invocation.  The *last* posted
    message_id is tracked for wait_for_response polling, plus all IDs are
    stored in ``clarification_message_ids``.

    Returns:
        clarification_needed (bool): Whether question(s) were posted.
        clarification_message_id (int | None): Last posted message ID (for polling).
        clarification_message_ids (list[int]): All posted message IDs.
        question_summary (str): Brief description of what was asked.
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
            "Analyse the task below. Identify 1-3 critical ambiguities and post "
            "structured questions IMMEDIATELY using the post_question tool.\n\n"
            "RULES:\n"
            "- DO NOT send any text before calling post_question. Your FIRST action must be a tool call.\n"
            "- NO greetings, NO introductions, NO preambles.\n"
            "- Call post_question once per question (up to 3 questions).\n"
            "- Prefer multiple_choice and yes_no over text_input — they're faster to answer.\n"
            "- Each question content must be a direct, specific question.\n\n"
            f"Task ID: {task_id}\n"
            f"Triage assessment (clarity score: {clarity_score:.2f}):\n{triage_reasoning}\n\n"
            f"Task data:\n{task_description}\n\n"
            "After posting all questions, return a JSON summary:\n"
            '{"clarification_needed": true, "question_summary": "brief description of all questions asked"}\n\n'
            "If the task is actually clear enough, skip posting and return:\n"
            '{"clarification_needed": false, "question_summary": "Task is sufficiently clear"}\n'
        )

        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=clarification_prompt),
        ]

        sent_message_ids: list[int] = []

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

                # Track all message IDs from post_question
                if tool_name == "post_question" and isinstance(tool_result, dict) and tool_result.get("ok"):
                    mid = tool_result.get("message_id")
                    if mid is not None:
                        sent_message_ids.append(mid)
                        logger.info("ClarificationAgent: posted question message_id=%d", mid)

                messages.append(
                    ToolMessage(content=json.dumps(tool_result, default=str), tool_call_id=tool_id)
                )

            self.record_action(json.dumps([tc["name"] for tc in response.tool_calls]))
            if self.is_stuck():
                break

        # Parse the final response for summary
        result = _parse_result(response.content if hasattr(response, "content") else "")
        question_summary = result.get("question_summary", "Clarification questions posted")
        clarification_needed = result.get("clarification_needed", len(sent_message_ids) > 0)

        # Use the last message_id for polling (user responds to the last question)
        last_message_id = sent_message_ids[-1] if sent_message_ids else None

        logger.info(
            "ClarificationAgent: needed=%s message_ids=%s summary=%s",
            clarification_needed, sent_message_ids, question_summary,
        )

        return {
            "questions": [question_summary],
            "clarification_needed": clarification_needed,
            "clarification_message_id": last_message_id,
            "clarification_message_ids": sent_message_ids,
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

    return {"clarification_needed": True, "question_summary": "Clarification questions posted"}
