"""PlanningAgent — decomposes a task into ordered subtasks with dependencies."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.base import BaseAgent
from app.db.enums import AgentRole
from app.llm.router import ModelTier
from app.tools import PLANNING_TOOLS

logger = logging.getLogger(__name__)

# Maximum tool-call iterations before forcing a plan
MAX_TOOL_ITERATIONS = 10


class PlanningAgent(BaseAgent):
    """Explores the workspace and creates an execution plan.

    Uses tools (read_file, list_files, analyze_codebase) via a ReAct-style loop
    to understand the codebase, then produces an ordered list of subtasks.

    Returns:
        plan (list[dict]): Each subtask has title, description, depends_on (list[int]).
    """

    def __init__(self, model_tier: str = ModelTier.DEFAULT.value) -> None:
        super().__init__(role=AgentRole.PLANNING.value, model_tier=model_tier)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Invoke the LLM with tool access to create a subtask plan."""
        model = self.get_model()
        system_prompt = self.load_prompt()
        model_with_tools = model.bind_tools(PLANNING_TOOLS)

        task_data = state.get("task_data", {})
        task_description = json.dumps(task_data, indent=2, default=str)
        workspace_path = state.get("workspace_path", "/tmp/oriexa-workspaces/unknown")
        complexity = state.get("complexity", "medium")
        clarification_response = state.get("clarification_response") or ""

        planning_prompt = (
            "You are planning how to complete the following task.\n\n"
            f"Task complexity: {complexity}\n"
            f"Workspace path: {workspace_path}\n\n"
        )
        if clarification_response:
            planning_prompt += f"Poster's clarification response:\n{clarification_response}\n\n"

        planning_prompt += (
            f"Task data:\n{task_description}\n\n"
            "First, explore the workspace using the available tools to understand "
            "the existing codebase structure. Then create a plan.\n\n"
            "When you are ready, return a JSON object with:\n"
            "- plan: array of subtask objects, each with:\n"
            "  - title: short descriptive title\n"
            "  - description: detailed description of what to do\n"
            "  - depends_on: array of 0-based indices of subtasks this depends on\n\n"
            "Return ONLY valid JSON when you are done planning (no markdown fences)."
        )

        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=planning_prompt),
        ]

        # ReAct tool-calling loop
        for iteration in range(MAX_TOOL_ITERATIONS):
            response = await model_with_tools.ainvoke(messages)
            self.track_tokens(response)
            messages.append(response)

            # Check if the model made tool calls
            if not response.tool_calls:
                # No tool calls — model is done; parse the plan from the response
                break

            # Process each tool call
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_id = tool_call["id"]

                # Inject workspace_path for tools that need it
                if "workspace_path" not in tool_args and tool_name in (
                    "read_file",
                    "list_files",
                    "analyze_codebase",
                ):
                    tool_args["workspace_path"] = workspace_path

                # Execute the tool
                tool_fn = _get_tool_by_name(tool_name)
                if tool_fn is not None:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as exc:
                        tool_result = f"[ERROR] Tool execution failed: {exc}"
                        logger.error("PlanningAgent tool error: %s(%s) -> %s", tool_name, tool_args, exc)
                else:
                    tool_result = f"[ERROR] Unknown tool: {tool_name}"

                messages.append(
                    ToolMessage(content=str(tool_result), tool_call_id=tool_id)
                )

            self.record_action(json.dumps([tc["name"] for tc in response.tool_calls]))
            if self.is_stuck():
                logger.warning("PlanningAgent: loop detected at iteration %d, forcing plan generation", iteration)
                # Ask the model to produce the plan without tools
                messages.append(
                    HumanMessage(content=(
                        "You seem to be repeating the same actions. Please produce the final "
                        "plan now as a JSON object. No more tool calls."
                    ))
                )
                response = await model.ainvoke(messages)  # model without tools bound
                self.track_tokens(response)
                messages.append(response)
                break
        else:
            # Hit max iterations — ask for plan without tools
            logger.warning("PlanningAgent: max iterations (%d) reached, forcing plan", MAX_TOOL_ITERATIONS)
            messages.append(
                HumanMessage(content=(
                    "Maximum exploration iterations reached. Please produce the final "
                    "plan now as a JSON object. No more tool calls."
                ))
            )
            response = await model.ainvoke(messages)
            self.track_tokens(response)

        # Parse the plan from the final response
        plan = _parse_plan(response.content)

        logger.info("PlanningAgent: created plan with %d subtasks", len(plan))

        return {
            "plan": plan,
            **self.get_token_usage(),
        }


def _get_tool_by_name(name: str) -> Any | None:
    """Look up a tool function by name."""
    tool_map = {t.name: t for t in PLANNING_TOOLS}
    return tool_map.get(name)


def _parse_plan(content: Any) -> list[dict[str, Any]]:
    """Extract the plan list from LLM response content."""
    
    # If the LLM wrapper already parsed it into a dict or list
    if isinstance(content, dict) and "plan" in content:
        return _validate_plan(content["plan"])
    if isinstance(content, list):
        return _validate_plan(content)

    if not isinstance(content, str):
        content = str(content)

    # Try direct JSON parse
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict) and "plan" in data:
            return _validate_plan(data["plan"])
        if isinstance(data, list):
            return _validate_plan(data)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences
    text = content.strip()
    for marker in ("```json", "```"):
        if marker in text:
            text = text.split(marker, 1)[1]
            text = text.split("```", 1)[0].strip()
            try:
                data = json.loads(text)
                if isinstance(data, dict) and "plan" in data:
                    return _validate_plan(data["plan"])
                if isinstance(data, list):
                    return _validate_plan(data)
            except json.JSONDecodeError:
                pass
            break

    logger.error("PlanningAgent: could not parse plan from response: %s", content[:500])
    # Return a single generic subtask as fallback
    return [
        {
            "title": "Complete task",
            "description": "Execute the full task as described in the requirements.",
            "depends_on": [],
        }
    ]


def _validate_plan(plan: list[Any]) -> list[dict[str, Any]]:
    """Ensure each subtask in the plan has the required fields."""
    validated: list[dict[str, Any]] = []
    for i, item in enumerate(plan):
        if not isinstance(item, dict):
            continue
        validated.append({
            "title": str(item.get("title", f"Subtask {i + 1}")),
            "description": str(item.get("description", "")),
            "depends_on": list(item.get("depends_on", [])),
        })
    return validated if validated else [
        {
            "title": "Complete task",
            "description": "Execute the full task as described in the requirements.",
            "depends_on": [],
        }
    ]
