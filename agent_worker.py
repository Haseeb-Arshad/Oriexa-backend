#!/usr/bin/env python3
"""
Oriexa Autonomous Worker Agent

A persistent agent bot that:
  1. Registers itself (or uses existing API key)
  2. Polls for new open tasks
  3. Uses LLM to evaluate which tasks to claim
  4. Claims the best matching task
  5. Generates deliverables using LLM
  6. Submits work
  7. Handles revision requests
  8. Loops back to find more work

Usage:
    # Auto-register and start working
    python scripts/agent-worker.py

    # Use existing API key
    python scripts/agent-worker.py --api-key th_agent_abc123...

    # Custom poll interval
    python scripts/agent-worker.py --interval 15

    # Specify capabilities for better task matching
    python scripts/agent-worker.py --capabilities python,javascript,sql
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Force UTF-8 on Windows
# if sys.platform == "win32":
#     sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
#     sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")
load_dotenv(Path(__file__).parent.parent / "reviewer-agent" / ".env")

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

BASE_URL = os.environ.get("ORIEXA_BASE_URL", os.environ.get("NEXTAUTH_URL", "http://localhost:3000"))
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")

DEFAULT_CAPABILITIES = ["nextjs", "react", "vite", "javascript", "typescript", "tailwindcss", "frontend", "web-development"]
DEFAULT_INTERVAL = 20  # seconds between polls
MAX_CONCURRENT_TASKS = 3  # tasks to work on at once
MAX_REMARKS_PER_TASK = 4  # max feedback remarks per agent per task (initial + follow-ups)

# Pipeline sub-agent scripts (mirrors swarm.py)
SCRIPT_DIR = Path(__file__).parent / "agents"
WORKSPACE_DIR = Path(__file__).parent.parent / "agent_works"
CODER_SCRIPT = SCRIPT_DIR / "coder_agent.py"
TESTER_SCRIPT = SCRIPT_DIR / "tester_agent.py"
DEPLOY_SCRIPT = SCRIPT_DIR / "deploy_agent.py"
REVISION_SCRIPT = SCRIPT_DIR / "revision_agent.py"
LOCK_TIMEOUT = 2400  # 40 minutes — agents should finish within this window

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════

def log(icon: str, msg: str, **kwargs):
    ts = datetime.now().strftime("%H:%M:%S")
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
    print(f"  [{ts}] {icon} {msg} {extra}", flush=True)

def log_think(msg: str, **kw): log("THINK", msg, **kw)
def log_act(msg: str, **kw):   log("ACT  ", msg, **kw)
def log_ok(msg): print(f"\033[32m[OK]     {msg}\033[0m", flush=True)
def log_warn(msg: str, **kw):  log("WARN ", msg, **kw)
def log_err(msg: str, **kw):   log("ERROR", msg, **kw)
def log_wait(msg: str, **kw):  log(" ... ", msg, **kw)


def iso_to_datetime(iso_str: str | None) -> datetime | None:
    """Safely convert ISO string (with Z or +00:00) to datetime object."""
    if not iso_str:
        return None
    try:
        # Standardize 'Z' to '+00:00' for fromisoformat compatibility in < 3.11
        clean_str = iso_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE HELPERS (run coder/deploy sub-agents, mirrors swarm.py)
# ═══════════════════════════════════════════════════════════════════════════

def _acquire_lock(task_dir: Path, agent_name: str) -> bool:
    """Acquire a per-task lock. Returns True if acquired."""
    lock_file = task_dir / ".agent_lock"
    if lock_file.exists():
        try:
            lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
            lock_age = time.time() - lock_data.get("timestamp", 0)
            if lock_age < LOCK_TIMEOUT:
                log_warn(f"Task dir locked by {lock_data.get('agent', '?')} ({int(lock_age)}s ago) — skipping")
                return False
        except Exception:
            pass
    lock_file.write_text(
        json.dumps({"agent": agent_name, "pid": os.getpid(), "timestamp": time.time()}),
        encoding="utf-8",
    )
    return True


def _release_lock(task_dir: Path):
    lock_file = task_dir / ".agent_lock"
    try:
        lock_file.unlink(missing_ok=True)
    except Exception:
        pass


def _run_pipeline_agent(script: Path, api_key: str, base_url: str, task_id: int, timeout: int = 1800) -> dict:
    """
    Run a pipeline sub-agent (coder / deploy / revision) as a subprocess.
    Prints its output live and returns the JSON result emitted on __RESULT__: line.
    """
    cmd = [
        sys.executable, str(script),
        "--api-key", api_key,
        "--base-url", base_url,
        "--task-id", str(task_id),
    ]
    agent_name = script.stem.replace("_agent", "").title()
    log_act(f"Dispatching {agent_name} Agent for task #{task_id}...")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=str(script.parent.parent),  # scripts/
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
        )

        for line in (proc.stdout or "").strip().splitlines():
            if not line.startswith("__RESULT__:"):
                print(f"    {line}", flush=True)
        for line in (proc.stderr or "").strip().splitlines():
            print(f"    [stderr] {line}", flush=True)

        # Extract __RESULT__ JSON
        for line in (proc.stdout or "").splitlines():
            if line.startswith("__RESULT__:"):
                try:
                    return json.loads(line[len("__RESULT__:"):])
                except json.JSONDecodeError:
                    pass

        if proc.returncode != 0:
            log_warn(f"{agent_name} Agent exited with code {proc.returncode}")
        return {"action": "no_result", "exit_code": proc.returncode}

    except subprocess.TimeoutExpired:
        log_err(f"{agent_name} Agent timed out after {timeout}s for task #{task_id}")
        return {"action": "timeout"}
    except Exception as exc:
        log_err(f"Failed to run {agent_name} Agent: {exc}")
        return {"action": "error", "error": str(exc)}


def _get_pipeline_stage(task_dir: Path) -> str:
    """Read the .swarm_state.json to determine the current pipeline stage."""
    state_file = task_dir / ".swarm_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            return state.get("status", "coding")
        except Exception:
            pass
    return "coding"


# ═══════════════════════════════════════════════════════════════════════════
# LLM CLIENT
# ═══════════════════════════════════════════════════════════════════════════

def llm_call(system: str, user: str, max_tokens: int = 2048) -> str:
    """Call Anthropic Claude Haiku directly via HTTP (no langchain dependency)."""
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def llm_json(system: str, user: str) -> dict:
    """LLM call that returns parsed JSON."""
    raw = llm_call(system, user, max_tokens=1024)
    # Try direct parse
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try finding JSON object
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"_raw": raw, "_parse_failed": True}


# ═══════════════════════════════════════════════════════════════════════════
# API CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class OriexaClient:
    """API client for Oriexa."""

    def __init__(self, base_url: str, api_key: str = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http = httpx.Client(base_url=self.base_url, timeout=3600.0)
        self.agent_id = None
        self.agent_name = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def get(self, path: str, params: dict = None) -> dict:
        resp = self.http.get(path, headers=self._headers(), params=params)
        return resp.json()

    def post(self, path: str, json_data: dict = None) -> dict:
        resp = self.http.post(path, headers=self._headers(), json=json_data)
        return resp.json()

    def register(self, name: str, capabilities: list[str]) -> str:
        """Register a new user + agent, return API key."""
        ts = int(time.time())
        email = f"worker-bot-{ts}@oriexa-agent.com"
        password = "AgentBot123!"

        # Register user
        resp = self.post("/api/auth/register", {
            "email": email,
            "password": password,
            "name": name,
        })
        if not isinstance(resp, dict):
            raise RuntimeError(f"Registration failed: {resp}")

        # Register agent
        resp = self.post("/api/v1/agents", {
            "email": email,
            "password": password,
            "name": name,
            "description": f"Autonomous AI worker agent. Capabilities: {', '.join(capabilities)}. "
                          "I automatically browse, claim, and deliver high-quality work on Oriexa tasks.",
            "capabilities": capabilities,
        })

        if not resp.get("ok"):
            raise RuntimeError(f"Agent registration failed: {resp}")

        self.api_key = resp["data"]["api_key"]
        self.agent_id = resp["data"].get("agent_id") or resp["data"].get("id")
        self.agent_name = name
        return self.api_key

    def browse_tasks(self, status: str = "open", limit: int = 20) -> list[dict]:
        """Browse available tasks."""
        resp = self.get("/api/v1/tasks", {"status": status, "limit": limit, "sort": "newest"})
        if resp.get("ok"):
            return resp.get("data", [])
        return []

    def get_task(self, task_id: int) -> dict | None:
        """Get full task details."""
        resp = self.get(f"/api/v1/tasks/{task_id}")
        if resp.get("ok"):
            return resp.get("data")
        return None

    def claim_task(self, task_id: int, proposed_credits: int, message: str) -> dict:
        """Submit a claim on a task."""
        return self.post(f"/api/v1/tasks/{task_id}/claims", {
            "proposed_credits": proposed_credits,
            "message": message,
        })

    def start_task(self, task_id: int) -> dict:
        """Mark a claimed task as in_progress (claimed → in_progress)."""
        return self.post(f"/api/v1/tasks/{task_id}/start", {})

    def submit_deliverable(self, task_id: int, content: str) -> dict:
        """Submit a deliverable for a task."""
        return self.post(f"/api/v1/tasks/{task_id}/deliverables", {
            "content": content,
        })

    def get_my_tasks(self, status: str = None) -> list[dict]:
        """Get tasks assigned to this agent."""
        params = {}
        if status:
            params["status"] = status
        resp = self.get("/api/v1/agents/me/tasks", params)
        if resp.get("ok"):
            return resp.get("data", [])
        return []

    def get_my_claims(self, status: str = None) -> list[dict]:
        """Get this agent's claims."""
        params = {}
        if status:
            params["status"] = status
        resp = self.get("/api/v1/agents/me/claims", params)
        if resp.get("ok"):
            return resp.get("data", [])
        return []

    def get_profile(self) -> dict | None:
        """Get agent profile."""
        resp = self.http.get("/api/v1/agents/me", headers=self._headers())
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                profile = data.get("data", {})
                self.agent_id = profile.get("id")
                self.agent_name = profile.get("name")
                return profile
        return None

    def get_task_messages(self, task_id: int) -> list[dict]:
        """Fetch conversation messages for a task (poster replies to agent feedback)."""
        resp = self.get(f"/api/v1/tasks/{task_id}/messages")
        if resp.get("ok"):
            data = resp.get("data", [])
            return data if isinstance(data, list) else []
        return []

    def post_remark(self, task_id: int, remark) -> dict:
        """Post a feedback remark on a task."""
        if isinstance(remark, str):
            payload = {"remark": remark}
        else:
            payload = remark
        return self.post(f"/api/v1/tasks/{task_id}/remarks", payload)


# ═══════════════════════════════════════════════════════════════════════════
# AGENT BRAIN — Think, Plan, Act
# ═══════════════════════════════════════════════════════════════════════════

class AgentBrain:
    """LLM-powered decision making for the agent."""

    def __init__(self, capabilities: list[str]):
        self.capabilities = capabilities

    def evaluate_task(self, task: dict, conv_messages: list[dict] | None = None) -> dict:
        """THINK: Should I claim this task? How much to bid?"""
        remarks = task.get("agent_remarks", [])
        remarks_history = ""
        has_answered_questions = False
        if remarks:
            remarks_history = "\nPrevious agent feedback on this task:\n"
            for r in remarks:
                remarks_history += f"- {r.get('agent_name')}: {r.get('remark')}\n"
                eval_data = r.get("evaluation")
                if eval_data:
                    for q in eval_data.get("questions", []):
                        if q.get("answer"):
                            has_answered_questions = True
                            remarks_history += f"  Q: {q['text']} -> Poster answered: {q['answer']}\n"
                        else:
                            remarks_history += f"  Q: {q['text']} -> (not answered yet)\n"

        # Include poster's free-form text messages in context
        if conv_messages:
            poster_msgs = [
                m for m in conv_messages
                if m.get("sender_type") == "poster" and m.get("message_type") == "text"
            ]
            if poster_msgs:
                has_answered_questions = True  # treat poster messages as clarification
                remarks_history += "\nPoster messages in chat:\n"
                for m in poster_msgs[-5:]:  # last 5 messages
                    remarks_history += f"  Poster: {m.get('content', '')}\n"

        # Build the system prompt — different for follow-up vs initial
        if has_answered_questions:
            system_prompt = (
                "You are an AI freelancer agent on Oriexa. The task poster has answered your evaluation questions. "
                f"Your capabilities: {', '.join(self.capabilities)}.\n\n"
                "STACK RESTRICTION: You ONLY work on frontend/web projects using Next.js, React, or Vite. "
                "If the task is NOT a frontend/web project, set should_claim=false.\n\n"
                "MANDATORY DECISION RULE:\n"
                "The poster has provided their answers. You now have enough information to start. "
                "You MUST set should_claim=true and confidence=high. This is non-negotiable.\n"
                "The ONLY reason NOT to claim is if the task requires skills you fundamentally don't have "
                "(e.g. they want hardware engineering and you only do software), or the budget is 0.\n\n"
                "OUTPUT RULES:\n"
                "- should_claim: true (mandatory)\n"
                "- confidence: 'high' (mandatory)\n"
                "- feedback: exactly ONE short sentence acknowledging their answers, e.g. 'Got it — starting on this right away.' "
                "No greetings. No restating the task. No enthusiasm filler.\n"
                "- questions: [] (empty — do NOT ask more questions)\n"
                "- approach: a concrete step-by-step plan based on all the information gathered\n"
            )
        else:
            system_prompt = (
                "You are an AI freelancer agent on Oriexa evaluating tasks to decide which to claim. "
                f"Your capabilities: {', '.join(self.capabilities)}. "
                "Be selective but pragmatic — claim tasks you can deliver quality work for. "
                "If a task has a clear title, reasonable description, and any stated requirements, that is usually ENOUGH to claim. "
                "Do NOT reject tasks for being 'too simple' or 'too small'. "
                "Only flag a task as vague if the description is truly insufficient to start work.\n\n"
                "STACK RESTRICTION: You ONLY work on frontend/web projects using Next.js, React, or Vite. "
                "If the task is NOT a frontend/web project (e.g. Python scripts, mobile apps, hardware), set should_claim=false.\n\n"
                "IMPORTANT: On this initial evaluation round, set should_claim=false. "
                "You are gathering requirements first. You will claim AFTER the poster answers your questions.\n\n"
                "FEEDBACK FIELD RULES (CRITICAL):\n"
                "- The 'feedback' field is shown to the poster BEFORE your questions appear in the UI\n"
                "- Keep it to ONE sentence stating intent: e.g. 'I can build this — a few quick questions:'\n"
                "- NO chatty greetings, NO 'Hey!', NO 'I took a look at', NO preambles\n"
                "- NO restating the task title back to the poster\n\n"
                "EVALUATION QUESTIONS — focus here. You have 4 question types:\n"
                "  1. 'multiple_choice' — MULTI-SELECT (poster can tick several options). Use for 'which features do you want?' or 'which of these apply?' with 3-6 options. Great for feature lists, tech preferences, or requirements.\n"
                "  2. 'yes_no' — For quick binary decisions (Need auth? Want dark mode?).\n"
                "  3. 'text_input' — For open-ended details (include a helpful placeholder).\n"
                "  4. 'scale' — For priorities on a spectrum (include scale_min, scale_max 1-5, scale_labels).\n\n"
                "Use 3-5 questions total. Mix types. Be specific, never generic.\n"
                "NEVER ask questions the description already answers.\n"
                "For 'multiple_choice', phrase questions as 'Which of the following...' or 'Select all that apply:' to hint at multi-select."
            )

        task_context = (
            f"Task title: {task.get('title', 'N/A')}\n"
            f"Description: {(task.get('description') or 'N/A')[:800]}\n"
            f"Budget: {task.get('budget_credits', 0)} credits\n"
            f"Requirements: {(task.get('requirements') or 'N/A')[:500]}\n"
            f"Category: {task.get('category', {}).get('name', 'General') if isinstance(task.get('category'), dict) else 'General'}\n"
            f"{remarks_history}"
        )

        if has_answered_questions:
            # Follow-up: poster answered — always claim, no more questions
            user_prompt = (
                f"{task_context}\n"
                "The poster has answered your questions. Respond with valid JSON:\n"
                '{\n'
                '  "should_claim": true,\n'
                '  "confidence": "high",\n'
                '  "proposed_credits": 100,\n'
                '  "is_vague": false,\n'
                '  "evaluation": {"score": 9, "strengths": ["Poster provided clear answers"], "concerns": [], "questions": []},\n'
                '  "feedback": "Got it — starting on this right away.",\n'
                '  "reason": "Poster answered all questions",\n'
                '  "approach": "Concrete step-by-step plan based on the answers provided."\n'
                '}\n'
                "Replace 100 with your actual proposed_credits. DO NOT change should_claim or confidence."
            )
        else:
            # Initial evaluation: ask questions to gather requirements — do NOT claim yet
            user_prompt = (
                f"Evaluate this task:\n{task_context}\n\n"
                "IMPORTANT: Set should_claim to FALSE. You are gathering requirements first.\n\n"
                "Respond with this JSON structure (use valid JSON only):\n"
                '{\n'
                '  "should_claim": false,\n'
                '  "confidence": "medium",\n'
                '  "proposed_credits": 50,\n'
                '  "is_vague": false,\n'
                '  "evaluation": {\n'
                '    "score": 7,\n'
                '    "strengths": ["strength1", "strength2"],\n'
                '    "concerns": ["concern1"],\n'
                '    "questions": [\n'
                '      {"id": "q1", "text": "Do you need user accounts?", "type": "yes_no"},\n'
                '      {"id": "q2", "text": "What vibe?", "type": "multiple_choice", "options": ["Minimal", "Bold", "Dark"]},\n'
                '      {"id": "q3", "text": "Describe your homepage", "type": "text_input", "placeholder": "e.g. A grid of cards..."},\n'
                '      {"id": "q4", "text": "How polished?", "type": "scale", "scale_min": 1, "scale_max": 5, "scale_labels": ["Prototype", "Production"]}\n'
                '    ]\n'
                '  },\n'
                '  "feedback": "ONE sentence stating intent, e.g. \'Ready to build this — just need a few details:\' NO greetings, NO restating the task title",\n'
                '  "reason": "internal reason",\n'
                '  "approach": "Detailed step-by-step plan: \'1. Set up project structure with X. 2. Implement Y using Z. 3. Add tests for A. 4. Deploy to B.\' Be concrete and technical."\n'
                '}'
            )

        result = llm_json(system_prompt, user_prompt)

        # Hard override for follow-up: if the poster answered our questions,
        # we MUST claim regardless of what the LLM said.
        if has_answered_questions:
            result["should_claim"] = True
            result["confidence"] = "high"
            if not result.get("feedback"):
                result["feedback"] = "Got it — starting on this right away."
            # Clear any lingering questions the LLM may have hallucinated
            eval_obj = result.get("evaluation")
            if isinstance(eval_obj, dict):
                eval_obj["questions"] = []
        else:
            # Initial evaluation: NEVER claim — feedback only
            result["should_claim"] = False
            # Ensure there are questions (if LLM forgot them)
            eval_obj = result.get("evaluation")
            if isinstance(eval_obj, dict) and not eval_obj.get("questions"):
                # Force default questions so the poster has something to answer
                eval_obj["questions"] = [
                    {"id": "q1", "text": "Do you need user accounts and login?", "type": "yes_no"},
                    {"id": "q2", "text": "What vibe are you going for with the design?", "type": "multiple_choice",
                     "options": ["Minimal & clean", "Bold & colorful", "Dark & sleek", "No preference"]},
                    {"id": "q3", "text": "Describe what the main page should look like in your own words", "type": "text_input",
                     "placeholder": "e.g. A list of items with a search bar, each showing a thumbnail..."},
                    {"id": "q4", "text": "How polished should the final product be?", "type": "scale",
                     "scale_min": 1, "scale_max": 5, "scale_labels": ["Quick prototype", "Production-ready"]},
                ]

        # Ensure evaluation exists — generate context-aware fallback if LLM omitted it
        if not result.get("evaluation") or not isinstance(result.get("evaluation"), dict):
            title = task.get("title", "this task")
            desc = (task.get("description") or "")[:200]
            budget = task.get("budget_credits", 0)

            strengths = []
            if title and title != "N/A":
                strengths.append("Clear title that communicates the goal well")
            if budget >= 50:
                strengths.append(f"Reasonable budget of {budget} credits for this scope")
            if desc and len(desc) > 50:
                strengths.append("Solid starting description to work from")

            concerns = []
            reqs = task.get("requirements") or ""
            if not reqs:
                concerns.append("No acceptance criteria yet — a few quick answers below will fix that")
            if len(desc) < 100:
                concerns.append("A bit more detail would help nail the deliverable on the first try")

            result["evaluation"] = {
                "score": 7 if has_answered_questions else (6 if len(desc) > 80 else 4),
                "strengths": (
                    ["Poster provided great answers to clarify the project", "Clear direction for implementation"]
                    if has_answered_questions
                    else strengths or ["The core concept is clear and achievable"]
                ),
                "concerns": (
                    []
                    if has_answered_questions
                    else concerns or ["A few quick clarifications would go a long way"]
                ),
                "questions": [] if has_answered_questions else [
                    {"id": "q1", "text": "Do you need user accounts and login?", "type": "yes_no"},
                    {"id": "q2", "text": "What vibe are you going for with the design?", "type": "multiple_choice",
                     "options": ["Minimal & clean", "Bold & colorful", "Dark & sleek", "No preference"]},
                    {"id": "q3", "text": "Describe what the main page should look like in your own words", "type": "text_input",
                     "placeholder": "e.g. A list of items with a search bar, each showing a thumbnail..."},
                    {"id": "q4", "text": "How polished should the final product be?", "type": "scale",
                     "scale_min": 1, "scale_max": 5, "scale_labels": ["Quick prototype", "Production-ready"]},
                ],
            }

        # Validate questions
        eval_obj = result.get("evaluation", {})
        if isinstance(eval_obj, dict) and "questions" in eval_obj:
            valid_questions = []
            for q in eval_obj.get("questions", []):
                if not isinstance(q, dict) or not q.get("text"):
                    continue
                qtype = q.get("type", "multiple_choice")
                if qtype == "multiple_choice" and len(q.get("options", [])) < 2:
                    continue
                if qtype not in ("multiple_choice", "yes_no", "text_input", "scale"):
                    q["type"] = "text_input"
                    q["placeholder"] = q.get("placeholder", "Type your answer...")
                valid_questions.append(q)
            eval_obj["questions"] = valid_questions

        # Ensure feedback is concise and direct (no chatty preambles)
        feedback = result.get("feedback", "")
        if not feedback or len(feedback) < 10:
            if has_answered_questions:
                result["feedback"] = "Got it — ready to start once the claim is accepted."
            else:
                result["feedback"] = "I can build this — a few quick questions before I dive in:"

        return result

    def generate_claim_message(self, task: dict, approach: str) -> str:
        """Generate a detailed, structured claim message with step-by-step approach."""
        return llm_call(
            "Write a professional, structured claim message for a freelance task on Oriexa. "
            "Format it as a numbered step-by-step implementation plan (4-6 steps). "
            "Each step should be concrete and specific to this task. "
            "Start with a single sentence summarising your qualifications, then list the numbered steps. "
            "NEVER use vague filler like 'I will deliver high-quality work'. Be technical and specific.",
            f"Task: {task.get('title')}\n"
            f"Description: {(task.get('description') or '')[:400]}\n"
            f"Requirements: {(task.get('requirements') or '')[:300]}\n"
            f"Planned approach: {approach}\n"
            f"My skills: {', '.join(self.capabilities)}\n\n"
            "Write the claim message with numbered steps. Example format:\n"
            "I specialise in [relevant skill] and will deliver this efficiently.\n"
            "1. [Specific first step]\n"
            "2. [Specific second step]\n"
            "3. [Specific third step]\n"
            "4. [Testing/QA step]\n"
            "5. [Delivery step]\n\n"
            "Write ONLY the claim message, nothing else.",
            max_tokens=400,
        ).strip()

    def generate_deliverable(self, task: dict) -> str:
        """ACT: Generate a plain-English delivery summary the client can understand."""
        title = task.get("title") or ""
        desc = task.get("description") or ""
        reqs = task.get("requirements") or ""

        return llm_call(
            "You are a professional software delivery agent writing a delivery summary for a client. "
            "You have completed the coding work. Now explain what was built in plain, friendly English. "
            "DO NOT include any code, code blocks, commands, or technical jargon. "
            "Write as if you are explaining to a business owner what they now have. "
            "Structure: a short 2–3 sentence overview of what was built, then a bullet list "
            "of the key features and functionality that is now available, then a brief closing note "
            "on how to access or use it (e.g. the live URL or repository — if known).",

            f"Task: {title}\n\n"
            f"Description:\n{desc}\n\n"
            f"Requirements:\n{reqs}\n\n"
            "Write the delivery summary now. No code, no commands — just clear English.",
            max_tokens=600,
        )

    def handle_revision(self, task: dict, deliverable: dict, feedback: str) -> str:
        """Handle a revision request, rewriting the delivery summary based on feedback."""
        title = task.get("title") or ""
        desc = (task.get("description") or "")[:500]
        reqs = (task.get("requirements") or "")[:300]
        prev = (deliverable.get("content") or "")[:2000]

        return llm_call(
            "You are a professional software delivery agent revising your delivery summary based on client feedback. "
            "Address every point in the feedback. DO NOT include any code, code blocks, or commands. "
            "Write the revised summary in plain, friendly English that a business owner can understand.",

            f"Task: {title}\n"
            f"Description: {desc}\n"
            f"Requirements: {reqs}\n\n"
            f"Previous delivery summary:\n{prev}\n\n"
            f"Client feedback:\n{feedback}\n\n"
            "Write the improved delivery summary now. No code — plain English only.",
            max_tokens=600,
        )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN AGENT LOOP
# ═══════════════════════════════════════════════════════════════════════════

class AutonomousWorkerAgent:
    """The main agent that runs the think-plan-act loop."""

    def __init__(self, client: OriexaClient, brain: AgentBrain, interval: int):
        self.client = client
        self.brain = brain
        self.interval = interval
        self.claimed_task_ids: set[int] = set()
        self.attempted_tasks: dict[int, datetime] = {}  # task_id -> updated_at when we last skipping/remarking
        self.tasks_completed = 0
        self.tasks_failed = 0

    def _load_existing_claims(self):
        """On startup, load pending/accepted claims so we don't double-claim."""
        try:
            claims = self.client.get_my_claims()
            for claim in claims:
                status = claim.get("status", "")
                task_id = claim.get("task_id") or (claim.get("task") or {}).get("id")
                if task_id and status in ("pending", "accepted"):
                    self.attempted_tasks[task_id] = datetime.now(timezone.utc)
                    self.claimed_task_ids.add(task_id)
            if self.claimed_task_ids:
                log_ok(f"Loaded {len(self.claimed_task_ids)} existing pending/accepted claim(s)")
        except Exception as e:
            log_warn(f"Could not load existing claims: {e}")

    def run(self):
        """Main agent loop."""
        profile = self.client.get_profile()
        if profile:
            print(f"\n{'='*60}")
            print(f"  Oriexa Autonomous Worker Agent")
            print(f"  Name: {profile.get('name', 'Unknown')}")
            print(f"  Agent ID: {profile.get('id', '?')}")
            print(f"  Capabilities: {profile.get('capabilities', [])}")
            print(f"  Reputation: {profile.get('reputation', 0)}")
            print(f"  Poll interval: {self.interval}s")
            print(f"  Server: {self.client.base_url}")
            print(f"{'='*60}\n")

        # Load existing claims so we don't double-claim after restart
        self._load_existing_claims()

        while True:
            try:
                self._tick()
                log_wait(f"Sleeping {self.interval}s... (completed={self.tasks_completed})")
                time.sleep(self.interval)
            except KeyboardInterrupt:
                print(f"\n\nAgent stopped. Completed {self.tasks_completed} tasks.")
                break
            except Exception as exc:
                log_err(f"Unexpected error: {exc}")
                time.sleep(self.interval)

    def _tick(self):
        """One cycle of the agent loop."""
        # Step 1: Check for tasks needing revision (in_progress tasks assigned to us)
        self._check_revisions()

        # Step 2: Work on accepted claims — generate and submit deliverables
        self._work_on_claimed_tasks()

        # Step 3: Check if we have capacity for new tasks.
        # IMPORTANT: claim status stays "accepted" forever — even after the task is
        # "delivered" or "completed". So we must count by TASK status, not claim status.
        # Only tasks in "claimed" or "in_progress" still have active work remaining.
        # "delivered" = agent submitted, waiting for poster review → slot is free.
        # "completed" / "cancelled" = fully done → slot is free.
        try:
            all_my_tasks = self.client.get_my_tasks()
            active_count = sum(
                1 for t in all_my_tasks
                if t.get("status") in ("claimed", "in_progress")
            )
        except Exception:
            active_count = 0

        if active_count >= MAX_CONCURRENT_TASKS:
            log_wait(f"Working on {active_count} active task(s), at capacity — skipping new task browse")
            return

        # Step 4: Browse for new open tasks
        log_think("Browsing for open tasks...")
        open_tasks = self.client.browse_tasks("open", limit=20) # Balanced limit

        if not open_tasks:
            log_wait("No open tasks available")
            return

        log_think(f"Found {len(open_tasks)} open task(s)")
        log_think(f"Task IDs: {[t.get('id') for t in open_tasks[:5]]}...")

        # Step 4: Evaluate each task and pick the best one
        best_task = None
        best_evaluation = None

        for task_summary in open_tasks:
            task_id = task_summary.get("id")
            if not task_id:
                continue

            # Don't skip claimed tasks — we still need to check for feedback responses
            # Just mark them so we don't try to claim again
            is_claimed = task_id in self.claimed_task_ids

            # Check browse-level updated_at to see if it's worth fetching details
            current_updated_at = iso_to_datetime(task_summary.get("updated_at"))
            last_seen_at = self.attempted_tasks.get(task_id)

            # Skip ONLY if we've seen this exact version of the task before
            if last_seen_at and current_updated_at and last_seen_at >= current_updated_at:
                continue

            # Get full task details
            try:
                detail = self.client.get_task(task_id)
            except Exception as e:
                log_warn(f"Failed to fetch task #{task_id}: {e}")
                continue
            if not detail:
                continue

            # Update our "last seen" mark
            task_updated = iso_to_datetime(detail.get("updated_at"))
            self.attempted_tasks[task_id] = task_updated or datetime.now(timezone.utc)

            # Skip check: Have we already left a remark on this VERSION?
            remarks = detail.get("agent_remarks", [])
            my_remarks = [r for r in remarks if r.get("agent_id") == self.client.agent_id]

            if my_remarks:
                # Find the LATEST remark by this agent
                latest_remark = max(my_remarks, key=lambda r: r.get("timestamp", ""))
                remark_time = iso_to_datetime(latest_remark.get("timestamp"))

                # If the task hasn't been updated since our last remark, skip
                if task_updated and remark_time and remark_time >= task_updated:
                    # Hard cap: if we've already left MAX_REMARKS_PER_TASK remarks
                    # AND the task hasn't changed, skip permanently
                    if len(my_remarks) >= MAX_REMARKS_PER_TASK:
                        log_think(f"Task #{task_id}: {len(my_remarks)} remarks posted, task unchanged, skipping")
                    continue
                else:
                    # Task was updated since our last remark — user clarified!
                    log_think(f"Task #{task_id} was updated since my last feedback. Re-evaluating...")

            log_think(f"Evaluating: \"{detail.get('title', '')[:50]}\" (budget={detail.get('budget_credits')})")

            # Fetch conversation messages so the LLM sees poster replies
            try:
                conv_messages = self.client.get_task_messages(task_id) or []
            except Exception:
                conv_messages = []

            try:
                evaluation = self.brain.evaluate_task(detail, conv_messages)
            except Exception as e:
                log_warn(f"LLM evaluation failed: {e}")
                log_warn(traceback.format_exc().strip().splitlines()[-1])
                continue

            # ── Two-phase feedback/claim logic ──
            # Phase 1 (initial / no answers yet): Post feedback with questions, do NOT claim.
            # Phase 2 (follow-up / poster answered): Skip remark, just claim.
            # Determine if poster has answered our questions
            has_answered_questions = False
            for r in my_remarks:
                eval_data = r.get("evaluation")
                if eval_data:
                    for q in eval_data.get("questions", []):
                        if q.get("answer"):
                            has_answered_questions = True
                            break
                if has_answered_questions:
                    break
            # Also treat poster free-form messages as answers
            if not has_answered_questions and conv_messages:
                poster_msgs = [m for m in conv_messages if m.get("sender_type") == "poster" and m.get("message_type") == "text"]
                if poster_msgs:
                    has_answered_questions = True

            if has_answered_questions:
                # PHASE 2: Poster answered — skip remark, proceed directly to claim
                log_think(f"  Poster answered questions for #{task_id} — proceeding to claim (no new remark)")
                if not is_claimed and evaluation.get("should_claim") and evaluation.get("confidence") in ("high", "medium"):
                    if best_task is None or evaluation.get("proposed_credits", 0) > (best_evaluation or {}).get("proposed_credits", 0):
                        best_task = detail
                        best_evaluation = evaluation
                        log_think(f"  -> Good fit! confidence={evaluation.get('confidence')}, "
                                 f"bid={evaluation.get('proposed_credits')}")
                elif is_claimed:
                    log_think(f"  -> Already claimed #{task_id}")
                    self.attempted_tasks[task_id] = datetime.now(timezone.utc)
            else:
                # PHASE 1: Initial evaluation — post feedback with questions, do NOT claim
                feedback = evaluation.get("feedback", "").strip().strip("\"'")
                if len(my_remarks) < MAX_REMARKS_PER_TASK and feedback:
                    try:
                        remark_payload = {"remark": feedback}
                        eval_data = evaluation.get("evaluation", {})
                        if eval_data and isinstance(eval_data, dict):
                            questions = []
                            for idx, q in enumerate(eval_data.get("questions", [])[:8]):
                                if not isinstance(q, dict) or not q.get("text"):
                                    continue
                                qtype = q.get("type", "multiple_choice")
                                entry = {
                                    "id": q.get("id", f"q{idx}"),
                                    "text": q["text"],
                                    "type": qtype,
                                }
                                if qtype == "multiple_choice":
                                    opts = q.get("options", [])
                                    if len(opts) < 2:
                                        continue
                                    entry["options"] = opts[:6]
                                elif qtype == "text_input":
                                    entry["placeholder"] = q.get("placeholder", "Type your answer...")
                                elif qtype == "scale":
                                    entry["scale_min"] = q.get("scale_min", 1)
                                    entry["scale_max"] = q.get("scale_max", 5)
                                    entry["scale_labels"] = q.get("scale_labels", ["Low", "High"])
                                # yes_no needs no extra fields
                                questions.append(entry)

                            remark_payload["evaluation"] = {
                                "score": int(eval_data.get("score", 5)),
                                "strengths": [s for s in eval_data.get("strengths", [])[:5] if s],
                                "concerns": [c for c in eval_data.get("concerns", [])[:5] if c],
                                "questions": questions,
                            }
                        log_think(f"  Posting feedback only (score={eval_data.get('score')}, "
                                 f"{len(remark_payload.get('evaluation', {}).get('questions', []))} Qs) — no claim yet")
                        resp = self.client.post(f"/api/v1/tasks/{task_id}/remarks", remark_payload)
                        if resp.get("ok"):
                            log_ok(f"  Feedback posted to #{task_id} (waiting for poster to answer)")
                        else:
                            err_info = resp.get("error", {})
                            err_msg = err_info.get("message", str(err_info)) if isinstance(err_info, dict) else str(err_info)
                            log_warn(f"  Failed to post feedback to #{task_id}: {err_msg}")
                    except Exception as e:
                        log_warn(f"Failed to send feedback to #{task_id}: {e}")

                # Do NOT claim — wait for poster to answer
                log_think(f"  -> Feedback posted, waiting for poster to answer before claiming")
                self.attempted_tasks[task_id] = datetime.now(timezone.utc)

        if not best_task:
            log_wait("No suitable tasks found this cycle")
            return

        # Step 5: Claim the best task
        self._claim_and_work(best_task, best_evaluation)

    def _claim_and_work(self, task: dict, evaluation: dict):
        """Claim a task and immediately generate + submit a deliverable."""
        task_id = task["id"]
        budget = task.get("budget_credits", 50)
        proposed = min(evaluation.get("proposed_credits", budget), budget)
        proposed = max(proposed, 10)

        self.attempted_tasks[task_id] = datetime.now(timezone.utc)

        # Generate claim message
        approach = evaluation.get("approach", "I will deliver high-quality work.")
        try:
            message = self.brain.generate_claim_message(task, approach)
        except Exception:
            message = f"I can deliver this task. My approach: {approach[:200]}"

        log_act(f"Claiming task #{task_id} for {proposed} credits...")
        claim_resp = self.client.claim_task(task_id, proposed, message)

        if not claim_resp.get("ok"):
            err = (claim_resp.get("error") or {})
            log_warn(f"Claim rejected: {err.get('code', 'unknown')} — {err.get('message', '')[:100]}")
            return

        claim_id = claim_resp["data"]["id"]
        log_ok(f"Claim #{claim_id} submitted! Waiting for poster to accept...")
        self.claimed_task_ids.add(task_id)

        # Note: We can't generate the deliverable until the claim is accepted.
        # The _work_on_claimed_tasks() method handles this in the next tick.

    def _work_on_claimed_tasks(self):
        """Check accepted claims and generate deliverables for them."""
        # Get tasks assigned to us that need deliverables
        try:
            my_tasks = self.client.get_my_tasks()
        except Exception as e:
            log_err(f"Failed to fetch my tasks: {e}")
            return

        if not my_tasks:
            log_warn("No tasks assigned to this agent (get_my_tasks returned empty)")
            return

        log_think(f"Checking {len(my_tasks)} assigned task(s) for pending work...")

        for task_summary in my_tasks:
            task_id = task_summary.get("id") or task_summary.get("task_id")
            status = task_summary.get("status", "")
            log_think(f"  Task #{task_id}: status={status}")

            if status in ("claimed", "in_progress", "accepted"):
                # Transition claimed → in_progress so the frontend stepper advances
                if status == "claimed":
                    try:
                        start_resp = self.client.start_task(task_id)
                        if start_resp.get("ok"):
                            log_ok(f"Task #{task_id} transitioned → in_progress")
                    except Exception:
                        pass  # Non-fatal; proceed regardless

                # Get full task details
                try:
                    task = self.client.get_task(task_id)
                except Exception as e:
                    log_err(f"Failed to fetch task #{task_id} details: {e}")
                    continue
                if not task:
                    log_warn(f"Task #{task_id} returned None from API")
                    continue

                # Check if task already has deliverables
                deliverables = task.get("deliverables", [])
                submitted = [d for d in deliverables if d.get("status") == "submitted"]
                if submitted:
                    log_think(f"  Task #{task_id}: already has {len(submitted)} submitted deliverable(s), waiting for review")
                    continue  # already submitted, waiting for review

                log_act(f"Running CI/CD pipeline for task #{task_id}: \"{task.get('title', '')[:40]}\"")

                # Use the real coder → deploy pipeline (same as swarm.py orchestrator)
                task_dir = WORKSPACE_DIR / f"task_{task_id}"
                task_dir.mkdir(parents=True, exist_ok=True)

                pipeline_stage = _get_pipeline_stage(task_dir)
                log_think(f"  Task #{task_id}: pipeline stage = '{pipeline_stage}'")

                # ── CODER stage ────────────────────────────────────────────
                if pipeline_stage == "coding":
                    if not _acquire_lock(task_dir, "Coder"):
                        log_warn(f"Task #{task_id} is locked by another agent — skipping")
                        continue
                    try:
                        result = _run_pipeline_agent(
                            CODER_SCRIPT, self.client.api_key, self.client.base_url, task_id, timeout=7200
                        )
                    finally:
                        _release_lock(task_dir)

                    action = result.get("action", "")
                    if action in ("coded", "done"):
                        log_ok(f"Coder Agent finished for task #{task_id} — now deploying")
                        pipeline_stage = _get_pipeline_stage(task_dir)  # re-read (coder updates it)
                    elif action in ("error", "timeout", "no_result"):
                        log_err(f"Coder Agent failed for task #{task_id}: {action}")
                        continue
                    else:
                        log_think(f"Coder Agent result: {action} — re-checking stage")
                        pipeline_stage = _get_pipeline_stage(task_dir)

                # ── TESTER stage ───────────────────────────────────────────
                if pipeline_stage == "testing":
                    if not _acquire_lock(task_dir, "Tester"):
                        log_warn(f"Task #{task_id} test locked — skipping")
                        continue
                    try:
                        result = _run_pipeline_agent(
                            TESTER_SCRIPT, self.client.api_key, self.client.base_url, task_id, timeout=7200
                        )
                    finally:
                        _release_lock(task_dir)

                    action = result.get("action", "")
                    if action in ("passed", "deploying"):
                        log_ok(f"Tests passed for task #{task_id} — advancing to deploy")
                        pipeline_stage = _get_pipeline_stage(task_dir)
                    elif action == "retry_coding":
                        log_warn(f"Tests failed for task #{task_id} — cycling back to coder")
                        pipeline_stage = _get_pipeline_stage(task_dir)
                    elif action in ("error", "timeout", "no_result"):
                        log_err(f"Tester Agent failed for task #{task_id}: {action}")
                        continue
                    else:
                        log_think(f"Tester Agent result: {action}")
                        pipeline_stage = _get_pipeline_stage(task_dir)

                # ── DEPLOY stage ───────────────────────────────────────────
                if pipeline_stage == "deploying":
                    if not _acquire_lock(task_dir, "Deployer"):
                        log_warn(f"Task #{task_id} deploy locked — skipping")
                        continue
                    try:
                        result = _run_pipeline_agent(
                            DEPLOY_SCRIPT, self.client.api_key, self.client.base_url, task_id, timeout=7200
                        )
                    finally:
                        _release_lock(task_dir)

                    action = result.get("action", "")
                    if action == "delivered":
                        del_id = result.get("deliverable_id", "?")
                        log_ok(f"Deliverable #{del_id} submitted via Deploy Agent for task #{task_id}!")
                    elif action in ("error", "timeout", "no_result"):
                        log_err(f"Deploy Agent failed for task #{task_id}: {action} — {result.get('error', '')[:120]}")
                    else:
                        log_think(f"Deploy Agent result: {action} — will retry next tick")

            elif status == "completed":
                if task_id in self.claimed_task_ids:
                    self.tasks_completed += 1
                    self.claimed_task_ids.discard(task_id)
                    log_ok(f"Task #{task_id} COMPLETED! Total completed: {self.tasks_completed}")

    def _check_revisions(self):
        """Check for tasks that need revision and resubmit."""
        my_tasks = self.client.get_my_tasks()

        for task_summary in my_tasks:
            task_id = task_summary.get("id") or task_summary.get("task_id")
            status = task_summary.get("status", "")

            # in_progress with existing deliverables means revision was requested
            if status == "in_progress":
                task = self.client.get_task(task_id)
                if not task:
                    continue

                deliverables = task.get("deliverables", [])
                revision_requested = [d for d in deliverables if d.get("status") == "revision_requested"]

                if revision_requested:
                    last = revision_requested[-1]
                    feedback = last.get("revision_notes", "Please improve the deliverable.")
                    log_act(f"Revision requested for task #{task_id}: \"{feedback[:60]}\"")

                    # Dispatch Revision Agent sub-process (mirrors swarm.py)
                    task_dir = WORKSPACE_DIR / f"task_{task_id}"
                    task_dir.mkdir(parents=True, exist_ok=True)
                    if not _acquire_lock(task_dir, "Revision"):
                        log_warn(f"Task #{task_id} revision locked — skipping")
                        continue
                    try:
                        result = _run_pipeline_agent(
                            REVISION_SCRIPT,
                            self.client.api_key,
                            self.client.base_url,
                            task_id,
                            timeout=3600,
                        )
                    finally:
                        _release_lock(task_dir)

                    if result.get("action") == "revised":
                        log_ok(f"Revision submitted for task #{task_id}")
                    else:
                        log_warn(f"Revision Agent result: {result.get('action', '?')} for task #{task_id}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Oriexa Autonomous Worker Agent")
    parser.add_argument("--api-key", type=str, help="Existing agent API key (skips registration)")
    parser.add_argument("--name", type=str, default="AutoWorker Bot",
                       help="Agent name (for registration)")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                       help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument("--capabilities", type=str, default=",".join(DEFAULT_CAPABILITIES),
                       help="Comma-separated capabilities")
    args = parser.parse_args()

    # Validate
    if not ANTHROPIC_KEY:
        print("FATAL: ANTHROPIC_KEY not set in environment. Cannot run agent.")
        sys.exit(1)

    capabilities = [c.strip() for c in args.capabilities.split(",")]

    # Create client
    client = OriexaClient(BASE_URL)

    # Register or use existing key
    if args.api_key:
        client.api_key = args.api_key
        log_ok(f"Using provided API key: {args.api_key[:14]}...")
        # Fetch profile to get agent_id
        profile = client.get_profile()
        if profile:
            log_ok(f"Logged in as: {client.agent_name} (ID: {client.agent_id})")
        else:
            log_err("Failed to fetch agent profile with provided API key")
            sys.exit(1)
    else:
        log_act(f"Registering new agent: {args.name}...")
        try:
            key = client.register(args.name, capabilities)
            log_ok(f"Registered! API key: {key[:14]}...")
            log_ok(f"Agent ID: {client.agent_id}")
        except Exception as e:
            log_err(f"Registration failed: {e}")
            sys.exit(1)

    # Create brain and agent
    brain = AgentBrain(capabilities)
    agent = AutonomousWorkerAgent(client, brain, args.interval)

    # Start the loop
    agent.run()


if __name__ == "__main__":
    main()
