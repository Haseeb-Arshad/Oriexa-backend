"""
Oriexa Live Demo
==================
Step-by-step agent workflow:
  1. Start FastAPI orchestrator (port 8000)
  2. Wait for Next.js (port 3000)
  3. Register poster account
  4. Register orchestrator worker agent  ->  ORIEXA_API_KEY
  5. Restart FastAPI with the new API key so the daemon activates
  6. Post a Vanilla JS + HTML task (via MCP-compatible REST calls)
  7. Watch the agent work in real time (triage → plan → code → deploy → deliver)
  8. Print the final deliverable (GitHub repo + Vercel live URL)

Run via:  start.bat   (recommended)
      or:  .venv\Scripts\python.exe -X utf8 demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import string
import subprocess
import sys
import time
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT      = Path(__file__).parent
ENV_FILE  = ROOT / ".env"
NEXT_URL  = "http://localhost:3000"
API_URL   = f"{NEXT_URL}/api/v1"
AUTH_URL  = f"{NEXT_URL}/api/auth"
ORCH_URL  = "http://127.0.0.1:8000"       # direct to FastAPI (no proxy)

TASK_TITLE = "Build a Vanilla JS + HTML portfolio landing page"
TASK_DESCRIPTION = """\
Build a beautiful, responsive personal portfolio landing page using ONLY
vanilla JavaScript and HTML/CSS (no frameworks or bundlers).

Requirements:
- Hero section: full-screen with animated name/title and a call-to-action button
- About section: short bio with a profile picture placeholder
- Skills section: grid of skill cards (HTML, CSS, JS, Python, React, Node.js)
- Projects section: 3 project cards with title, description, links
- Contact form: name, email, message with basic JS validation
- Dark / light mode toggle: button in the navbar, persisted to localStorage
- Smooth scroll navigation with active-link highlighting
- CSS animations: fade-in on scroll, hover effects on cards
- Fully responsive (mobile-first, works on 320 px–1440 px)
- Pure vanilla JS only — Tailwind CSS via CDN is allowed for styling

Deliverables:
1. All source files pushed to a GitHub repository
2. Deployed to Vercel — return the live URL and the GitHub repo URL
""".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divider(char: str = "=", width: int = 62) -> str:
    return char * width


def step_banner(n: int, title: str) -> None:
    print(f"\n{_divider()}")
    print(f"  Step {n}: {title}")
    print(_divider())


def info(msg: str) -> None:
    print(f"    {msg}")


def ok(msg: str) -> None:
    print(f"  [OK]  {msg}")


def warn(msg: str) -> None:
    print(f"  [!!]  {msg}")


def fail(msg: str) -> None:
    print(f"  [XX]  {msg}")


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env_key(key: str, value: str) -> None:
    text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    pattern = rf"^{re.escape(key)}=.*$"
    new_line = f"{key}={value}"
    if re.search(pattern, text, re.MULTILINE):
        text = re.sub(pattern, new_line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{new_line}\n"
    ENV_FILE.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# FastAPI process management
# ---------------------------------------------------------------------------

_fastapi_proc: subprocess.Popen | None = None   # noqa: F841 (module-level)


def _kill_port(port: int) -> None:
    """Kill any process listening on the given port (Windows)."""
    subprocess.run(
        [
            "powershell", "-Command",
            f"Get-NetTCPConnection -LocalPort {port} -State Listen "
            f"-ErrorAction SilentlyContinue | ForEach-Object {{ "
            f"Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}",
        ],
        capture_output=True,
        text=True,
    )


def start_fastapi(env_override: dict[str, str] | None = None) -> subprocess.Popen:
    """Launch FastAPI as a child process and return the Popen object."""
    global _fastapi_proc
    env = os.environ.copy()
    env.update(read_env())
    if env_override:
        env.update(env_override)

    _fastapi_proc = subprocess.Popen(
        [str(ROOT / ".venv/Scripts/python.exe"), "-m", "uvicorn",
         "app.main:app", "--port", "8000"],
        cwd=str(ROOT),
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,   # Windows: own group
    )
    return _fastapi_proc


def stop_fastapi() -> None:
    """Gracefully stop the FastAPI subprocess and kill port 8000."""
    global _fastapi_proc
    if _fastapi_proc and _fastapi_proc.poll() is None:
        try:
            _fastapi_proc.terminate()
            _fastapi_proc.wait(timeout=5)
        except Exception:
            pass
    _kill_port(8000)
    _fastapi_proc = None


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------

async def wait_for(url: str, label: str, timeout: int = 45) -> bool:
    """Poll URL until HTTP 2xx/3xx or timeout. Returns True on success."""
    print(f"    Waiting for {label}", end="", flush=True)
    async with httpx.AsyncClient() as client:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = await client.get(url, timeout=3)
                if r.status_code < 500:
                    print(" ready!", flush=True)
                    return True
            except Exception:
                pass
            print(".", end="", flush=True)
            await asyncio.sleep(1.5)
    print(" TIMED OUT", flush=True)
    return False


async def rest(
    method: str,
    url: str,
    *,
    api_key: str | None = None,
    json_body: dict | None = None,
    timeout: int = 20,
) -> tuple[int, dict]:
    """Simple HTTP call. Returns (status_code, response_dict)."""
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient() as client:
        fn = getattr(client, method.lower())
        kwargs: dict = {"headers": headers, "timeout": timeout}
        if json_body is not None:
            kwargs["json"] = json_body
        r = await fn(url, **kwargs)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    return r.status_code, body


# ---------------------------------------------------------------------------
# Account / agent helpers
# ---------------------------------------------------------------------------

async def register_user(name: str, email: str, password: str) -> bool:
    code, _ = await rest("post", f"{AUTH_URL}/register",
                         json_body={"name": name, "email": email, "password": password})
    return code in (200, 201, 409)   # 409 = already exists, that's fine


async def create_agent(
    name: str, email: str, password: str, description: str,
) -> tuple[int | None, str | None]:
    """Register user + create agent. Returns (agent_id, api_key) or (None, None)."""
    await register_user(name, email, password)
    code, body = await rest(
        "post", f"{API_URL}/agents",
        json_body={"email": email, "password": password, "name": name, "description": description},
    )
    if code not in (200, 201):
        return None, None
    data = body.get("data", {})
    return data.get("agent_id"), data.get("api_key")


def _random_suffix(n: int = 6) -> str:
    return "".join(random.choices(string.digits, k=n))


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

async def post_task(
    api_key: str,
    title: str,
    description: str,
    budget: int = 250,
    category_id: int = 1,
) -> int | None:
    code, body = await rest(
        "post", f"{API_URL}/tasks",
        api_key=api_key,
        json_body={
            "title": title,
            "description": description,
            "budget_credits": budget,
            "category_id": category_id,
            "max_revisions": 2,
        },
    )
    if code in (200, 201):
        return body["data"]["id"]
    warn(f"Task creation failed ({code}): {body}")
    return None


async def trigger_task(task_id: int) -> int | None:
    """Manually trigger orchestration for a task. Returns execution_id."""
    code, body = await rest("post", f"{ORCH_URL}/orchestrator/tasks/{task_id}/start")
    if code == 200:
        return body.get("data", {}).get("execution_id")
    return None


async def get_pending_claims(task_id: int, poster_key: str) -> list[dict]:
    """Get pending claims on a task (visible to the poster)."""
    code, body = await rest("get", f"{API_URL}/tasks/{task_id}/claims", api_key=poster_key)
    if code == 200:
        items = body.get("data", [])
        return [c for c in items if c.get("status") == "pending"]
    return []


async def accept_claim(task_id: int, poster_key: str, claim_id: int) -> bool:
    """Poster accepts a pending claim — moves task to 'claimed' status."""
    code, _ = await rest(
        "post", f"{API_URL}/tasks/{task_id}/claims/accept",
        api_key=poster_key,
        json_body={"claim_id": claim_id},
    )
    return code == 200


async def get_task_status(task_id: int, api_key: str) -> str:
    code, body = await rest("get", f"{API_URL}/tasks/{task_id}", api_key=api_key)
    if code == 200:
        return body["data"]["status"]
    return "unknown"


async def get_active_execution(task_id: int) -> int | None:
    """Ask the orchestrator for the active execution ID for a task."""
    code, body = await rest("get", f"{ORCH_URL}/orchestrator/tasks/by-task/{task_id}/active")
    if code == 200:
        data = body.get("data") or {}
        return data.get("execution_id")
    return None


async def get_progress_steps(execution_id: int) -> list[dict]:
    code, body = await rest("get", f"{ORCH_URL}/orchestrator/progress/executions/{execution_id}")
    if code == 200:
        return body.get("data", {}).get("steps", [])
    return []


async def get_deliverable(task_id: int, api_key: str) -> dict | None:
    code, body = await rest("get", f"{API_URL}/tasks/{task_id}/deliverables", api_key=api_key)
    if code == 200:
        items = body.get("data", [])
        return items[0] if items else None
    return None


# ---------------------------------------------------------------------------
# Live monitoring
# ---------------------------------------------------------------------------

async def monitor(task_id: int, poster_key: str, timeout_secs: int = 900) -> str:
    """
    Poll task status + orchestrator progress until done.
    Prints live progress lines. Returns final task status.
    """
    last_status  = "open"
    last_step_n  = 0
    exec_id: int | None = None
    start = time.time()

    print()
    info("Monitoring task — dots = polling, [ORCH] lines = agent progress...")
    print()

    while time.time() - start < timeout_secs:
        await asyncio.sleep(4)

        # ── task status ──────────────────────────────────────────────────
        status = await get_task_status(task_id, poster_key)
        if status != last_status:
            elapsed = int(time.time() - start)
            print(f"\n  [{elapsed:4d}s]  status: {last_status} -> {status}")
            last_status = status

        if status in ("completed", "delivered"):
            print()
            break

        # ── orchestrator progress ─────────────────────────────────────────
        if exec_id is None:
            exec_id = await get_active_execution(task_id)

        if exec_id is not None:
            steps = await get_progress_steps(exec_id)
            if len(steps) > last_step_n:
                for s in steps[last_step_n:]:
                    phase   = s.get("phase", "")
                    stage   = s.get("title", "")
                    detail  = s.get("detail", "") or s.get("description", "")
                    pct     = s.get("progress_pct", 0)
                    print(f"\n  [ORCH {pct:3d}%]  [{phase:18s}]  {detail[:70]}")
                last_step_n = len(steps)
        else:
            print(".", end="", flush=True)

    return last_status


# ---------------------------------------------------------------------------
# Main demo flow
# ---------------------------------------------------------------------------

async def main() -> None:
    print()
    print(_divider())
    print("  Oriexa  —  AI Agent Live Demo")
    print("  Vanilla JS + HTML Portfolio  |  GitHub + Vercel Deploy")
    print(_divider())

    # ── Step 1: Next.js ────────────────────────────────────────────────────
    step_banner(1, "Checking Next.js (port 3000)")
    if not await wait_for(f"{NEXT_URL}/api/health", "Next.js", timeout=15):
        # Try a simpler health check
        if not await wait_for(NEXT_URL, "Next.js homepage", timeout=15):
            fail("Next.js is not running on port 3000.")
            info("Start it with:  cd Oriexa && npm run dev")
            sys.exit(1)
    ok("Next.js is up")

    # ── Step 2: Register poster ────────────────────────────────────────────
    step_banner(2, "Registering poster account")
    poster_email = f"demo-poster-{_random_suffix()}@oriexa.dev"
    poster_pass  = "P0sterDem0!2025"
    poster_id, poster_key = await create_agent(
        "Demo Poster", poster_email, poster_pass,
        "Human user posting coding tasks on the Oriexa demo.",
    )
    if not poster_key:
        fail("Could not create poster account")
        sys.exit(1)
    ok(f"Poster account created  ->  agent_id={poster_id}")
    ok(f"Poster API key          ->  {poster_key[:20]}...")

    # ── Step 3: Register orchestrator worker ──────────────────────────────
    step_banner(3, "Registering orchestrator worker agent")
    orch_email = f"orch-worker-{_random_suffix()}@oriexa.dev"
    orch_pass  = "0rch3str8tor!2025"
    orch_description = (
        "AI orchestrator agent. Builds, tests, and deploys software. "
        "Specialises in web development (Vanilla JS, HTML/CSS, React, Next.js). "
        "Delivers GitHub repository + Vercel live URL."
    )
    orch_id, orch_key = await create_agent(
        "Haseeb Orchestrator", orch_email, orch_pass, orch_description,
    )
    if not orch_key:
        fail("Could not create orchestrator agent")
        sys.exit(1)
    ok(f"Orchestrator agent created  ->  agent_id={orch_id}")
    ok(f"Orchestrator API key        ->  {orch_key[:20]}...")

    # ── Step 4: Write ORIEXA_API_KEY + (re)start FastAPI ────────────────
    step_banner(4, "Activating orchestrator daemon")
    current_key = read_env().get("ORIEXA_API_KEY", "")
    need_restart = (current_key != orch_key)

    write_env_key("ORIEXA_API_KEY", orch_key)
    ok(f"ORIEXA_API_KEY written to .env")

    # Check if FastAPI is already up
    api_up = False
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{ORCH_URL}/health", timeout=3)
            api_up = (r.status_code == 200)
    except Exception:
        pass

    if api_up and need_restart:
        info("Restarting FastAPI to load the new API key...")
        stop_fastapi()
        await asyncio.sleep(2)
        start_fastapi()
        if not await wait_for(f"{ORCH_URL}/health", "FastAPI (restart)", timeout=40):
            fail("FastAPI failed to restart")
            sys.exit(1)
        ok("FastAPI restarted with orchestrator key")
    elif not api_up:
        info("Starting FastAPI orchestrator...")
        start_fastapi()
        if not await wait_for(f"{ORCH_URL}/health", "FastAPI", timeout=40):
            fail("FastAPI failed to start")
            sys.exit(1)
        ok("FastAPI started")
    else:
        ok("FastAPI already running with correct key")

    # Give the daemon a moment to initialise
    info("Waiting 3 s for the task-picker daemon to register its webhook...")
    await asyncio.sleep(3)

    # ── Step 5: Post the task ──────────────────────────────────────────────
    step_banner(5, "Posting the task via MCP-compatible REST API")
    info(f"Title:    {TASK_TITLE}")
    info(f"Budget:   250 credits  |  Category: Coding (id=1)")
    info("")
    task_id = await post_task(
        api_key=poster_key,
        title=TASK_TITLE,
        description=TASK_DESCRIPTION,
        budget=250,
        category_id=1,
    )
    if not task_id:
        fail("Task creation failed — check the server logs")
        sys.exit(1)
    ok(f"Task posted  ->  task_id={task_id}")
    info(f"Dashboard:  {NEXT_URL}/dashboard")
    info(f"Orchestrator preview:  {ORCH_URL}/dashboard")

    # ── Step 6: Trigger / wait for orchestrator claim ─────────────────────
    step_banner(6, "Waiting for orchestrator to claim the task")
    info("The daemon polls every 30 s. Triggering immediately via API...")
    await asyncio.sleep(2)

    exec_id = await trigger_task(task_id)
    if exec_id:
        ok(f"Task triggered  ->  execution_id={exec_id}")
    else:
        info("Manual trigger returned nothing — daemon will pick it up on the next poll")
        info("Watching for autonomous claim...")

    # ── Poster accepts the orchestrator's claim ────────────────────────────
    # The task must be "claimed" (not just "open") before the agent can deliver.
    info("Poster accepting the orchestrator's pending claim...")
    for attempt in range(10):          # poll up to 10 × 3 s = 30 s
        await asyncio.sleep(3)
        pending = await get_pending_claims(task_id, poster_key)
        if pending:
            cid = pending[0]["id"]
            if await accept_claim(task_id, poster_key, cid):
                ok(f"Claim accepted  ->  claim_id={cid}  (task is now 'claimed')")
            else:
                warn("accept_claim returned non-200 — task may still be open")
            break
    else:
        warn("No pending claim found after 30 s — orchestrator may not have claimed yet")

    # ── Step 7: Live monitoring ────────────────────────────────────────────
    step_banner(7, "Live agent progress (triage -> plan -> code -> deploy -> deliver)")
    info("This usually takes 3-8 minutes for a Vanilla JS project.")
    info(f"Watch in browser:  {ORCH_URL}/dashboard")
    info("")

    final_status = await monitor(task_id, poster_key, timeout_secs=900)

    # ── Step 8: Show deliverable ───────────────────────────────────────────
    step_banner(8, "Deliverable")

    if final_status not in ("completed", "delivered"):
        warn(f"Task ended with status: {final_status} (may still be in progress)")
        info(f"Check dashboard: {NEXT_URL}/dashboard")
    else:
        ok(f"Task status: {final_status}")

    deliverable = await get_deliverable(task_id, poster_key)
    if deliverable:
        content = deliverable.get("content", "")
        ok("Deliverable received!")
        print()

        # Extract URLs from the content
        github_urls = re.findall(r'https?://github\.com/[^\s\)\"\']+', content)
        vercel_urls = re.findall(r'https?://[^\s\)\"\']*(vercel\.app|vercel\.com)[^\s\)\"\']*', content)

        if github_urls:
            ok(f"GitHub  ->  {github_urls[0]}")
        if vercel_urls:
            ok(f"Vercel  ->  {vercel_urls[0][0] if isinstance(vercel_urls[0], tuple) else vercel_urls[0]}")

        print()
        print("  " + _divider("-", 56))
        print("  DELIVERABLE CONTENT:")
        print("  " + _divider("-", 56))
        preview = content[:1200]
        for line in preview.splitlines():
            print(f"  {line}")
        if len(content) > 1200:
            print(f"\n  ... ({len(content) - 1200} more chars — check the dashboard)")
        print()
    else:
        info("No deliverable found yet (task may still be running).")
        info(f"Check:  {NEXT_URL}/dashboard  or  task_id={task_id}")

    print()
    print(_divider())
    print(f"  Demo complete!  task_id={task_id}")
    print(f"  Dashboard:           {NEXT_URL}/dashboard")
    print(f"  Orchestrator:        {ORCH_URL}/dashboard")
    print(_divider())
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user.")
