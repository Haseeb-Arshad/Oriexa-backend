"""ExecutionAgent — executes subtasks using a ReAct tool-calling loop."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agents.base import BaseAgent
from app.db.enums import AgentRole
from app.llm.router import ModelTier
from app.tools import EXECUTION_TOOLS

logger = logging.getLogger(__name__)

# Maximum ReAct iterations per run
MAX_ITERATIONS = 12


class ExecutionAgent(BaseAgent):
    """Executes planned subtasks by running commands and writing files.

    Uses a ReAct-style loop with tools: execute_command, read_file, write_file,
    list_files. Iterates through each subtask in the plan, executing them
    in dependency order.

    Returns:
        subtask_results (list[dict]): Result of each subtask execution.
        files_created (list[str]): New files created during execution.
        files_modified (list[str]): Existing files modified during execution.
        commands_executed (list[dict]): Commands that were run.
        deliverable_summary (str): High-quality summary of what was built.
    """

    def __init__(self, model_tier: str = ModelTier.DEFAULT.value) -> None:
        super().__init__(role=AgentRole.EXECUTION.value, model_tier=model_tier)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute all subtasks via a ReAct tool-calling loop."""
        model = self.get_model()
        system_prompt = self.load_prompt()
        model_with_tools = model.bind_tools(EXECUTION_TOOLS)

        task_data = state.get("task_data", {})
        plan = state.get("plan", [])
        workspace_path = state.get("workspace_path", "/tmp/oriexa-workspaces/unknown")
        existing_results = state.get("subtask_results", [])
        review_feedback = state.get("review_feedback", "")

        # Build a summary of the execution plan
        plan_summary = _format_plan(plan)
        completed_summary = _format_completed(existing_results)

        execution_prompt = (
            "You are executing a task. Work through each subtask in the plan using "
            "the available tools.\n\n"
            f"Workspace path: {workspace_path}\n\n"
            f"Task data:\n{json.dumps(task_data, indent=2, default=str)}\n\n"
            f"Execution plan:\n{plan_summary}\n"
        )

        # Inject skills based on task type (mandatory frontend-design + context-aware extras)
        from app.orchestrator.skills import skill_resolver
        task_type = state.get("task_type", "general")
        skill_injection = skill_resolver.resolve(task_type)
        if skill_injection:
            execution_prompt += skill_injection

        if completed_summary:
            execution_prompt += f"\nPreviously completed subtasks:\n{completed_summary}\n"

        if review_feedback:
            execution_prompt += (
                f"\nPrevious review feedback (address these issues):\n{review_feedback}\n"
            )

        execution_prompt += (
            "\nExecute each subtask in order. As you work, if the user sends you a live message asking for an update or giving new instructions, you MUST reply back occasionally using the `send_chat_message` tool. You can also use it to proactively provide progress updates.\n\n"
            "After completing all subtasks, "
            "return a JSON object with:\n"
            "- subtask_results: array of {index, title, status, result, files_changed}\n"
            "- deliverable_summary: a detailed, high-quality summary of what was built, "
            "key architecture decisions, how to run/use the project, and any important notes. "
            "Do NOT paste raw source code into this field — the code is already saved to the workspace files. "
            "Focus on explaining the implementation, structure, and how everything fits together.\n"
            "- files_created: array of file paths created\n"
            "- files_modified: array of file paths modified\n\n"
            "Return ONLY valid JSON when you are done (no markdown fences)."
        )

        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=execution_prompt),
        ]

        files_created: list[str] = []
        files_modified: list[str] = []
        commands_executed: list[dict[str, Any]] = []

        # ReAct loop
        last_processed_msg_index = 0

        for iteration in range(MAX_ITERATIONS):
            # Check for new live messages from the user since last iteration
            live_msgs = state.get("user_live_messages", [])
            if len(live_msgs) > last_processed_msg_index:
                new_msgs = live_msgs[last_processed_msg_index:]
                for msg in new_msgs:
                    messages.append(HumanMessage(content=(
                        f"[SYSTEM ALERT: The user just sent this message during your execution: '{msg['content']}']\n"
                        "Please acknowledge this message or adjust your actions accordingly."
                    )))
                last_processed_msg_index = len(live_msgs)

            response = await model_with_tools.ainvoke(messages)
            self.track_tokens(response)
            messages.append(response)

            # No tool calls — the agent is done
            if not response.tool_calls:
                break

            # Process tool calls
            for tool_call in response.tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                tool_id = tool_call["id"]

                # Inject workspace_path where needed
                if "workspace_path" not in tool_args and tool_name in (
                    "read_file",
                    "write_file",
                    "list_files",
                    "execute_command",
                    "lint_code",
                ):
                    tool_args["workspace_path"] = workspace_path

                # Inject execution_id/oriexa_task_id where needed
                if tool_name == "send_chat_message":
                    tool_args["oriexa_task_id"] = state.get("oriexa_task_id")

                # Execute the tool
                tool_fn = _get_tool_by_name(tool_name)
                if tool_fn is not None:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as exc:
                        tool_result = f"[ERROR] Tool execution failed: {exc}"
                        logger.error(
                            "ExecutionAgent tool error: %s(%s) -> %s",
                            tool_name, tool_args, exc,
                        )
                else:
                    tool_result = f"[ERROR] Unknown tool: {tool_name}"

                messages.append(
                    ToolMessage(content=str(tool_result), tool_call_id=tool_id)
                )

                # Track side effects
                if tool_name == "write_file":
                    file_path = tool_args.get("file_path", "")
                    if "[OK]" in str(tool_result):
                        if file_path not in files_created and file_path not in files_modified:
                            files_created.append(file_path)
                elif tool_name == "execute_command":
                    commands_executed.append({
                        "command": tool_args.get("command", ""),
                        "result_preview": str(tool_result)[:500],
                    })

            # Loop detection
            self.record_action(json.dumps([tc["name"] for tc in response.tool_calls]))
            if self.is_stuck():
                logger.warning(
                    "ExecutionAgent: loop detected at iteration %d, forcing completion",
                    iteration,
                )
                messages.append(
                    HumanMessage(content=(
                        "You appear to be repeating the same actions. Please wrap up and "
                        "return the final JSON result now. No more tool calls."
                    ))
                )
                response = await model.ainvoke(messages)
                self.track_tokens(response)
                messages.append(response)
                break
        else:
            # Hit max iterations
            logger.warning("ExecutionAgent: max iterations (%d) reached", MAX_ITERATIONS)
            messages.append(
                HumanMessage(content=(
                    "Maximum iterations reached. Please return the final JSON result now "
                    "summarising what you accomplished. No more tool calls."
                ))
            )
            response = await model.ainvoke(messages)
            self.track_tokens(response)

        # Parse the final result
        result = _parse_execution_result(response.content, plan)

        # Merge tracked files into the result
        result.setdefault("files_created", [])
        result.setdefault("files_modified", [])
        result.setdefault("commands_executed", [])
        for f in files_created:
            if f not in result["files_created"]:
                result["files_created"].append(f)
        for f in files_modified:
            if f not in result["files_modified"]:
                result["files_modified"].append(f)
        result["commands_executed"].extend(commands_executed)

        logger.info(
            "ExecutionAgent: completed with %d subtask results, %d files created, %d commands",
            len(result.get("subtask_results", [])),
            len(result.get("files_created", [])),
            len(result.get("commands_executed", [])),
        )

        return {
            **result,
            **self.get_token_usage(),
        }


def _get_tool_by_name(name: str) -> Any | None:
    """Look up a tool function by name."""
    tool_map = {t.name: t for t in EXECUTION_TOOLS}
    return tool_map.get(name)


def _format_plan(plan: list[dict[str, Any]]) -> str:
    """Format the plan into a human-readable string."""
    if not plan:
        return "(no plan provided)"
    lines = []
    for i, subtask in enumerate(plan):
        deps = subtask.get("depends_on", [])
        dep_str = f" (depends on: {deps})" if deps else ""
        lines.append(f"  {i}. {subtask.get('title', 'Untitled')}{dep_str}")
        lines.append(f"     {subtask.get('description', '')}")
    return "\n".join(lines)


def _format_completed(results: list[dict[str, Any]]) -> str:
    """Format previously completed subtask results."""
    if not results:
        return ""
    lines = []
    for r in results:
        lines.append(
            f"  [{r.get('status', '?')}] Subtask {r.get('index', '?')}: "
            f"{r.get('title', 'Untitled')} — {r.get('result', '')[:200]}"
        )
    return "\n".join(lines)


def _parse_execution_result(content: str, plan: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse the execution result JSON from the LLM response."""
    # Try direct parse
    try:
        data = json.loads(content.strip())
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Try code-fence extraction
    text = content.strip()
    for marker in ("```json", "```"):
        if marker in text:
            text = text.split(marker, 1)[1]
            text = text.split("```", 1)[0].strip()
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
            break

    logger.error("ExecutionAgent: could not parse result JSON: %s", content[:500])

    # Fallback: mark all subtasks as completed with the raw content as result
    return {
        "subtask_results": [
            {
                "index": i,
                "title": s.get("title", f"Subtask {i}"),
                "status": "completed",
                "result": content[:1000] if i == 0 else "See subtask 0 result.",
                "files_changed": [],
            }
            for i, s in enumerate(plan)
        ],
        "deliverable_summary": content[:2000],
        "files_created": [],
        "files_modified": [],
        "commands_executed": [],
    }
