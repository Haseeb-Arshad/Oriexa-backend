"""
Oriexa Agent Swarm — Shared Base Module

Shared utilities used by all specialized agents:
  - OriexaClient (API client)
  - LLM call helpers (Anthropic Claude)
  - Logging
  - Configuration
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load environment
# On droplet: __file__ = /opt/oriexa/repo/agents/base_agent.py → parent.parent = repo/
# Locally:    __file__ = Oriexa/scripts/agents/base_agent.py  → parent.parent.parent = Oriexa/
_script_dir = Path(__file__).parent.parent
load_dotenv(_script_dir / ".env")
load_dotenv(_script_dir / "reviewer-agent" / ".env")

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

BASE_URL = os.environ.get("ORIEXA_BASE_URL", os.environ.get("NEXTAUTH_URL", "http://localhost:3000"))
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
KIMI_KEY = os.environ.get("Kimi_Key", "") or os.environ.get("KIMI_KEY", "")
MOONSHOT_API_KEY = os.environ.get("MOONSHOT_API_KEY", "")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")

DEFAULT_CAPABILITIES = ["nextjs", "react", "vite", "javascript", "typescript", "tailwindcss", "frontend", "web-development"]


# ═══════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════

def log(icon: str, msg: str, agent_name: str = "", **kwargs):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{agent_name}] " if agent_name else ""
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
    print(f"  [{ts}] {icon} {prefix}{msg} {extra}", flush=True)


def log_think(msg: str, agent_name: str = "", **kw):
    log("THINK", msg, agent_name, **kw)


def log_act(msg: str, agent_name: str = "", **kw):
    log("ACT  ", msg, agent_name, **kw)


def log_ok(msg: str, agent_name: str = ""):
    prefix = f"[{agent_name}] " if agent_name else ""
    print(f"\033[32m[OK]     {prefix}{msg}\033[0m", flush=True)


def log_warn(msg: str, agent_name: str = "", **kw):
    log("WARN ", msg, agent_name, **kw)


def log_err(msg: str, agent_name: str = "", **kw):
    log("ERROR", msg, agent_name, **kw)


def log_wait(msg: str, agent_name: str = "", **kw):
    log(" ... ", msg, agent_name, **kw)


def iso_to_datetime(iso_str: str | None) -> datetime | None:
    """Safely convert ISO string (with Z or +00:00) to datetime object."""
    if not iso_str:
        return None
    try:
        clean_str = iso_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PROGRESS EMISSION (shared by all agents)
# ═══════════════════════════════════════════════════════════════════════════

def write_progress(
    task_dir: Path,
    task_id: int,
    phase: str,
    title: str,
    description: str,
    detail: str = "",
    progress_pct: float = 0.0,
    subtask_id: int | None = None,
    metadata: dict | None = None,
) -> None:
    """
    Append a ProgressStep JSON line to progress.jsonl in the task workspace.
    The index is derived from the current line count so multiple agent
    processes can append without a shared counter.
    """
    try:
        progress_file = task_dir / "progress.jsonl"
        # Derive next index from existing lines
        idx = 0
        if progress_file.exists():
            content = progress_file.read_text(encoding="utf-8")
            idx = len([l for l in content.split("\n") if l.strip()])

        step = {
            "index": idx,
            "subtask_id": subtask_id,
            "phase": phase,
            "title": title,
            "description": description,
            "detail": detail,
            "progress_pct": progress_pct,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(step) + "\n")
    except Exception:
        pass  # Never crash the agent over progress logging


# ═══════════════════════════════════════════════════════════════════════════
# LLM CLIENT
# ═══════════════════════════════════════════════════════════════════════════

def llm_call(system: str, user: str, max_tokens: int = 2048, provider: str = "kimi") -> str:
    """Multi-provider LLM call wrapper."""
    if provider == "claude" or provider == "claude-sonnet":
        # Use current 3.5 Sonnet model ID
        model_id = "claude-3-5-sonnet-20240620"
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model_id,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=3600.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]
        
    elif provider == "kimi":
        api_key = MOONSHOT_API_KEY or KIMI_KEY
        if not api_key:
            raise ValueError("Kimi/Moonshot API key not configured")
        resp = httpx.post(
            "https://api.moonshot.cn/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "kimi-k2.5-thinking",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
            timeout=3600.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    elif provider == "trinity":
        if not OPENROUTER_KEY:
            raise ValueError("OpenRouter API key not configured")
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "arcee-ai/trinity-large-preview:free",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
            timeout=3600.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    
    else:
        raise ValueError(f"Unknown provider: {provider}")


def smart_llm_call(system: str, user: str, max_tokens: int = 2048, complexity: str = "routine") -> str:
    """Routes to Kimi/Trinity first, falling back to Claude only if needed or complex."""
    if complexity == "extreme":
        providers = ["claude-sonnet", "kimi", "trinity"]
    elif complexity == "high":
        providers = ["kimi", "claude-sonnet", "trinity"]
    else:
        providers = ["kimi", "trinity", "claude-sonnet"]
    
    last_error = "No providers attempted"
    for p in providers:
        # Skip if no key
        if p.startswith("claude") and not ANTHROPIC_KEY: continue
        if p == "kimi" and not (MOONSHOT_API_KEY or KIMI_KEY): continue
        if p == "trinity" and not OPENROUTER_KEY: continue

        # Internal retry for the same provider on transient errors
        for attempt in range(2):
            try:
                return llm_call(system, user, max_tokens, provider=p)
            except httpx.HTTPStatusError as e:
                # Immediate fallback for auth/credit errors
                if e.response.status_code in (401, 402, 403, 404):
                    last_error = f"{p} (HTTP {e.response.status_code})"
                    log_warn(f"Provider {p} auth/credit failure ({e.response.status_code}). Switching...")
                    break 
                
                # Retry once for 5xx
                if e.response.status_code >= 500 and attempt == 0:
                    time.sleep(1)
                    continue
                
                last_error = str(e)
                log_warn(f"Provider {p} failed: {e}. Falling back...")
                break
            except Exception as e:
                last_error = str(e)
                log_warn(f"Provider {p} failed: {e}. Falling back...")
                break
    
    log_err(f"All LLMs failed for smart_llm_call. Last error: {last_error}")
    return ""


def _clean_json(raw: str) -> dict:
    """Parse JSON with progressive fallback strategies."""
    # Strategy 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip trailing commas before } or ]
    cleaned = re.sub(r',(\s*[}\]])', r'\1', raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 3: replace unescaped literal newlines inside JSON string values.
    # This handles the case where LLM puts a real newline inside a quoted string
    # instead of \n, which is invalid JSON.
    def _fix_string_newlines(s: str) -> str:
        result = []
        in_string = False
        escape_next = False
        for ch in s:
            if escape_next:
                result.append(ch)
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                result.append(ch)
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string and ch == '\n':
                result.append('\\n')
                continue
            if in_string and ch == '\r':
                result.append('\\r')
                continue
            result.append(ch)
        return ''.join(result)

    try:
        return json.loads(_fix_string_newlines(cleaned))
    except json.JSONDecodeError as e:
        raise e


def _extract_json_block(raw: str) -> str | None:
    """
    Extract the outermost JSON object from a string.
    Handles markdown code fences (```json ... ```) and finds the correct
    closing brace by counting depth rather than using rfind.
    """
    # Strip markdown code fences first
    fence_match = re.search(r'```(?:json)?\s*\n?({.*?})\s*```', raw, re.DOTALL)
    if fence_match:
        return fence_match.group(1)

    # Find first '{' and walk forward counting depth
    start = raw.find('{')
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(raw[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def llm_json(system: str, user: str, max_tokens: int = 2048, complexity: str = "routine", provider: str = None) -> dict:
    """LLM call that returns parsed JSON, using smart routing and robust parsing."""
    if provider:
        try:
            raw = llm_call(system, user, max_tokens=max_tokens, provider=provider)
        except Exception as e:
            log_warn(f"Requested provider {provider} failed: {e}. Falling back to smart routing...")
            raw = smart_llm_call(system, user, max_tokens=max_tokens, complexity=complexity)
    else:
        raw = smart_llm_call(system, user, max_tokens=max_tokens, complexity=complexity)
        
    if not raw:
        return {}

    json_str = _extract_json_block(raw)
    if json_str:
        try:
            return _clean_json(json_str)
        except Exception as e:
            log_err(f"JSON extract failed: {e}")
            return {"_raw": raw}

    return {"_raw": raw}


def kimi_enhance_prompt(prompt: str) -> str:
    """Uses Kimi K2 Thinking (Direct API) to enhance task requirements into a high-end implementation blueprint."""
    api_key = MOONSHOT_API_KEY or KIMI_KEY
    if not api_key:
        log_warn("Moonshot/Kimi API key is not configured. Falling back to Trinity or raw prompt.")
        return trinity_enhance_prompt(prompt)

    sys_prompt = ("You are a world-class Staff Software Architect. The user will provide raw, basic task requirements. "
                  "Your job is to transform these requirements into an extremely detailed, high-level technical blueprint and specification. "
                  "Add best practices, edge-case handling, precise technological choices, and step-by-step logic. "
                  "CRITICAL: Always prioritize the latest versions of all technologies, frameworks, and libraries. "
                  "If you encounter obstacles or past failures, you MUST be extremely proactive: resolve the issues whatever it takes, "
                  "even if it means changing the architecture, switching tools, or adopting a different approach to bypass the blocker.")
    try:
        resp = httpx.post(
            "https://api.moonshot.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "kimi-k2.5-thinking",
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": f"Please enhance these raw requirements into a detailed architectural blueprint:\n\n{prompt}"},
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
            },
            timeout=3600.0,
        )
        data = resp.json()
        if "choices" in data and data["choices"]:
            enhanced = data["choices"][0]["message"]["content"]
            # Cap output to prevent Claude from generating oversized JSON
            if len(enhanced) > 3000:
                log_warn(f"Kimi blueprint was {len(enhanced)} chars — trimming to 3000", "Kimi")
                enhanced = enhanced[:3000] + "\n\n[Blueprint truncated for token safety]"
            return enhanced
        else:
            log_warn(f"Moonshot Direct API failed, falling back to Trinity: {data}")
            return trinity_enhance_prompt(prompt)
    except Exception as e:
        log_err(f"Moonshot Direct API failed: {e}. Falling back to Trinity.")
        return trinity_enhance_prompt(prompt)

def claude_enhance_prompt(prompt: str) -> str:
    """Uses Claude to enhance task requirements into a high-end implementation blueprint."""
    sys_prompt = ("You are a world-class Staff Software Architect. The user will provide raw, basic task requirements. "
                  "Your job is to transform these requirements into an extremely detailed, high-level technical blueprint and specification. "
                  "Add best practices, edge-case handling, precise technological choices, and step-by-step logic. "
                  "CRITICAL: Always prioritize the latest versions of all technologies, frameworks, and libraries. "
                  "If you encounter obstacles or past failures, you MUST be extremely proactive: resolve the issues whatever it takes, "
                  "even if it means changing the architecture, switching tools, or adopting a different approach to bypass the blocker.")
    try:
        enhanced = llm_call(
            sys_prompt, 
            f"Please enhance these raw requirements into a detailed architectural blueprint:\n\n{prompt}", 
            max_tokens=4000, 
            provider="claude-sonnet"
        )
        if len(enhanced) > 3000:
            log_warn(f"Claude blueprint was {len(enhanced)} chars — trimming to 3000", "Claude")
            enhanced = enhanced[:3000] + "\n\n[Blueprint truncated for token safety]"
        return enhanced
    except Exception as e:
        log_err(f"Claude API failed: {e}. Falling back to Kimi.")
        return kimi_enhance_prompt(prompt)


def trinity_enhance_prompt(prompt: str) -> str:
    """Uses Arcee-AI Trinity Large Preview (Free via OpenRouter) as an alternative enhancement model."""
    if not OPENROUTER_KEY:
        log_warn("OPENROUTER_API_KEY is not configured. Returning raw prompt.")
        return prompt

    sys_prompt = ("You are a world-class Staff Software Architect. Enhance these basic requirements into a detailed technical specification. "
                  "CRITICAL: Always prioritize the latest versions of all technologies, frameworks, and libraries. "
                  "If you encounter obstacles or past failures, you MUST be extremely proactive: resolve the issues whatever it takes, "
                  "even if it means changing the architecture, switching tools, or adopting a different approach to bypass the blocker.")
    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "arcee-ai/trinity-large-preview:free",
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "max_tokens": 10000,
            },
            timeout=3600.0,
        )
        data = resp.json()
        if "choices" in data and data["choices"]:
            return data["choices"][0]["message"]["content"]
        else:
            log_warn(f"Trinity Free API failure: {data}")
            return prompt
    except Exception as e:
        log_err(f"Trinity enhancement failed: {e}")
        return prompt


# ═══════════════════════════════════════════════════════════════════════════
# API CLIENT
# ═══════════════════════════════════════════════════════════════════════════

class OriexaClient:
    """API client for Oriexa with automatic retry and connection recovery."""

    MAX_RETRIES = 3
    RETRY_DELAYS = [1, 3, 5]  # seconds between retries
    TRANSIENT_ERRORS = (
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.PoolTimeout,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        ConnectionResetError,
        ConnectionAbortedError,
        OSError,
    )

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

    def _reconnect(self):
        """Recreate HTTP client to recover from broken connections."""
        try:
            self.http.close()
        except Exception:
            pass
        self.http = httpx.Client(base_url=self.base_url, timeout=3600.0)

    def _request_with_retry(self, method: str, path: str, **kwargs) -> dict:
        """Execute an HTTP request with automatic retry on transient failures."""
        import time as _time

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                if method == "GET":
                    resp = self.http.get(path, headers=self._headers(), **kwargs)
                else:
                    resp = self.http.post(path, headers=self._headers(), **kwargs)

                # Check for empty or non-JSON responses
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"Server error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )

                content_type = resp.headers.get("content-type", "")
                if "application/json" not in content_type:
                    body = resp.text[:200]
                    if not body.strip():
                        return {"ok": False, "error": {"code": "empty_response", "message": f"HTTP {resp.status_code}: empty response"}}
                    # Try parsing anyway — some servers omit content-type header
                    try:
                        return resp.json()
                    except Exception:
                        return {"ok": False, "error": {"code": "non_json", "message": f"HTTP {resp.status_code}: {body}"}}

                return resp.json()

            except self.TRANSIENT_ERRORS as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                    _time.sleep(delay)
                    self._reconnect()  # Fresh connection for next attempt
                continue

            except httpx.HTTPStatusError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1 and e.response.status_code >= 500:
                    delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                    _time.sleep(delay)
                    continue
                # 4xx errors: don't retry
                try:
                    return e.response.json()
                except Exception:
                    return {"ok": False, "error": {"code": f"http_{e.response.status_code}", "message": str(e)}}

            except Exception as e:
                last_error = e
                break

        # All retries exhausted
        raise last_error or RuntimeError(f"Request to {path} failed after {self.MAX_RETRIES} attempts")

    def get(self, path: str, params: dict = None) -> dict:
        return self._request_with_retry("GET", path, params=params)

    def post(self, path: str, json_data: dict = None) -> dict:
        return self._request_with_retry("POST", path, json=json_data)

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
        """Fetch conversation messages for a task (poster + agent messages)."""
        resp = self.get(f"/api/v1/tasks/{task_id}/messages")
        if resp.get("ok"):
            data = resp.get("data", [])
            return data if isinstance(data, list) else []
        return []

    def post_remark(self, task_id: int, remark) -> dict:
        """Post a feedback remark on a task. Accepts a string or a dict with 'remark' + optional 'evaluation'."""
        if isinstance(remark, str):
            payload = {"remark": remark}
        else:
            payload = remark
        return self.post(f"/api/v1/tasks/{task_id}/remarks", payload)


# ═══════════════════════════════════════════════════════════════════════════
# CONVENIENCE: Write files + commit in one step
# ═══════════════════════════════════════════════════════════════════════════

def step_commit(
    task_dir: Path,
    description: str,
    files: list[dict],
    push: bool = False,
) -> str | None:
    """
    Write files to disk and commit with a descriptive message.
    
    Args:
        task_dir: Path to the task workspace
        description: Human-readable step description (used as commit msg)
        files: List of {"path": "relative/path", "content": "file content"}
        push: Whether to push after committing
    
    Returns:
        Commit hash on success, None on failure.
    """
    from agents.git_ops import commit_step as _commit, push_to_remote, append_commit_log

    # Write files
    for f in files:
        file_path = task_dir / f["path"]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(f["content"], encoding="utf-8")

    # Commit
    commit_msg = f"feat: {description}" if not description.startswith(("feat:", "fix:", "chore:", "test:", "docs:")) else description
    h = _commit(task_dir, commit_msg)
    
    if h:
        append_commit_log(task_dir, h, commit_msg)
        if push:
            push_to_remote(task_dir)

    return h
