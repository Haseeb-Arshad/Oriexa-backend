"""Code analysis tools — linting and codebase analysis within task workspaces."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

from app.sandbox.executor import SandboxExecutor
from app.sandbox.policy import CommandPolicy

logger = logging.getLogger(__name__)

_executor: SandboxExecutor | None = None

# File extensions to language mapping
LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

# Linter commands per language
LINTER_COMMANDS: dict[str, list[str]] = {
    "python": [
        "python -m py_compile {file}",
        "python -m flake8 --max-line-length=120 --count --statistics {file}",
    ],
    "javascript": [
        "npx eslint --no-eslintrc --rule '{{ \"no-unused-vars\": \"warn\", \"no-undef\": \"error\" }}' {file}",
    ],
    "typescript": [
        "npx tsc --noEmit --pretty {file}",
    ],
}

# Fallback: syntax-check only
SYNTAX_CHECK_COMMANDS: dict[str, str] = {
    "python": "python -m py_compile {file}",
    "javascript": "node --check {file}",
    "typescript": "npx tsc --noEmit --pretty {file}",
}


def _get_executor() -> SandboxExecutor:
    """Lazy-initialise a shared SandboxExecutor singleton."""
    global _executor
    if _executor is None:
        _executor = SandboxExecutor(policy=CommandPolicy())
    return _executor


def _detect_language(file_path: str) -> str | None:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(ext)


@tool
async def lint_code(
    file_path: Annotated[str, "Relative path to the file to lint inside the workspace"],
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    language: Annotated[str | None, "Language override (python, javascript, typescript). Auto-detected if omitted."] = None,
) -> str:
    """Run linting and syntax checks on a single source file.

    Automatically detects the language from the file extension, or accepts
    an explicit override. Runs appropriate linters (flake8 for Python,
    eslint for JS, tsc for TypeScript) in the sandbox.

    Returns a summary of lint results: errors, warnings, and pass/fail status.
    """
    # Validate the file exists
    workspace = Path(workspace_path).resolve()
    target = (workspace / file_path).resolve()

    if not str(target).startswith(str(workspace)):
        return "[ERROR] Path traversal detected: file is outside workspace."

    if not target.exists():
        return f"[ERROR] File not found: {file_path}"

    if not target.is_file():
        return f"[ERROR] Not a regular file: {file_path}"

    # Detect or validate language
    detected = language or _detect_language(file_path)
    if detected is None:
        return (
            f"[ERROR] Cannot determine language for '{file_path}'. "
            f"Supported extensions: {', '.join(sorted(LANGUAGE_MAP.keys()))}. "
            f"You can also pass language= explicitly."
        )

    detected = detected.lower()

    logger.info(
        "lint_code: file=%s workspace=%s language=%s",
        file_path, workspace_path, detected,
    )

    executor = _get_executor()
    results: list[str] = [f"Lint results for: {file_path} (language: {detected})"]
    had_errors = False
    any_linter_ran = False

    # Try full linters first
    linter_cmds = LINTER_COMMANDS.get(detected, [])
    for cmd_template in linter_cmds:
        cmd = cmd_template.replace("{file}", str(target))
        result = await executor.execute(command=cmd, cwd=workspace_path, timeout=60)

        if result.policy_decision and not result.policy_decision.allowed:
            results.append(f"  [SKIPPED] {cmd_template.split()[0]}: blocked by policy")
            continue

        any_linter_ran = True
        tool_name = cmd_template.split()[0]
        if tool_name in ("python", "node", "npx"):
            # Use the second token for better naming
            parts = cmd_template.split()
            tool_name = parts[1] if len(parts) > 1 else parts[0]

        if result.exit_code == 0:
            output = result.stdout.strip() or "(clean)"
            results.append(f"  [{tool_name}] PASS: {output[:500]}")
        else:
            had_errors = True
            error_output = (result.stdout.strip() + "\n" + result.stderr.strip()).strip()
            if len(error_output) > 2000:
                error_output = error_output[:2000] + "\n... [truncated]"
            results.append(f"  [{tool_name}] FAIL (exit {result.exit_code}):\n{error_output}")

    # If no linter ran (all blocked), try syntax check as fallback
    if not any_linter_ran and detected in SYNTAX_CHECK_COMMANDS:
        cmd = SYNTAX_CHECK_COMMANDS[detected].replace("{file}", str(target))
        result = await executor.execute(command=cmd, cwd=workspace_path, timeout=30)

        if result.policy_decision and not result.policy_decision.allowed:
            results.append("  [SKIPPED] Syntax check blocked by policy")
        else:
            any_linter_ran = True
            if result.exit_code == 0:
                results.append("  [syntax] PASS: No syntax errors")
            else:
                had_errors = True
                error_output = (result.stdout.strip() + "\n" + result.stderr.strip()).strip()
                results.append(f"  [syntax] FAIL:\n{error_output[:2000]}")

    if not any_linter_ran:
        results.append("  [WARNING] No linters could run. Check sandbox policy and tool availability.")

    # Summary line
    if had_errors:
        results.append("\nOverall: ISSUES FOUND")
    else:
        results.append("\nOverall: CLEAN")

    return "\n".join(results)


@tool
async def analyze_codebase(
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    pattern: Annotated[str | None, "Grep pattern to search for in the codebase (regex supported)"] = None,
    file_extensions: Annotated[str | None, "Comma-separated file extensions to include, e.g. '.py,.js,.ts'"] = None,
) -> str:
    """Analyze the structure and contents of a codebase in the workspace.

    Performs several analyses:
    1. File tree with language statistics (line counts, file counts per extension)
    2. Optionally searches for a pattern across all files (uses grep)
    3. Identifies key files (README, package.json, requirements.txt, etc.)

    Returns a structured report of the codebase.
    """
    workspace = Path(workspace_path).resolve()

    if not workspace.exists():
        return f"[ERROR] Workspace not found: {workspace_path}"

    if not workspace.is_dir():
        return f"[ERROR] Not a directory: {workspace_path}"

    logger.info(
        "analyze_codebase: workspace=%s pattern=%s extensions=%s",
        workspace_path, pattern, file_extensions,
    )

    executor = _get_executor()
    report_parts: list[str] = ["=== Codebase Analysis ===", ""]

    # -- 1. File tree (using find) --
    find_result = await executor.execute(
        command="find . -type f -not -path './.git/*' -not -path './node_modules/*' -not -path './__pycache__/*' -not -path './.venv/*' | head -200",
        cwd=workspace_path,
        timeout=30,
    )

    all_files: list[str] = []
    if find_result.exit_code == 0 and find_result.stdout.strip():
        all_files = [f.strip() for f in find_result.stdout.strip().splitlines() if f.strip()]

    report_parts.append(f"Total files found: {len(all_files)}")

    # -- 2. Language statistics --
    ext_counts: dict[str, int] = {}
    ext_lines: dict[str, int] = {}

    # Parse extensions filter
    allowed_exts: set[str] | None = None
    if file_extensions:
        allowed_exts = set()
        for ext in file_extensions.split(","):
            ext = ext.strip()
            if not ext.startswith("."):
                ext = f".{ext}"
            allowed_exts.add(ext.lower())

    for f in all_files:
        ext = Path(f).suffix.lower()
        if not ext:
            ext = "(no extension)"
        if allowed_exts and ext not in allowed_exts:
            continue
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    if ext_counts:
        report_parts.append("\nFile types:")
        for ext, count in sorted(ext_counts.items(), key=lambda x: -x[1]):
            lang = LANGUAGE_MAP.get(ext, "")
            lang_label = f" ({lang})" if lang else ""
            report_parts.append(f"  {ext}{lang_label}: {count} files")

    # -- 3. Line counts for top languages --
    code_extensions = [".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb", ".php"]
    relevant_exts = [e for e in code_extensions if e in ext_counts]

    if relevant_exts:
        # Use wc -l on source files for line count
        for ext in relevant_exts[:5]:  # limit to top 5 extensions
            wc_cmd = f"find . -name '*{ext}' -not -path './.git/*' -not -path './node_modules/*' -not -path './__pycache__/*' -not -path './.venv/*' -exec cat {{}} + | wc -l"
            wc_result = await executor.execute(command=wc_cmd, cwd=workspace_path, timeout=30)
            if wc_result.exit_code == 0 and wc_result.stdout.strip():
                try:
                    line_count = int(wc_result.stdout.strip().split()[0])
                    ext_lines[ext] = line_count
                except (ValueError, IndexError):
                    pass

        if ext_lines:
            report_parts.append("\nLine counts:")
            for ext, lines in sorted(ext_lines.items(), key=lambda x: -x[1]):
                report_parts.append(f"  {ext}: {lines:,} lines")

    # -- 4. Key files detection --
    key_files = [
        "README.md", "README.rst", "README.txt",
        "package.json", "tsconfig.json",
        "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
        "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        ".env.example", ".gitignore",
        "Cargo.toml", "go.mod",
    ]

    found_key_files: list[str] = []
    for f in all_files:
        basename = Path(f).name
        if basename in key_files:
            found_key_files.append(f)

    if found_key_files:
        report_parts.append("\nKey files detected:")
        for f in sorted(found_key_files):
            report_parts.append(f"  {f}")

    # -- 5. Pattern search (optional) --
    if pattern:
        report_parts.append(f"\nPattern search: '{pattern}'")

        grep_cmd = f"grep -rn --include='*' -E '{pattern}' . --color=never"

        # If specific extensions requested, build include flags
        if allowed_exts:
            include_flags = " ".join(f"--include='*{ext}'" for ext in allowed_exts)
            grep_cmd = f"grep -rn {include_flags} -E '{pattern}' . --color=never"

        # Exclude noise directories
        grep_cmd += " --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ --exclude-dir=.venv"
        grep_cmd += " | head -50"

        grep_result = await executor.execute(command=grep_cmd, cwd=workspace_path, timeout=30)

        if grep_result.exit_code == 0 and grep_result.stdout.strip():
            matches = grep_result.stdout.strip().splitlines()
            report_parts.append(f"  Found {len(matches)} match(es):")
            for match in matches:
                if len(match) > 200:
                    match = match[:200] + "..."
                report_parts.append(f"    {match}")
        elif grep_result.exit_code == 1:
            report_parts.append("  No matches found.")
        else:
            report_parts.append(f"  [WARNING] grep failed: {grep_result.stderr.strip()[:500]}")

    # -- 6. Directory structure overview (top 2 levels) --
    report_parts.append("\nDirectory structure (top 2 levels):")
    ls_result = await executor.execute(
        command="find . -maxdepth 2 -type d -not -path './.git*' -not -path './node_modules*' -not -path './__pycache__*' -not -path './.venv*' | sort | head -50",
        cwd=workspace_path,
        timeout=15,
    )
    if ls_result.exit_code == 0 and ls_result.stdout.strip():
        for d in ls_result.stdout.strip().splitlines():
            report_parts.append(f"  {d}")
    else:
        report_parts.append("  (could not list directories)")

    report_parts.append("\n=== End Analysis ===")
    return "\n".join(report_parts)


@tool
async def run_tests(
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    test_command: Annotated[str | None, "Explicit test command to run. Auto-detected if omitted."] = None,
    test_path: Annotated[str | None, "Specific test file or directory to run"] = None,
) -> str:
    """Run tests and return structured results.

    Auto-detects the test framework from project config files:
    - pytest (pyproject.toml / setup.cfg / conftest.py)
    - npm test (package.json)
    - go test (go.mod)

    Returns a structured report with: pass/fail counts, failed test names,
    and overall status.
    """
    executor = _get_executor()
    workspace = Path(workspace_path).resolve()

    if test_command:
        cmd = test_command
    else:
        # Auto-detect test framework
        cmd = await _detect_test_command(workspace, test_path)
        if not cmd:
            return "[ERROR] Could not auto-detect test framework. Pass test_command explicitly."

    logger.info("run_tests: cmd=%s workspace=%s", cmd, workspace_path)

    result = await executor.execute(command=cmd, cwd=workspace_path, timeout=120)

    # Build structured report
    parts: list[str] = [f"Test command: {cmd}", f"Exit code: {result.exit_code}", ""]

    if result.timed_out:
        parts.append("[TIMED OUT] Tests exceeded timeout")

    output = (result.stdout + "\n" + result.stderr).strip()

    # Parse test results from output
    summary = _parse_test_summary(output)
    if summary:
        parts.append(f"Results: {summary}")

    # Include relevant output (truncated)
    if output:
        if len(output) > 5000:
            # Show first 2000 and last 2000
            parts.append("--- Test Output (truncated) ---")
            parts.append(output[:2000])
            parts.append("... [truncated] ...")
            parts.append(output[-2000:])
        else:
            parts.append("--- Test Output ---")
            parts.append(output)

    # Overall verdict
    if result.exit_code == 0:
        parts.append("\nOverall: ALL TESTS PASSED")
    else:
        parts.append("\nOverall: TESTS FAILED")

    return "\n".join(parts)


async def _detect_test_command(workspace: Path, test_path: str | None) -> str | None:
    """Auto-detect the appropriate test command."""
    path_arg = test_path or ""

    # Python: pytest
    if (workspace / "pyproject.toml").exists() or (workspace / "conftest.py").exists() or (workspace / "tests").exists():
        cmd = "python -m pytest -v --tb=short"
        if path_arg:
            cmd += f" {path_arg}"
        return cmd

    # Node.js: npm test
    if (workspace / "package.json").exists():
        if path_arg:
            return f"npx jest --verbose {path_arg}"
        return "npm test"

    # Go: go test
    if (workspace / "go.mod").exists():
        if path_arg:
            return f"go test -v {path_arg}"
        return "go test -v ./..."

    return None


def _parse_test_summary(output: str) -> str | None:
    """Extract pass/fail summary from test output."""
    lines = output.splitlines()

    # pytest format: "X passed, Y failed, Z errors"
    for line in reversed(lines):
        if "passed" in line and ("failed" in line or "error" in line or "warning" in line):
            return line.strip()
        if line.strip().startswith("=") and "passed" in line:
            return line.strip().strip("=").strip()

    # Jest/Mocha format: "Tests: X passed, Y failed, Z total"
    for line in reversed(lines):
        if "Tests:" in line and ("passed" in line or "failed" in line):
            return line.strip()

    # Go format: "ok  " or "FAIL"
    for line in reversed(lines):
        if line.startswith("ok ") or line.startswith("FAIL"):
            return line.strip()

    return None
