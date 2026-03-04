"""File operation tools — read, write, and list files within a task workspace."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Safety limits
MAX_READ_SIZE = 1_000_000  # 1 MB
MAX_WRITE_SIZE = 5_000_000  # 5 MB
MAX_LIST_DEPTH = 8
MAX_LIST_ENTRIES = 500


def _resolve_safe_path(workspace_path: str, relative_path: str) -> Path:
    """Resolve a relative path within the workspace, preventing path traversal.

    Raises ValueError if the resolved path escapes the workspace root.
    """
    workspace = Path(workspace_path).resolve()
    target = (workspace / relative_path).resolve()
    # Ensure target is inside the workspace
    if not str(target).startswith(str(workspace)):
        raise ValueError(
            f"Path traversal detected: '{relative_path}' resolves outside the workspace"
        )
    return target


@tool
async def read_file(
    file_path: Annotated[str, "Relative path to the file inside the workspace"],
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    offset: Annotated[int, "Line number to start reading from (0-based)"] = 0,
    limit: Annotated[int | None, "Maximum number of lines to return"] = None,
) -> str:
    """Read the contents of a file from the task workspace.

    Supports optional line-range selection via offset and limit.
    Files larger than 1 MB are rejected.
    Binary files are detected and rejected with a descriptive message.
    """
    try:
        target = _resolve_safe_path(workspace_path, file_path)
    except ValueError as exc:
        return f"[ERROR] {exc}"

    if not target.exists():
        return f"[ERROR] File not found: {file_path}"

    if not target.is_file():
        return f"[ERROR] Not a regular file: {file_path}"

    file_size = target.stat().st_size
    if file_size > MAX_READ_SIZE:
        return (
            f"[ERROR] File too large ({file_size:,} bytes). "
            f"Maximum read size is {MAX_READ_SIZE:,} bytes."
        )

    logger.info("read_file: path=%s workspace=%s offset=%d limit=%s", file_path, workspace_path, offset, limit)

    try:
        raw = target.read_bytes()
    except PermissionError:
        return f"[ERROR] Permission denied: {file_path}"
    except OSError as exc:
        return f"[ERROR] Could not read file: {exc}"

    # Detect binary files
    if b"\x00" in raw[:8192]:
        return f"[ERROR] File appears to be binary ({file_size:,} bytes). Cannot display."

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            return f"[ERROR] Could not decode file as text."

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)

    # Apply offset/limit
    selected = lines[offset:]
    if limit is not None and limit > 0:
        selected = selected[:limit]

    content = "".join(selected)

    header = f"File: {file_path} ({total_lines} lines, {file_size:,} bytes)"
    if offset > 0 or limit is not None:
        end_line = offset + len(selected)
        header += f" [showing lines {offset + 1}-{end_line}]"

    return f"{header}\n{content}"


@tool
async def write_file(
    file_path: Annotated[str, "Relative path to the file inside the workspace"],
    content: Annotated[str, "The text content to write to the file"],
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    create_dirs: Annotated[bool, "Create parent directories if they do not exist"] = True,
) -> str:
    """Write text content to a file in the task workspace.

    Creates parent directories automatically unless disabled.
    Overwrites the file if it already exists.
    Content larger than 5 MB is rejected.
    """
    try:
        target = _resolve_safe_path(workspace_path, file_path)
    except ValueError as exc:
        return f"[ERROR] {exc}"

    if len(content.encode("utf-8")) > MAX_WRITE_SIZE:
        return (
            f"[ERROR] Content too large ({len(content.encode('utf-8')):,} bytes). "
            f"Maximum write size is {MAX_WRITE_SIZE:,} bytes."
        )

    logger.info("write_file: path=%s workspace=%s size=%d", file_path, workspace_path, len(content))

    try:
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)

        target.write_text(content, encoding="utf-8")
    except PermissionError:
        return f"[ERROR] Permission denied: {file_path}"
    except OSError as exc:
        return f"[ERROR] Could not write file: {exc}"

    written_size = target.stat().st_size
    return f"[OK] Wrote {written_size:,} bytes to {file_path}"


@tool
async def list_files(
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    sub_path: Annotated[str, "Relative subdirectory to list (default: workspace root)"] = ".",
    max_depth: Annotated[int, "Maximum directory depth to recurse into"] = 3,
    include_hidden: Annotated[bool, "Include hidden files/directories (starting with '.')"] = False,
) -> str:
    """List files and directories in the task workspace.

    Returns a tree-like listing with file sizes.
    Respects max depth to avoid overwhelming output.
    Hidden files are excluded by default.
    """
    try:
        root = _resolve_safe_path(workspace_path, sub_path)
    except ValueError as exc:
        return f"[ERROR] {exc}"

    if not root.exists():
        return f"[ERROR] Directory not found: {sub_path}"

    if not root.is_dir():
        return f"[ERROR] Not a directory: {sub_path}"

    effective_depth = min(max_depth, MAX_LIST_DEPTH)

    logger.info("list_files: workspace=%s sub_path=%s max_depth=%d", workspace_path, sub_path, effective_depth)

    entries: list[str] = []
    count = 0

    def _walk(directory: Path, depth: int, prefix: str) -> None:
        nonlocal count

        if depth > effective_depth or count >= MAX_LIST_ENTRIES:
            return

        try:
            items = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            entries.append(f"{prefix}[permission denied]")
            return

        for item in items:
            if count >= MAX_LIST_ENTRIES:
                entries.append(f"{prefix}... (truncated at {MAX_LIST_ENTRIES} entries)")
                return

            name = item.name
            if not include_hidden and name.startswith("."):
                continue

            if item.is_dir():
                entries.append(f"{prefix}{name}/")
                count += 1
                _walk(item, depth + 1, prefix + "  ")
            elif item.is_file():
                try:
                    size = item.stat().st_size
                    size_str = _format_size(size)
                except OSError:
                    size_str = "???"
                entries.append(f"{prefix}{name}  ({size_str})")
                count += 1
            else:
                # symlink or other special
                entries.append(f"{prefix}{name}  [special]")
                count += 1

    _walk(root, 0, "")

    if not entries:
        return f"Directory '{sub_path}' is empty."

    header = f"Listing: {sub_path} ({count} entries, max_depth={effective_depth})"
    return f"{header}\n" + "\n".join(entries)


@tool
async def verify_file(
    file_path: Annotated[str, "Relative path to the file inside the workspace"],
    workspace_path: Annotated[str, "Absolute path to the task workspace directory"],
    expected_strings: Annotated[list[str] | None, "Optional list of strings that must appear in the file"] = None,
) -> str:
    """Verify that a file was written correctly.

    Checks: file exists, has content, is valid text, and optionally contains expected strings.
    Use this after every write_file to confirm the write succeeded.
    """
    try:
        target = _resolve_safe_path(workspace_path, file_path)
    except ValueError as exc:
        return f"[FAIL] {exc}"

    if not target.exists():
        return f"[FAIL] File does not exist: {file_path}"

    if not target.is_file():
        return f"[FAIL] Not a regular file: {file_path}"

    size = target.stat().st_size
    if size == 0:
        return f"[FAIL] File is empty: {file_path}"

    try:
        content = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        return f"[FAIL] Cannot read file: {exc}"

    line_count = len(content.splitlines())
    result = f"[OK] {file_path}: {line_count} lines, {size:,} bytes"

    if expected_strings:
        missing = [s for s in expected_strings if s not in content]
        if missing:
            result += f"\n[WARN] Missing expected strings: {missing}"
        else:
            result += f"\n[OK] All {len(expected_strings)} expected strings found"

    return result


def _format_size(size: int) -> str:
    """Human-readable file size."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
