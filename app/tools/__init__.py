"""Oriexa orchestrator tools — LangChain @tool functions for agent use."""

from app.tools.shell import execute_command
from app.tools.file_ops import read_file, write_file, list_files, verify_file
from app.tools.communication import send_clarification, check_response
from app.tools.code_analysis import lint_code, analyze_codebase, run_tests

__all__ = [
    # Shell execution
    "execute_command",
    # File operations
    "read_file",
    "write_file",
    "list_files",
    "verify_file",
    # Communication
    "send_clarification",
    "check_response",
    # Code analysis
    "lint_code",
    "analyze_codebase",
    "run_tests",
]

# Tool groups for different agent roles

# Execution agents: full toolset for building and testing
EXECUTION_TOOLS = [
    execute_command,
    read_file,
    write_file,
    list_files,
    verify_file,
    lint_code,
    run_tests,
]

# Planning agents: read-only exploration tools
PLANNING_TOOLS = [
    read_file,
    list_files,
    analyze_codebase,
]

# Communication tools (for clarification agent)
COMMUNICATION_TOOLS = [
    send_clarification,
    check_response,
]

# All tools combined
ALL_TOOLS = [
    execute_command,
    read_file,
    write_file,
    list_files,
    verify_file,
    send_clarification,
    check_response,
    lint_code,
    analyze_codebase,
    run_tests,
]
