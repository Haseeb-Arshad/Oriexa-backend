from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_normalize_scaffold_command_downgrades_for_node18(tmp_path: Path):
    from agents.coder_agent import DEFAULT_NEXT_SCAFFOLD_COMMAND, _normalize_scaffold_command

    with patch("agents.coder_agent._detect_node_major", return_value=18):
        normalized = _normalize_scaffold_command(DEFAULT_NEXT_SCAFFOLD_COMMAND, tmp_path)

    assert "create-next-app@15" in normalized


def test_normalize_scaffold_command_keeps_latest_for_modern_node(tmp_path: Path):
    from agents.coder_agent import DEFAULT_NEXT_SCAFFOLD_COMMAND, _normalize_scaffold_command

    with patch("agents.coder_agent._detect_node_major", return_value=22):
        normalized = _normalize_scaffold_command(DEFAULT_NEXT_SCAFFOLD_COMMAND, tmp_path)

    assert normalized == DEFAULT_NEXT_SCAFFOLD_COMMAND


def test_run_scaffold_command_retries_on_engine_mismatch(tmp_path: Path):
    from agents.coder_agent import DEFAULT_NEXT_SCAFFOLD_COMMAND, _run_scaffold_command

    with patch("agents.coder_agent._detect_node_major", return_value=None), patch(
        "agents.coder_agent.run_shell_combined",
        side_effect=[
            (1, "npm WARN EBADENGINE Unsupported engine for next@16"),
            (0, "success"),
        ],
    ) as run_mock, patch("agents.coder_agent._cleanup_scaffold_artifacts") as cleanup_mock:
        executed_cmd, rc, out = _run_scaffold_command(DEFAULT_NEXT_SCAFFOLD_COMMAND, tmp_path)

    assert rc == 0
    assert out == "success"
    assert "create-next-app@15" in executed_cmd
    assert run_mock.call_count == 2
    cleanup_mock.assert_called_once_with(tmp_path)
