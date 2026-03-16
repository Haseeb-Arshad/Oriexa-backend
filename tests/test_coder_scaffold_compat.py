from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_normalize_scaffold_command_keeps_latest_for_node18(tmp_path: Path):
    from agents.coder_agent import DEFAULT_NEXT_SCAFFOLD_COMMAND, _normalize_scaffold_command

    with patch("agents.coder_agent._detect_node_major", return_value=18):
        normalized = _normalize_scaffold_command(DEFAULT_NEXT_SCAFFOLD_COMMAND, tmp_path)

    assert normalized == DEFAULT_NEXT_SCAFFOLD_COMMAND


def test_canonicalize_next_scaffold_command_rewrites_pinned_versions():
    from agents.coder_agent import DEFAULT_NEXT_SCAFFOLD_COMMAND, _canonicalize_next_scaffold_command

    normalized = _canonicalize_next_scaffold_command(
        "npx create-next-app@15 ./ --typescript --tailwind --eslint --app --no-src-dir --import-alias @/* --yes --force"
    )

    assert normalized == DEFAULT_NEXT_SCAFFOLD_COMMAND


def test_run_scaffold_command_does_not_retry_with_older_next(tmp_path: Path):
    from agents.coder_agent import DEFAULT_NEXT_SCAFFOLD_COMMAND, _run_scaffold_command

    with patch("agents.coder_agent._detect_node_major", return_value=None), patch(
        "agents.coder_agent.run_shell_combined",
        return_value=(1, "npm WARN EBADENGINE Unsupported engine for next@16"),
    ) as run_mock, patch("agents.coder_agent.append_build_log") as build_log_mock:
        executed_cmd, rc, out = _run_scaffold_command(DEFAULT_NEXT_SCAFFOLD_COMMAND, tmp_path)

    assert rc == 1
    assert executed_cmd == DEFAULT_NEXT_SCAFFOLD_COMMAND
    assert "will not pin an older Next.js version automatically" in out
    assert run_mock.call_count == 1
    build_log_mock.assert_called_once()
