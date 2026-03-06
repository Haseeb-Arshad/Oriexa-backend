#!/usr/bin/env python3
"""
Oriexa Swarm Orchestrator — Multi-Agent Dispatcher

Central dispatcher that:
  1. Polls the Oriexa API for the agent's current state
  2. Determines which specialized agent to trigger based on priorities:
     - Revision Agent  → for tasks with revision requests (highest priority)
     - Worker Agent    → for accepted/claimed tasks needing deliverables
     - Scout Agent     → for browsing and claiming new tasks (lowest priority)
  3. Spawns the appropriate sub-agent as a subprocess
  4. Monitors results and logs activity

Usage:
    python scripts/swarm.py --api-key <key> [--interval 10] [--base-url http://...]

Architecture inspired by agentswarm/packages/orchestrator/ patterns:
  - Orchestrator dispatches to specialized workers
  - Each sub-agent is one-shot (does its job then exits)
  - Orchestrator tracks state across cycles
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from agents.base_agent import (
    BASE_URL,
    ANTHROPIC_KEY,
    DEFAULT_CAPABILITIES,
    OriexaClient,
    iso_to_datetime,
    log_act,
    log_err,
    log_ok,
    log_think,
    log_wait,
    log_warn,
)

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_INTERVAL = 10    # seconds between orchestrator ticks
MAX_CONCURRENT_TASKS = 3 # how many tasks to work on simultaneously

ORCH = "Orchestrator"

# Paths to sub-agent scripts
SCRIPT_DIR = Path(__file__).parent / "agents"
SCOUT_SCRIPT = SCRIPT_DIR / "scout_agent.py"
CODER_SCRIPT = SCRIPT_DIR / "coder_agent.py"
TESTER_SCRIPT = SCRIPT_DIR / "tester_agent.py"
DEPLOY_SCRIPT = SCRIPT_DIR / "deploy_agent.py"
REVISION_SCRIPT = SCRIPT_DIR / "revision_agent.py"
WORKSPACE_DIR = Path(os.environ.get("AGENT_WORKSPACE_DIR", str(Path(__file__).parent / "agent_works")))
LOCK_TIMEOUT = 2400  # 40 minutes — agents should finish within this window


def acquire_lock(task_dir: Path, agent_name: str) -> bool:
    """Acquire a lock on a task directory. Returns True if lock acquired."""
    lock_file = task_dir / ".agent_lock"
    if lock_file.exists():
        try:
            lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
            lock_age = time.time() - lock_data.get("timestamp", 0)
            if lock_age < LOCK_TIMEOUT:
                log_warn(
                    f"Task dir locked by {lock_data.get('agent', '?')} "
                    f"({int(lock_age)}s ago) — skipping",
                    ORCH
                )
                return False
            # Stale lock — override it
            log_warn(f"Stale lock found ({int(lock_age)}s old) — overriding", ORCH)
        except Exception:
            pass

    lock_file.write_text(
        json.dumps({"agent": agent_name, "pid": os.getpid(), "timestamp": time.time()}),
        encoding="utf-8"
    )
    return True


def release_lock(task_dir: Path):
    """Release the lock on a task directory."""
    lock_file = task_dir / ".agent_lock"
    if lock_file.exists():
        try:
            lock_file.unlink()
        except Exception:
            pass


def log_dispatch(task_dir: Path, agent_name: str, result: dict):
    """Append an entry to the task's dispatch log."""
    log_file = task_dir / ".dispatch_log"
    timestamp = datetime.now(timezone.utc).isoformat()
    action = result.get("action", "unknown") if isinstance(result, dict) else "unknown"
    entry = f"[{timestamp}] {agent_name}: {action}\n"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# SUB-AGENT RUNNER
# ═══════════════════════════════════════════════════════════════════════════

def run_sub_agent(
    script: Path,
    api_key: str,
    base_url: str,
    extra_args: list[str] | None = None,
    timeout: int = 1800,
) -> dict:
    """
    Run a sub-agent as a subprocess and capture its result.
    
    Sub-agents print their result as: __RESULT__:<json>
    """
    cmd = [
        sys.executable, str(script),
        "--api-key", api_key,
        "--base-url", base_url,
    ]
    if extra_args:
        cmd.extend(extra_args)

    agent_name = script.stem.replace("_agent", "").title()
    log_act(f"Dispatching {agent_name} Agent...", ORCH)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=str(SCRIPT_DIR.parent),
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
        )

        # Print sub-agent output (for visibility)
        if proc.stdout:
            for line in proc.stdout.strip().splitlines():
                if not line.startswith("__RESULT__:"):
                    print(f"    {line}", flush=True)

        if proc.stderr:
            for line in proc.stderr.strip().splitlines():
                print(f"    [stderr] {line}", flush=True)

        # Parse result
        for line in (proc.stdout or "").splitlines():
            if line.startswith("__RESULT__:"):
                try:
                    return json.loads(line[len("__RESULT__:"):])
                except json.JSONDecodeError:
                    pass

        if proc.returncode != 0:
            log_warn(f"{agent_name} Agent exited with code {proc.returncode}", ORCH)

        return {"action": "no_result", "exit_code": proc.returncode}

    except subprocess.TimeoutExpired:
        log_err(f"{agent_name} Agent timed out after {timeout}s", ORCH)
        return {"action": "timeout"}
    except Exception as e:
        log_err(f"Failed to run {agent_name} Agent: {e}", ORCH)
        return {"action": "error", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════

class SwarmOrchestrator:
    """Central dispatcher that routes work to specialized sub-agents."""

    def __init__(
        self,
        client: OriexaClient,
        api_key: str,
        base_url: str,
        capabilities: list[str],
        interval: int = 10,
        max_active: int = 10,
    ):
        self.client = client
        self.api_key = api_key
        self.base_url = base_url
        self.capabilities = capabilities
        self.interval = interval
        self.max_active = max_active

        # Tracking
        self.claimed_task_ids: set[int] = set()
        self.tasks_completed = 0
        self.agents_dispatched = 0
        self.cycle_count = 0
        self.sub_agent_running = False

    def _load_existing_state(self):
        """Load existing claims and task state on startup."""
        try:
            claims = self.client.get_my_claims()
            for claim in claims:
                status = claim.get("status", "")
                task_id = claim.get("task_id") or (claim.get("task") or {}).get("id")
                task_status = claim.get("task_status", "")
                if task_id and status in ("pending", "accepted"):
                    self.claimed_task_ids.add(task_id)
                    # If the task is still "claimed" (not yet started), resume it
                    if status == "accepted" and task_status in ("claimed", "in_progress"):
                        log_think(
                            f"Resuming task #{task_id} (claim=accepted, task_status={task_status})",
                            ORCH
                        )
            if self.claimed_task_ids:
                log_ok(f"Loaded {len(self.claimed_task_ids)} existing pending/accepted claim(s)", ORCH)
        except Exception as e:
            log_warn(f"Could not load existing claims: {e}", ORCH)

    def _wait_for_backend(self, max_retries: int = 20, wait: int = 5) -> dict:
        """Wait for backend to be reachable and authenticate. Retries with backoff."""
        for attempt in range(1, max_retries + 1):
            try:
                profile = self.client.get_profile()
                if profile:
                    return profile
                log_warn(
                    f"Auth failed (attempt {attempt}/{max_retries}) — retrying in {wait}s...",
                    ORCH,
                )
            except Exception as e:
                log_warn(
                    f"Backend not reachable (attempt {attempt}/{max_retries}): {e} — retrying in {wait}s...",
                    ORCH,
                )
            time.sleep(wait)

        log_err("Failed to connect to backend after all retries — check API key and backend status", ORCH)
        sys.exit(1)

    def run(self):
        """Main orchestrator loop."""
        profile = self._wait_for_backend()

        print(f"\n{'='*60}")
        print(f"  Oriexa Swarm Orchestrator")
        print(f"  Agent: {profile.get('name', 'Unknown')} (ID: {profile.get('id', '?')})")
        print(f"  Capabilities: {self.capabilities}")
        print(f"  Poll interval: {self.interval}s")
        print(f"  Server: {self.base_url}")
        print(f"  Sub-agents: Scout, Coder, Tester, Deployer, Revision")
        print(f"{'='*60}\n")

        self._load_existing_state()

        while True:
            try:
                self._tick()
                log_wait(
                    f"Sleeping {self.interval}s... "
                    f"(cycle={self.cycle_count}, dispatched={self.agents_dispatched}, completed={self.tasks_completed})",
                    ORCH,
                )
                time.sleep(self.interval)
            except KeyboardInterrupt:
                print(f"\n\nSwarm stopped. Completed {self.tasks_completed} tasks. "
                      f"Dispatched {self.agents_dispatched} sub-agents across {self.cycle_count} cycles.")
                break
            except Exception as exc:
                log_err(f"Unexpected error: {exc}", ORCH)
                log_err(traceback.format_exc().strip().splitlines()[-1], ORCH)
                time.sleep(self.interval)

    def _tick(self):
        """One orchestrator dispatch cycle."""
        self.cycle_count += 1

        # ── Priority 1: Check for revision requests ──────────────────
        dispatched = self._check_revisions()
        if dispatched:
            return

        # ── Priority 2: Check for accepted tasks needing deliverables ─
        dispatched = self._check_work()
        if dispatched:
            return

        # ── Priority 3: Scout for new tasks (if below capacity) ──────
        self._check_scout()

    def _check_revisions(self) -> bool:
        """Check for tasks needing revision. Returns True if an agent was dispatched."""
        try:
            my_tasks = self.client.get_my_tasks()
        except Exception as e:
            log_warn(f"Failed to fetch my tasks: {e}", ORCH)
            return False

        revision_tasks = [
            t for t in my_tasks
            if t.get("status") == "in_progress"
        ]

        if not revision_tasks:
            return False

        for task_summary in revision_tasks:
            task_id = task_summary.get("id") or task_summary.get("task_id")
            log_think(f"Task #{task_id} is in_progress — checking for revision requests", ORCH)

            result = run_sub_agent(
                REVISION_SCRIPT,
                self.api_key,
                self.base_url,
                ["--task-id", str(task_id)],
            )
            self.agents_dispatched += 1

            if isinstance(result, dict) and result.get("action") == "revised":
                log_ok(f"Revision Agent submitted improved deliverable for task #{task_id}", ORCH)
                return True

        return False

    def _check_work(self) -> bool:
        """Check for accepted/claimed tasks needing CI/CD pipeline action. Returns True if dispatched."""
        try:
            accepted_claims = self.client.get_my_claims("accepted")
        except Exception as e:
            log_warn(f"Failed to fetch accepted claims: {e}", ORCH)
            return False

        if not accepted_claims:
            return False

        for claim in accepted_claims:
            task_id = claim.get("task_id") or (claim.get("task") or {}).get("id")
            if not task_id:
                continue

            task_status = claim.get("task_status", "")
            if task_status in ("completed", "delivered", "cancelled"):
                continue

            # Read local state to figure out pipeline stage
            task_dir = WORKSPACE_DIR / f"task_{task_id}"
            task_dir.mkdir(parents=True, exist_ok=True)
            state_file = task_dir / ".swarm_state.json"
            
            pipeline_stage = "coding"
            if state_file.exists():
                try:
                    with open(state_file, "r") as f:
                        state = json.load(f)
                        pipeline_stage = state.get("status", "coding")
                except Exception:
                    pass

            # Determine which agent to dispatch
            if pipeline_stage == "coding":
                agent_name = "Coder"
                script = CODER_SCRIPT
            elif pipeline_stage == "testing":
                agent_name = "Tester"
                script = TESTER_SCRIPT
            elif pipeline_stage == "deploying":
                agent_name = "Deployer"
                script = DEPLOY_SCRIPT
            else:
                log_warn(f"Unknown pipeline stage '{pipeline_stage}' for Task #{task_id}", ORCH)
                continue

            # Acquire lock before dispatching
            if not acquire_lock(task_dir, agent_name):
                continue  # Another agent is active on this task

            # Transition task to in_progress when starting coding (claimed → in_progress)
            if pipeline_stage == "coding" and task_status in ("claimed", ""):
                try:
                    start_resp = self.client.start_task(task_id)
                    if start_resp.get("ok"):
                        log_ok(f"Task #{task_id} transitioned → in_progress", ORCH)
                    # 400/409 means already started or wrong status — fine, continue
                except Exception as e:
                    log_warn(f"start_task failed for #{task_id}: {e} — continuing anyway", ORCH)

            log_think(f"Task #{task_id} stage='{pipeline_stage}' — dispatching {agent_name}", ORCH)

            try:
                result = run_sub_agent(
                    script,
                    self.api_key,
                    self.base_url,
                    ["--task-id", str(task_id)],
                )
                self.agents_dispatched += 1
                log_dispatch(task_dir, agent_name, result)
            finally:
                release_lock(task_dir)

            if isinstance(result, dict) and result.get("action") not in ("error", "no_result"):
                return True

        return False

    def _check_scout(self) -> bool:
        """Browse for new tasks if below capacity. Returns True if dispatched."""
        # Check capacity by TASK status, not claim status.
        # Claim status stays "accepted" forever even after a task is delivered/completed,
        # so counting accepted claims always grows and the agent gets permanently stuck.
        # Only tasks in "claimed" or "in_progress" still have active work to do.
        try:
            all_tasks = self.client.get_my_tasks()
            pending_claims = self.client.get_my_claims("pending")
            active_count = (
                sum(1 for t in all_tasks if t.get("status") in ("claimed", "in_progress"))
                + len(pending_claims)
            )
        except Exception as e:
            log_warn(f"Failed to check capacity: {e}", ORCH)
            return False

        if active_count >= self.max_active:
            log_wait(f"At capacity ({active_count}/{self.max_active} active tasks)", ORCH)
            return False

        log_think(f"Below capacity ({active_count}/{self.max_active}) — dispatching Scout", ORCH)

        result = run_sub_agent(
            SCOUT_SCRIPT,
            self.api_key,
            self.base_url,
            ["--capabilities", ",".join(self.capabilities)],
        )
        self.agents_dispatched += 1

        if isinstance(result, dict) and result.get("action") == "claimed":
            task_id = result.get("task_id")
            self.claimed_task_ids.add(task_id)
            log_ok(f"Scout Agent claimed task #{task_id}!", ORCH)
            return True

        return False


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Oriexa Swarm Orchestrator")
    parser.add_argument("--api-key", type=str, required=True,
                       help="Agent API key")
    parser.add_argument("--name", type=str, default="SwarmAgent",
                       help="Agent name for display")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                       help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("--capabilities", type=str,
                       default=",".join(DEFAULT_CAPABILITIES),
                       help="Comma-separated capabilities")
    parser.add_argument("--base-url", type=str, default=BASE_URL,
                       help="Oriexa API base URL")
    parser.add_argument("--dry-run", action="store_true",
                       help="Run one tick without dispatching sub-agents (for testing)")
    args = parser.parse_args()

    # Validate
    if not ANTHROPIC_KEY:
        print("FATAL: ANTHROPIC_KEY not set in environment. Cannot run swarm.")
        sys.exit(1)

    capabilities = [c.strip() for c in args.capabilities.split(",")]
    client = OriexaClient(args.base_url, args.api_key)

    orchestrator = SwarmOrchestrator(
        client=client,
        api_key=args.api_key,
        base_url=args.base_url,
        capabilities=capabilities,
        interval=args.interval,
        max_active=MAX_CONCURRENT_TASKS,
    )

    if args.dry_run:
        log_ok("Dry-run mode — running one tick", ORCH)
        profile = client.get_profile()
        if profile:
            log_ok(f"Authenticated as: {client.agent_name} (ID: {client.agent_id})", ORCH)
        else:
            log_err("Authentication FAILED", ORCH)
            sys.exit(1)
        orchestrator._load_existing_state()
        orchestrator._tick()
        log_ok("Dry-run complete", ORCH)
    else:
        orchestrator.run()


if __name__ == "__main__":
    main()
