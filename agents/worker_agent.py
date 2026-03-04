#!/usr/bin/env python3
"""
Oriexa Worker Agent — Generate and Submit Deliverables

One-shot agent that:
  1. Checks for accepted/claimed tasks assigned to this agent
  2. Generates a deliverable using LLM
  3. Submits it to the Oriexa API

Usage (called by orchestrator, not directly):
    python -m agents.worker_agent --api-key <key> --task-id <id> [--base-url <url>]
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from agents.base_agent import (
    BASE_URL,
    OriexaClient,
    llm_call,
    log_act,
    log_err,
    log_ok,
    log_think,
    log_warn,
)

AGENT_NAME = "Worker"


# ═══════════════════════════════════════════════════════════════════════════
# WORKER BRAIN
# ═══════════════════════════════════════════════════════════════════════════

def generate_deliverable(task: dict) -> str:
    """Generate the actual deliverable content using LLM."""
    title = task.get("title") or ""
    desc = task.get("description") or ""
    reqs = task.get("requirements") or ""

    return llm_call(
        "You are a senior developer delivering high-quality work. "
        "Write clean, production-ready code with proper documentation. "
        "Include all necessary imports, type hints, docstrings, and edge case handling.",

        f"Complete this task and deliver the full implementation:\n\n"
        f"## Task: {title}\n\n"
        f"## Description:\n{desc}\n\n"
        f"## Requirements:\n{reqs}\n\n"
        "Deliver the complete solution with code and brief explanation.",
        max_tokens=3000,
    )


# ═══════════════════════════════════════════════════════════════════════════
# WORKER MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_worker(client: OriexaClient, task_id: int) -> dict:
    """
    Generate and submit a deliverable for a specific task.
    Returns a result dict with:
      - action: "delivered" | "already_submitted" | "error"
      - deliverable_id: (if delivered)
    """
    log_think(f"Fetching task #{task_id} details...", AGENT_NAME)

    try:
        task = client.get_task(task_id)
    except Exception as e:
        log_err(f"Failed to fetch task #{task_id}: {e}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": str(e)}

    if not task:
        log_err(f"Task #{task_id} not found", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": "task_not_found"}

    # Check if already submitted
    deliverables = task.get("deliverables", [])
    submitted = [d for d in deliverables if d.get("status") == "submitted"]
    if submitted:
        log_think(f"Task #{task_id} already has {len(submitted)} submitted deliverable(s)", AGENT_NAME)
        return {"action": "already_submitted", "task_id": task_id}

    # Generate deliverable
    log_act(f"Generating deliverable for: \"{task.get('title', '')[:60]}\"", AGENT_NAME)

    try:
        content = generate_deliverable(task)
    except Exception as e:
        log_err(f"LLM deliverable generation FAILED: {e}", AGENT_NAME)
        log_err(f"  {traceback.format_exc().strip().splitlines()[-1]}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": f"llm_failed: {e}"}

    if not content or len(content.strip()) < 10:
        log_err(f"Generated content is empty or too short ({len(content)} chars)", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": "empty_content"}

    # Submit deliverable
    log_act(f"Submitting deliverable ({len(content)} chars)...", AGENT_NAME)

    try:
        resp = client.submit_deliverable(task_id, content)
    except Exception as e:
        log_err(f"Deliverable submission request FAILED: {e}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": f"submit_failed: {e}"}

    if resp.get("ok"):
        del_id = resp["data"]["id"]
        log_ok(f"Deliverable #{del_id} submitted for task #{task_id}!", AGENT_NAME)
        return {"action": "delivered", "task_id": task_id, "deliverable_id": del_id}
    else:
        err = resp.get("error") or {}
        log_err(f"API rejected deliverable: code={err.get('code', '?')} msg={err.get('message', '')[:200]}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": f"api_rejected: {err.get('message', '')}"}


def run_worker_all(client: OriexaClient) -> list[dict]:
    """Check all assigned tasks and generate deliverables for those that need them."""
    results = []

    try:
        my_tasks = client.get_my_tasks()
    except Exception as e:
        log_err(f"Failed to fetch assigned tasks: {e}", AGENT_NAME)
        return [{"action": "error", "error": str(e)}]

    if not my_tasks:
        log_think("No tasks assigned to this agent", AGENT_NAME)
        return [{"action": "no_tasks"}]

    for task_summary in my_tasks:
        task_id = task_summary.get("id") or task_summary.get("task_id")
        status = task_summary.get("status", "")

        if status in ("claimed", "in_progress", "accepted"):
            result = run_worker(client, task_id)
            results.append(result)
        elif status == "completed":
            log_ok(f"Task #{task_id} already completed", AGENT_NAME)

    return results if results else [{"action": "no_pending_work"}]


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Oriexa Worker Agent")
    parser.add_argument("--api-key", type=str, required=True, help="Agent API key")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help="Oriexa API base URL")
    parser.add_argument("--task-id", type=int, help="Specific task ID to work on (optional)")
    args = parser.parse_args()

    client = OriexaClient(args.base_url, args.api_key)

    profile = client.get_profile()
    if not profile:
        log_err("Failed to authenticate with API key", AGENT_NAME)
        sys.exit(1)

    log_ok(f"Worker Agent active as: {client.agent_name} (ID: {client.agent_id})", AGENT_NAME)

    if args.task_id:
        results = [run_worker(client, args.task_id)]
    else:
        results = run_worker_all(client)

    print(f"\n__RESULT__:{json.dumps(results)}", flush=True)


if __name__ == "__main__":
    main()
