"""ComplexTaskAgent — handles high-complexity tasks using the strongest model tier."""

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

# Same tools as ExecutionAgent (EXECUTION_TOOLS includes verify_file and run_tests)
COMPLEX_TOOLS = EXECUTION_TOOLS

# Higher iteration limit for complex tasks
MAX_ITERATIONS = 30


class ComplexTaskAgent(BaseAgent):
    """Executes high-complexity or high-budget tasks with the strongest model.

    Identical tool set to ExecutionAgent but uses ModelTier.STRONG and a higher
    iteration limit (25 vs 15) for deeper reasoning and more complex workflows.

    Returns:
        subtask_results (list[dict]): Result of each subtask execution.
        files_created (list[str]): New files created during execution.
        files_modified (list[str]): Existing files modified during execution.
        commands_executed (list[dict]): Commands that were run.
        deliverable_summary (str): High-quality summary of what was built.
    """

    def __init__(self, model_tier: str = ModelTier.STRONG.value) -> None:
        super().__init__(role=AgentRole.COMPLEX_TASK.value, model_tier=model_tier)

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute all subtasks via an extended ReAct tool-calling loop."""
        model = self.get_model()
        system_prompt = self.load_prompt()
        model_with_tools = model.bind_tools(COMPLEX_TOOLS)

        task_data = state.get("task_data", {})
        plan = state.get("plan", [])
        workspace_path = state.get("workspace_path", "/tmp/oriexa-workspaces/unknown")
        existing_results = state.get("subtask_results", [])
        review_feedback = state.get("review_feedback", "")

        plan_summary = _format_plan(plan)
        completed_summary = _format_completed(existing_results)

        execution_prompt = (
            "You are a senior engineer executing a complex task. Work through each "
            "subtask carefully, using the available tools. This is a high-complexity "
            "task that requires thorough attention to detail.\n\n"
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
                f"\nPrevious review feedback (address these issues carefully):\n"
                f"{review_feedback}\n"
            )

        execution_prompt += (
            "\nExecute each subtask in order. Be thorough — verify your work by "
            "reading back files you write and testing commands.\n\n"
            "After completing all subtasks, return a JSON object with:\n"
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

        # Extended ReAct loop
        for iteration in range(MAX_ITERATIONS):
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

                # Execute the tool
                tool_fn = _get_tool_by_name(tool_name)
                if tool_fn is not None:
                    try:
                        tool_result = await tool_fn.ainvoke(tool_args)
                    except Exception as exc:
                        tool_result = f"[ERROR] Tool execution failed: {exc}"
                        logger.error(
                            "ComplexTaskAgent tool error: %s(%s) -> %s",
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
                    "ComplexTaskAgent: loop detected at iteration %d, forcing completion",
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
            logger.warning("ComplexTaskAgent: max iterations (%d) reached", MAX_ITERATIONS)
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

        # Merge tracked files
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
            "ComplexTaskAgent: completed with %d subtask results, %d files created, %d commands",
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
    tool_map = {t.name: t for t in COMPLEX_TOOLS}
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

    logger.error("ComplexTaskAgent: could not parse result JSON: %s", content[:500])

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
