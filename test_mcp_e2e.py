"""
Oriexa MCP End-to-End Test
=============================
Tests ALL 21 MCP tools step-by-step in the correct agent workflow order.

Pre-requisites:
  - Next.js app running on localhost:3000
  - oriexa-api running on localhost:8001 (with MCP mounted at /mcp/)

Flow:
  1.  Bootstrap: register two users + two agents via REST (poster & worker)
  2.  Poster creates tasks via MCP create_task
  3.  Worker browses/searches tasks via MCP browse_tasks / search_tasks
  4.  Worker claims main task via MCP claim_task
  5.  Poster lists claims and accepts via MCP list_task_claims / accept_claim
  6.  Worker checks assigned tasks, submits deliverable via MCP submit_deliverable
  7.  Poster requests revision -> worker resubmits (request_revision)
  8.  Poster accepts deliverable -> credits flow (accept_deliverable)
  9.  Both agents check credits via MCP get_my_credits
  10. Webhook register/list/delete via MCP
  11. Rollback test (separate task) via MCP rollback_task
  12. Bulk claims test (3 tasks) via MCP bulk_claim_tasks
  13. Profile update + agent lookup via MCP

Usage:
    python -X utf8 test_mcp_e2e.py
    python -X utf8 test_mcp_e2e.py --next-url http://localhost:3000
"""
from __future__ import annotations

import argparse
import asyncio
import random
import string
import sys
from collections import Counter
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_NEXT_URL = "http://localhost:3000"

PASS_SYM = "[OK]  "
FAIL_SYM = "[FAIL]"

results: list[tuple[str, bool, str]] = []


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def rand_str(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n))


def log(label: str, ok: bool, detail: str = "") -> None:
    icon = PASS_SYM if ok else FAIL_SYM
    line = f"  {icon}  {label}"
    if detail:
        line += f"  ->  {detail}"
    print(line)
    results.append((label, ok, detail))


def assert_ok(label: str, resp: dict, extra: str = "") -> bool:
    ok = isinstance(resp, dict) and resp.get("ok") is True
    msg = extra or ""
    if not ok:
        err = resp.get("error", {}) if isinstance(resp, dict) else resp
        msg = err.get("message", str(resp)) if isinstance(err, dict) else str(err)
    log(label, ok, msg)
    return ok


def section(title: str) -> None:
    print(f"\n-- {title} {'-' * max(0, 55 - len(title))}")


# ---------------------------------------------------------------------------
# Bootstrap (REST, not MCP — creates fresh user+agent each run)
# ---------------------------------------------------------------------------

async def bootstrap_user_agent(next_url: str, suffix: str) -> tuple[str, int]:
    """
    Register a user then register an agent for that user.
    Returns (api_key, agent_id).

    POST /api/auth/register  -> creates user (no auth required)
    POST /api/v1/agents      -> creates agent using email+password inline auth
    """
    async with httpx.AsyncClient(base_url=next_url, timeout=30) as c:
        email = f"mcp_{suffix}_{rand_str()}@test.local"
        password = "TestPass123!"
        name = f"MCPUser_{suffix}_{rand_str(4)}"

        # 1) Create user account
        r = await c.post("/api/auth/register", json={
            "email": email, "password": password, "name": name,
        })
        body = r.json()
        if r.status_code not in (200, 201) or "id" not in body:
            raise RuntimeError(f"User registration failed ({r.status_code}): {body}")

        # 2) Register agent (email+password auth is built into the endpoint)
        r2 = await c.post("/api/v1/agents", json={
            "email": email,
            "password": password,
            "name": f"MCPAgent_{suffix}_{rand_str(4)}",
            "description": f"Automated MCP e2e test agent ({suffix}) - created by test_mcp_e2e.py",
            "capabilities": ["python", "testing"],
        })
        body2 = r2.json()
        if not body2.get("ok"):
            raise RuntimeError(f"Agent registration failed ({r2.status_code}): {body2}")

        api_key: str = body2["data"]["api_key"]
        agent_id: int = body2["data"]["agent_id"]
        return api_key, agent_id


# ---------------------------------------------------------------------------
# Main test suite
# ---------------------------------------------------------------------------

async def run_tests(next_url: str) -> int:
    api_base = f"{next_url}/api/v1"

    print()
    print("=" * 65)
    print(" Oriexa MCP End-to-End Test")
    print("=" * 65)
    print(f" Next.js  : {next_url}")
    print(f" API base : {api_base}")
    print()

    # Point the MCP HTTP client at our Next.js API
    import os
    os.environ["ORIEXA_API_BASE_URL"] = api_base

    from oriexa_mcp.server import (
        _client as mcp_http,
        browse_tasks,
        search_tasks,
        get_task,
        list_task_claims,
        list_task_deliverables,
        create_task,
        claim_task,
        bulk_claim_tasks,
        submit_deliverable,
        accept_claim,
        accept_deliverable,
        request_revision,
        rollback_task,
        get_my_profile,
        update_my_profile,
        get_my_claims,
        get_my_tasks,
        get_my_credits,
        get_agent_profile,
        register_webhook,
        list_webhooks,
        delete_webhook,
    )
    # Override base URL in case it was already cached from a previous import
    mcp_http._base_url = api_base
    await mcp_http.start()

    # -----------------------------------------------------------------------
    # Bootstrap
    # -----------------------------------------------------------------------
    section("Bootstrap")
    try:
        poster_key, poster_id = await bootstrap_user_agent(next_url, "poster")
        print(f"  {PASS_SYM}  Poster registered  ->  agent_id={poster_id}")
    except Exception as e:
        print(f"  {FAIL_SYM}  Poster registration FAILED: {e}")
        await mcp_http.close()
        return 1

    try:
        worker_key, worker_id = await bootstrap_user_agent(next_url, "worker")
        print(f"  {PASS_SYM}  Worker registered  ->  agent_id={worker_id}")
    except Exception as e:
        print(f"  {FAIL_SYM}  Worker registration FAILED: {e}")
        await mcp_http.close()
        return 1

    # -----------------------------------------------------------------------
    # 1. Agent profile tools
    # -----------------------------------------------------------------------
    section("1. Agent Profile")

    r = await get_my_profile(api_key=worker_key)
    if assert_ok("get_my_profile (worker)", r):
        operator = r["data"].get("operator", {})
        bal = operator.get("credit_balance", r["data"].get("credit_balance", "?"))
        log("  credit_balance after registration", True, f"{bal} credits")

    r = await get_my_profile(api_key=poster_key)
    assert_ok("get_my_profile (poster)", r)

    r = await update_my_profile(
        api_key=worker_key,
        name=f"WorkerAgent_{rand_str(4)}",
        description="Automated MCP test worker - updated via update_my_profile tool",
        capabilities=["python", "testing", "automation"],
        hourly_rate_credits=50,
    )
    assert_ok("update_my_profile (name, description, capabilities, hourly_rate)", r)

    r = await get_agent_profile(api_key=worker_key, agent_id=poster_id)
    assert_ok("get_agent_profile (worker looks up poster by ID)", r)

    r = await get_my_credits(api_key=worker_key)
    if assert_ok("get_my_credits (initial state)", r):
        balance = r["data"].get("balance", r["data"].get("credit_balance", "?"))
        txns = len(r["data"].get("transactions", []))
        log("  initial balance", True, f"{balance} credits, {txns} transaction(s)")

    # -----------------------------------------------------------------------
    # 2. Task creation (poster side)
    # -----------------------------------------------------------------------
    section("2. Task Creation")

    r = await create_task(
        api_key=poster_key,
        title=f"[MCP-TEST] Email validator Python function {rand_str(4)}",
        description=(
            "Write a Python function validate_email(email) that uses regex to "
            "validate email addresses. Return True for valid, False for invalid. "
            "Handle None and empty string inputs gracefully."
        ),
        budget_credits=150,
        category_id=1,
        requirements="Must include type hints, docstring, and 3 usage examples.",
        max_revisions=2,
    )
    if not assert_ok("create_task (main task, budget=150, category=Coding)", r):
        print("  Cannot continue without a task - aborting")
        await mcp_http.close()
        return 1
    main_task_id: int = r["data"]["id"]
    log("  main task_id", True, str(main_task_id))

    r2 = await create_task(
        api_key=poster_key,
        title=f"[MCP-TEST] Rollback target {rand_str(4)}",
        description="Simple task used to test the rollback endpoint in the MCP e2e test.",
        budget_credits=50,
        category_id=7,
    )
    rollback_task_id: Optional[int] = r2["data"]["id"] if r2.get("ok") else None
    log("create_task (rollback target)", bool(rollback_task_id), f"task_id={rollback_task_id}")

    bulk_task_ids: list[int] = []
    for i in range(3):
        rb = await create_task(
            api_key=poster_key,
            title=f"[MCP-TEST] Bulk claim target {i+1} {rand_str(4)}",
            description=f"Bulk-claims test task #{i+1}. Write a short Python utility.",
            budget_credits=30 + i * 10,
            category_id=1,
        )
        if rb.get("ok"):
            bulk_task_ids.append(rb["data"]["id"])
    log("create_task x3 (bulk claim targets)", len(bulk_task_ids) == 3,
        f"ids={bulk_task_ids}")

    # -----------------------------------------------------------------------
    # 3. Browse and search
    # -----------------------------------------------------------------------
    section("3. Browse & Search")

    r = await browse_tasks(api_key=worker_key, status="open", sort="newest", limit=5)
    if assert_ok("browse_tasks (status=open, sort=newest, limit=5)", r):
        cnt = r["meta"].get("count", 0)
        log("  tasks returned in page", cnt > 0, f"{cnt} task(s)")

    r = await browse_tasks(
        api_key=worker_key, status="open", category=1, min_budget=100, limit=10
    )
    if assert_ok("browse_tasks (category=1 Coding, min_budget=100)", r):
        cnt = r["meta"].get("count", 0)
        log("  coding tasks with budget >= 100", cnt >= 1, f"{cnt} task(s)")

    r = await search_tasks(api_key=worker_key, q="python email validation", limit=5)
    if assert_ok("search_tasks (q='python email validation')", r):
        cnt = r["meta"].get("count", 0)
        log("  relevance-ranked results returned", cnt >= 1, f"{cnt} match(es)")

    r = await get_task(api_key=worker_key, task_id=main_task_id)
    if assert_ok(f"get_task (id={main_task_id})", r):
        status = r["data"].get("status", "?")
        budget = r["data"].get("budget_credits", "?")
        log("  task status=open", status == "open", f"status={status}, budget={budget}")

    # -----------------------------------------------------------------------
    # 4. Claim task (worker side)
    # -----------------------------------------------------------------------
    section("4. Claim Task")

    r = await claim_task(
        api_key=worker_key,
        task_id=main_task_id,
        proposed_credits=140,
        message=(
            "I'll deliver this with full type hints, docstring, and edge-case "
            "handling for None/empty inputs. Regex tested against RFC 5322."
        ),
    )
    if not assert_ok("claim_task (worker bids on main task)", r):
        await mcp_http.close()
        return 1
    claim_id: int = r["data"]["id"]
    log("  claim_id created", True, str(claim_id))
    log("  claim status=pending", r["data"].get("status") == "pending",
        r["data"].get("status", "?"))

    r = await list_task_claims(api_key=poster_key, task_id=main_task_id)
    if assert_ok("list_task_claims (poster reviews bids)", r):
        bids = r["data"]
        proposed = bids[0].get("proposed_credits", "?") if bids else "?"
        log("  claim visible to poster", len(bids) >= 1,
            f"{len(bids)} bid(s), worker proposed {proposed} credits")

    r = await get_my_claims(api_key=worker_key)
    if assert_ok("get_my_claims (worker sees own claims)", r):
        pending = [c for c in r["data"] if c.get("status") == "pending"]
        log("  pending claim in list", len(pending) >= 1,
            f"{len(pending)} pending claim(s)")

    # -----------------------------------------------------------------------
    # 5. Accept claim (poster side)
    # -----------------------------------------------------------------------
    section("5. Accept Claim")

    r = await accept_claim(api_key=poster_key, task_id=main_task_id, claim_id=claim_id)
    if not assert_ok("accept_claim (poster accepts worker's bid)", r):
        await mcp_http.close()
        return 1
    log("  task assigned to worker",
        r["data"].get("agent_id") == worker_id,
        f"agent_id={r['data'].get('agent_id')}")

    r = await get_task(api_key=worker_key, task_id=main_task_id)
    if assert_ok("get_task (status after accept_claim)", r):
        log("  status changed to claimed", r["data"]["status"] == "claimed",
            r["data"]["status"])

    r = await get_my_tasks(api_key=worker_key)
    if assert_ok("get_my_tasks (worker sees active task)", r):
        has_task = any(t["id"] == main_task_id for t in r["data"])
        log("  main task appears in active tasks", has_task)

    # -----------------------------------------------------------------------
    # 6. Submit deliverable (worker side)
    # -----------------------------------------------------------------------
    section("6. Submit Deliverable")

    content_v1 = (
        "## Email Validator\n\n"
        "```python\n"
        "import re\n"
        "from typing import Optional\n\n"
        "def validate_email(email: Optional[str]) -> bool:\n"
        '    """\n'
        "    Validate an email address.\n\n"
        "    Args:\n"
        "        email: Email string to validate, or None.\n\n"
        "    Returns:\n"
        "        True if valid, False otherwise.\n\n"
        "    Examples:\n"
        '        >>> validate_email("user@example.com")\n'
        "        True\n"
        '        >>> validate_email("bad-email")\n'
        "        False\n"
        '        >>> validate_email(None)\n'
        "        False\n"
        '    """\n'
        "    if not email:\n"
        "        return False\n"
        "    pattern = r'^[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}$'\n"
        "    return bool(re.match(pattern, email.strip()))\n"
        "```\n"
    )

    r = await submit_deliverable(
        api_key=worker_key,
        task_id=main_task_id,
        content=content_v1,
    )
    if not assert_ok("submit_deliverable (v1 - initial submission)", r):
        await mcp_http.close()
        return 1
    deliverable_id: int = r["data"]["id"]
    rev_num = r["data"].get("revision_number", "?")
    log("  deliverable_id assigned", True, str(deliverable_id))
    log("  revision_number starts at 0", rev_num == 0, str(rev_num))

    r = await get_task(api_key=poster_key, task_id=main_task_id)
    if assert_ok("get_task (after submit - should be delivered)", r):
        log("  status=delivered", r["data"]["status"] == "delivered",
            r["data"]["status"])

    r = await list_task_deliverables(api_key=poster_key, task_id=main_task_id)
    if assert_ok("list_task_deliverables (poster reviews submitted work)", r):
        log("  deliverable visible", len(r["data"]) >= 1,
            f"{len(r['data'])} item(s)")

    # -----------------------------------------------------------------------
    # 7. Revision cycle
    # -----------------------------------------------------------------------
    section("7. Revision Cycle")

    r = await request_revision(
        api_key=poster_key,
        task_id=main_task_id,
        deliverable_id=deliverable_id,
        revision_notes=(
            "Good start! Please also add a unittest.TestCase subclass with "
            "at least 5 test cases covering valid emails, None, empty string, "
            "and invalid formats."
        ),
    )
    if assert_ok("request_revision (poster requests tests be added)", r):
        log("  deliverable status=revision_requested",
            r["data"].get("status") == "revision_requested",
            r["data"].get("status", "?"))

    r = await get_task(api_key=worker_key, task_id=main_task_id)
    if assert_ok("get_task (back to in_progress after revision request)", r):
        log("  status=in_progress", r["data"]["status"] == "in_progress",
            r["data"]["status"])

    # Worker resubmits with unit tests added
    content_v2 = (
        content_v1
        + "\n## Unit Tests\n\n"
        "```python\n"
        "import unittest\n\n"
        "class TestValidateEmail(unittest.TestCase):\n"
        '    def test_valid_simple(self):\n'
        '        self.assertTrue(validate_email("user@example.com"))\n\n'
        '    def test_valid_subdomain(self):\n'
        '        self.assertTrue(validate_email("a.b+tag@sub.domain.org"))\n\n'
        '    def test_invalid_no_at(self):\n'
        '        self.assertFalse(validate_email("notanemail"))\n\n'
        '    def test_none_input(self):\n'
        "        self.assertFalse(validate_email(None))\n\n"
        '    def test_empty_string(self):\n'
        '        self.assertFalse(validate_email(""))\n\n'
        '    def test_whitespace_only(self):\n'
        '        self.assertFalse(validate_email("   "))\n'
        "```\n"
    )
    r = await submit_deliverable(
        api_key=worker_key,
        task_id=main_task_id,
        content=content_v2,
    )
    if assert_ok("submit_deliverable (v2 - with unit tests)", r):
        deliverable_id = r["data"]["id"]   # update to latest deliverable
        rev = r["data"].get("revision_number", "?")
        log("  revision_number incremented to 1", rev == 1, str(rev))

    # -----------------------------------------------------------------------
    # 8. Accept deliverable -> credits flow
    # -----------------------------------------------------------------------
    section("8. Accept Deliverable (Credits Flow)")

    r = await accept_deliverable(
        api_key=poster_key,
        task_id=main_task_id,
        deliverable_id=deliverable_id,
    )
    if not assert_ok("accept_deliverable (poster approves v2)", r):
        await mcp_http.close()
        return 1

    credits_paid = r["data"].get("credits_paid", "?")
    platform_fee = r["data"].get("platform_fee", "?")
    log("  credits_paid to worker operator", True,
        f"{credits_paid} credits  (fee={platform_fee})")

    expected_pay = 150 - int(150 * 0.10)   # = 135
    log("  payment = budget minus 10% platform fee",
        credits_paid == expected_pay,
        f"expected {expected_pay}, got {credits_paid}")

    r = await get_task(api_key=worker_key, task_id=main_task_id)
    if assert_ok("get_task (status after accept_deliverable)", r):
        log("  status=completed", r["data"]["status"] == "completed",
            r["data"]["status"])

    r = await get_my_credits(api_key=worker_key)
    if assert_ok("get_my_credits (worker after payment)", r):
        balance = r["data"].get("balance", r["data"].get("credit_balance", "?"))
        txns = r["data"].get("transactions", [])
        payment_txn = next((t for t in txns if t.get("type") == "payment"), None)
        log("  worker balance updated", True, f"{balance} credits")
        log("  payment ledger entry exists", payment_txn is not None,
            f"amount={payment_txn.get('amount') if payment_txn else 'MISSING'}")

    # -----------------------------------------------------------------------
    # 9. Webhook tools
    # -----------------------------------------------------------------------
    section("9. Webhooks")

    r = await register_webhook(
        api_key=worker_key,
        url="https://webhook.site/mcp-test-e2e",
        events=["claim.accepted", "deliverable.accepted", "deliverable.revision_requested"],
        secret="mcp_test_hmac_secret_xyz",
    )
    wh_id: Optional[int] = None
    if assert_ok("register_webhook (3 events, with secret)", r):
        wh_id = r["data"]["id"]
        evts = r["data"].get("events", [])
        log("  webhook_id assigned", True, str(wh_id))
        log("  3 events subscribed", len(evts) == 3, str(evts))

    r = await list_webhooks(api_key=worker_key)
    if assert_ok("list_webhooks (worker sees registered webhooks)", r):
        cnt = len(r["data"])
        log("  webhook visible in list", cnt >= 1, f"{cnt} webhook(s)")

    if wh_id:
        r = await delete_webhook(api_key=worker_key, webhook_id=wh_id)
        if assert_ok("delete_webhook (remove by ID)", r):
            r2 = await list_webhooks(api_key=worker_key)
            remaining = [w for w in r2.get("data", []) if w.get("id") == wh_id]
            log("  webhook removed from list", len(remaining) == 0,
                f"{len(remaining)} remaining with that ID")
    else:
        log("delete_webhook", False, "skipped (webhook_id missing)")

    # -----------------------------------------------------------------------
    # 10. Rollback task
    # -----------------------------------------------------------------------
    section("10. Rollback Task")

    if rollback_task_id:
        r = await claim_task(
            api_key=worker_key,
            task_id=rollback_task_id,
            proposed_credits=50,
            message="Test claim for rollback flow",
        )
        rb_claim_id: Optional[int] = r["data"]["id"] if r.get("ok") else None

        if rb_claim_id:
            r = await accept_claim(
                api_key=poster_key,
                task_id=rollback_task_id,
                claim_id=rb_claim_id,
            )
            assert_ok("accept_claim (rollback test task)", r)

            r = await rollback_task(api_key=poster_key, task_id=rollback_task_id)
            if assert_ok("rollback_task (poster cancels assignment)", r):
                prev = r["data"].get("previous_status", "?")
                new_s = r["data"].get("status", "?")
                log("  task reverted to open", new_s == "open",
                    f"{prev} -> {new_s}")

            r = await get_task(api_key=worker_key, task_id=rollback_task_id)
            if assert_ok("get_task (confirm status after rollback)", r):
                log("  status=open confirmed",
                    r["data"]["status"] == "open",
                    r["data"]["status"])
        else:
            log("rollback_task", False, "claim step failed")
    else:
        log("rollback_task", False, "skipped (rollback task creation failed)")

    # -----------------------------------------------------------------------
    # 11. Bulk claims
    # -----------------------------------------------------------------------
    section("11. Bulk Claims")

    if len(bulk_task_ids) == 3:
        bulk_payload = [
            {
                "task_id": tid,
                "proposed_credits": 25,
                "message": f"Bulk claim #{i+1} from MCP e2e test",
            }
            for i, tid in enumerate(bulk_task_ids)
        ]
        r = await bulk_claim_tasks(api_key=worker_key, claims=bulk_payload)
        if assert_ok("bulk_claim_tasks (3 tasks in one request)", r):
            summary = r["data"].get("summary", {})
            succeeded = summary.get("succeeded", 0)
            failed = summary.get("failed", 0)
            results_list = r["data"].get("results", [])
            log("  all 3 claims succeeded", succeeded == 3,
                f"succeeded={succeeded}, failed={failed}")
            log("  3 result items in response", len(results_list) == 3,
                str(len(results_list)))
    else:
        log("bulk_claim_tasks", False,
            f"skipped (only {len(bulk_task_ids)} bulk tasks available)")

    # -----------------------------------------------------------------------
    # 12. Final state check
    # -----------------------------------------------------------------------
    section("12. Final State Check")

    r = await get_my_profile(api_key=worker_key)
    if assert_ok("get_my_profile (worker - final)", r):
        completed = r["data"].get("tasks_completed", "?")
        reputation = r["data"].get("reputation_score", "?")
        log("  tasks_completed = 1 (main task done)", completed == 1, str(completed))
        log("  reputation_score present", reputation != "?", str(reputation))

    r = await get_my_claims(api_key=worker_key)
    if assert_ok("get_my_claims (all claims overview)", r):
        all_claims = r["data"]
        status_counts = dict(Counter(c.get("status") for c in all_claims))
        log("  all claims returned", len(all_claims) > 0,
            f"total={len(all_claims)}, statuses={status_counts}")

    r = await get_my_credits(api_key=poster_key)
    if assert_ok("get_my_credits (poster - final)", r):
        balance = r["data"].get("balance", r["data"].get("credit_balance", "?"))
        log("  poster balance visible", True, f"{balance} credits")

    await mcp_http.close()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print()
    print("=" * 65)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    pct = int(100 * passed / total) if total else 0
    print(f" Results : {passed}/{total} passed  ({pct}%)")
    if failed:
        print(f" Failed  : {failed}")
    print("=" * 65)

    if failed > 0:
        print("\n Failed checks:")
        for label, ok, detail in results:
            if not ok:
                print(f"   {FAIL_SYM}  {label}  {detail}")
    print()

    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Oriexa MCP End-to-End Test")
    parser.add_argument(
        "--next-url",
        default=DEFAULT_NEXT_URL,
        help=f"Next.js app base URL (default: {DEFAULT_NEXT_URL})",
    )
    args = parser.parse_args()
    rc = asyncio.run(run_tests(args.next_url))
    sys.exit(rc)
