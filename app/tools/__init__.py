"""Oriexa orchestrator tools — LangChain @tool functions for agent use."""

from app.tools.shell import execute_command, execute_parallel
from app.tools.file_ops import read_file, write_file, list_files, verify_file
from app.tools.communication import post_question, read_task_messages, send_chat_message
from app.tools.code_analysis import lint_code, analyze_codebase, run_tests
from app.tools.platform import list_available_agents, consult_specialist
from app.tools.deployment import (
    create_github_repo,
    deploy_to_vercel,
    run_full_test_suite,
    DEPLOYMENT_TOOLS,
)

__all__ = [
    # Shell execution
    "execute_command",
    "execute_parallel",
    # File operations
    "read_file",
    "write_file",
    "list_files",
    "verify_file",
    # Communication
    "post_question",
    "read_task_messages",
    "send_chat_message",
    # Code analysis
    "lint_code",
    "analyze_codebase",
    "run_tests",
    # Platform
    "list_available_agents",
    "consult_specialist",
    # Deployment
    "create_github_repo",
    "deploy_to_vercel",
    "run_full_test_suite",
    "DEPLOYMENT_TOOLS",
]

# Tool groups for different agent roles

# Execution agents: full toolset for building and testing
EXECUTION_TOOLS = [
    execute_command,
    execute_parallel,
    read_file,
    write_file,
    list_files,
    verify_file,
    lint_code,
    run_tests,
    consult_specialist,
    send_chat_message,
]

# Planning agents: read-only exploration + platform awareness
PLANNING_TOOLS = [
    read_file,
    list_files,
    analyze_codebase,
    list_available_agents,
    consult_specialist,
]

# Communication tools (for clarification agent)
COMMUNICATION_TOOLS = [
    post_question,
    read_task_messages,
]

# All tools combined
ALL_TOOLS = [
    execute_command,
    execute_parallel,
    read_file,
    write_file,
    list_files,
    verify_file,
    post_question,
    read_task_messages,
    lint_code,
    analyze_codebase,
    run_tests,
    list_available_agents,
    consult_specialist,
]
