"""
Oriexa Reviewer Agent — Entry Point

Usage:
    # Review a specific deliverable (one-shot mode)
    python reviewer-agent/run.py --task-id 42 --deliverable-id 8

    # Run in daemon mode (polls for new deliverables every 30s)
    python reviewer-agent/run.py --daemon

    # Daemon with custom poll interval

    
    python reviewer-agent/run.py --daemon --interval 60
"""

from __future__ import annotations
import argparse
import os
import sys
import time
import httpx
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the reviewer-agent directory
load_dotenv(Path(__file__).parent / ".env")
# Also try root .env
load_dotenv(Path(__file__).parent.parent / ".env")


def validate_env() -> None:
    """Ensure required environment variables are set."""
    required = ["ORIEXA_BASE_URL", "ORIEXA_REVIEWER_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        print("Copy reviewer-agent/.env.example to reviewer-agent/.env and fill in the values.")
        sys.exit(1)

    # Check at least one LLM key is available
    has_key = any(
        os.environ.get(k)
        for k in ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    )
    if not has_key:
        print(
            "WARNING: No default LLM key found (OPENROUTER_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY).\n"
            "         The agent will only review tasks where the poster or freelancer has provided a key."
        )


def review_deliverable(task_id: int, deliverable_id: int) -> dict:
    """Run the reviewer graph for a single task/deliverable pair."""
    from graph import app

    initial_state = {
        "task_id": task_id,
        "deliverable_id": deliverable_id,
        "review_scores": {},
        "skip_review": False,
    }

    print(f"\n{'='*60}")
    print(f"Reviewing task {task_id} / deliverable {deliverable_id}")
    print(f"{'='*60}")

    result = app.invoke(initial_state)

    if result.get("error"):
        print(f"  [ERROR] {result['error']}")
    elif result.get("skip_review"):
        print(f"  [SKIP] No LLM key available — manual review required")
    else:
        verdict = result.get("verdict", "unknown")
        color = "\033[32m" if verdict == "pass" else "\033[31m"
        reset = "\033[0m"
        print(f"\n  Verdict: {color}{verdict.upper()}{reset}")
        print(f"  Feedback: {result.get('review_feedback', 'N/A')}")
        print(f"  Scores: {result.get('review_scores', {})}")
        print(f"  Model: {result.get('llm_model', 'N/A')}")
        print(f"  Key source: {result.get('key_source', 'none')}")
        if result.get("credits_paid"):
            print(f"  Credits paid: {result.get('credits_paid')} (task completed!)")

    return result


class ReviewerClient:
    """Client for interacting with the Oriexa API from the Reviewer Agent."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60.0,
        )

    def fetch_pending_reviews(self) -> list[dict]:
        """Poll the API for tasks with auto_review_enabled that are in 'delivered' status."""
        try:
            resp = self.http.get(
                "/api/v1/tasks",
                params={"status": "delivered", "limit": 50},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            print(f"  [poll] Failed to fetch tasks: {exc}")
            return []

        if not data.get("ok"):
            return []

        tasks = data.get("data", [])
        return [t for t in tasks if t.get("auto_review_enabled")]

    def get_deliverable_for_task(self, task_id: int) -> int | None:
        """Get the most recent submitted deliverable for a task."""
        try:
            resp = self.http.get(f"/api/v1/tasks/{task_id}")
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError:
            return None

        if not data.get("ok"):
            return None

        deliverables = data.get("data", {}).get("deliverables", [])
        submitted = [d for d in deliverables if d.get("status") == "submitted"]
        if submitted:
            return submitted[-1]["id"]
        return None

    def close(self):
        self.http.close()


def run_daemon(interval: int = 30) -> None:
    """Poll for pending deliverables and review them."""
    client = ReviewerClient(
        base_url=os.environ["ORIEXA_BASE_URL"],
        api_key=os.environ["ORIEXA_REVIEWER_API_KEY"],
    )

    print(f"\nStarting Reviewer Agent daemon (polling every {interval}s)")
    print(f"Base URL: {client.base_url}")
    print("Press Ctrl+C to stop.\n")

    reviewed_pairs: set[tuple[int, int]] = set()

    try:
        while True:
            try:
                pending_tasks = client.fetch_pending_reviews()
                if pending_tasks:
                    print(f"[daemon] Found {len(pending_tasks)} task(s) awaiting review")
                    for task in pending_tasks:
                        task_id = task["id"]
                        deliverable_id = client.get_deliverable_for_task(task_id)
                        if not deliverable_id:
                            continue
                        pair = (task_id, deliverable_id)
                        if pair not in reviewed_pairs:
                            review_deliverable(task_id, deliverable_id)
                            reviewed_pairs.add(pair)
                else:
                    # Clearer presence log
                    t_now = time.strftime("%H:%M:%S")
                    print(f"[{t_now}] [daemon] No pending reviews. Sleeping {interval}s...")

                time.sleep(interval)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[daemon] Unexpected iteration error: {exc}")
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\nReviewer Agent stopping...")
    finally:
        client.close()
        print("Reviewer Agent stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Oriexa Reviewer Agent — LangGraph-powered automated deliverable review"
    )
    parser.add_argument("--task-id", type=int, help="Task ID to review (one-shot mode)")
    parser.add_argument(
        "--deliverable-id", type=int, help="Deliverable ID to review (one-shot mode)"
    )
    parser.add_argument(
        "--daemon", action="store_true", help="Run in daemon mode (polls for new deliverables)"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("POLL_INTERVAL", "30")),
        help="Poll interval in seconds (daemon mode, default: 30)",
    )
    args = parser.parse_args()

    validate_env()

    if args.daemon:
        run_daemon(args.interval)
    elif args.task_id and args.deliverable_id:
        result = review_deliverable(args.task_id, args.deliverable_id)
        # Exit with 0 on pass, 1 on fail, 2 on skip/error
        if result.get("verdict") == "pass":
            sys.exit(0)
        elif result.get("verdict") == "fail":
            sys.exit(1)
        else:
            sys.exit(2)
    else:
        print("Usage:")
        print("  One-shot: python run.py --task-id 42 --deliverable-id 8")
        print("  Daemon:   python run.py --daemon [--interval 60]")
        sys.exit(1)


if __name__ == "__main__":
    main()
