#!/usr/bin/env python3
"""
Oriexa Revision Agent — Handle Revision Requests

One-shot agent that:
  1. Checks for tasks with revision-requested deliverables
  2. Re-generates an improved deliverable using LLM  
  3. Submits the revised work

Usage (called by orchestrator, not directly):
    python -m agents.revision_agent --api-key <key> [--task-id <id>] [--base-url <url>]
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

AGENT_NAME = "Revision"


# ═══════════════════════════════════════════════════════════════════════════
# REVISION BRAIN
# ═══════════════════════════════════════════════════════════════════════════

def handle_revision(task: dict, deliverable: dict, feedback: str) -> str:
    """Generate an improved deliverable based on feedback."""
    return llm_call(
        "You are a senior developer revising work based on feedback. "
        "Address ALL feedback points and improve the overall quality.",

        f"## Original Task: {task.get('title')}\n"
        f"## Description: {(task.get('description') or '')[:500]}\n"
        f"## Requirements: {(task.get('requirements') or '')[:300]}\n\n"
        f"## Previous Deliverable:\n{deliverable.get('content', '')[:2000]}\n\n"
        f"## Revision Feedback:\n{feedback}\n\n"
        "Deliver the improved, complete solution addressing all feedback.",
        max_tokens=3000,
    )


# ═══════════════════════════════════════════════════════════════════════════
# REVISION MAIN
# ═══════════════════════════════════════════════════════════════════════════

def run_revision(client: OriexaClient, task_id: int) -> dict:
    """
    Handle a revision request for a specific task.
    Returns a result dict with:
      - action: "revised" | "no_revision_needed" | "error"
      - deliverable_id: (if revised)
    """
    log_think(f"Checking task #{task_id} for revision requests...", AGENT_NAME)

    try:
        task = client.get_task(task_id)
    except Exception as e:
        log_err(f"Failed to fetch task #{task_id}: {e}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": str(e)}

    if not task:
        log_err(f"Task #{task_id} not found", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": "task_not_found"}

    deliverables = task.get("deliverables", [])
    revision_requested = [d for d in deliverables if d.get("status") == "revision_requested"]

    if not revision_requested:
        log_think(f"Task #{task_id}: no revision requests pending", AGENT_NAME)
        return {"action": "no_revision_needed", "task_id": task_id}

    last = revision_requested[-1]
    feedback = last.get("revision_notes", "Please improve the deliverable.")

    log_act(f"Revision requested for task #{task_id}: \"{feedback[:80]}\"", AGENT_NAME)

    try:
        improved = handle_revision(task, last, feedback)
    except Exception as e:
        log_err(f"LLM revision generation FAILED: {e}", AGENT_NAME)
        log_err(f"  {traceback.format_exc().strip().splitlines()[-1]}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": f"llm_failed: {e}"}

    if not improved or len(improved.strip()) < 10:
        log_err(f"Revised content is empty or too short ({len(improved)} chars)", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": "empty_content"}

    log_act(f"Submitting revised deliverable ({len(improved)} chars)...", AGENT_NAME)

    try:
        resp = client.submit_deliverable(task_id, improved)
    except Exception as e:
        log_err(f"Revision submission FAILED: {e}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": f"submit_failed: {e}"}

    if resp.get("ok"):
        del_id = resp["data"]["id"]
        log_ok(f"Revision #{del_id} submitted for task #{task_id}!", AGENT_NAME)
        return {"action": "revised", "task_id": task_id, "deliverable_id": del_id}
    else:
        err = resp.get("error") or {}
        log_err(f"API rejected revision: {err.get('message', '')[:200]}", AGENT_NAME)
        return {"action": "error", "task_id": task_id, "error": f"api_rejected: {err.get('message', '')}"}


def run_revision_all(client: OriexaClient) -> list[dict]:
    """Check all in_progress tasks for revision requests."""
    results = []

    try:
        my_tasks = client.get_my_tasks()
    except Exception as e:
        log_err(f"Failed to fetch assigned tasks: {e}", AGENT_NAME)
        return [{"action": "error", "error": str(e)}]

    for task_summary in my_tasks:
        task_id = task_summary.get("id") or task_summary.get("task_id")
        status = task_summary.get("status", "")

        if status == "in_progress":
            result = run_revision(client, task_id)
            results.append(result)

    return results if results else [{"action": "no_revisions_pending"}]


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Oriexa Revision Agent")
    parser.add_argument("--api-key", type=str, required=True, help="Agent API key")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help="Oriexa API base URL")
    parser.add_argument("--task-id", type=int, help="Specific task ID to revise (optional)")
    args = parser.parse_args()

    client = OriexaClient(args.base_url, args.api_key)

    profile = client.get_profile()
    if not profile:
        log_err("Failed to authenticate with API key", AGENT_NAME)
        sys.exit(1)

    log_ok(f"Revision Agent active as: {client.agent_name} (ID: {client.agent_id})", AGENT_NAME)

    if args.task_id:
        results = [run_revision(client, args.task_id)]
    else:
        results = run_revision_all(client)

    print(f"\n__RESULT__:{json.dumps(results)}", flush=True)


if __name__ == "__main__":
    main()
