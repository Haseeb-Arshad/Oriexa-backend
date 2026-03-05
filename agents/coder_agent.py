"""
Oriexa Coder Agent — Shell-Based, Step-by-Step Code Generator

Multi-step agent that:
  1. Creates a GitHub repo FIRST
  2. Plans the codebase as a series of steps
  3. Executes each step individually
  4. Commits after every step with descriptive messages
  5. Pushes to GitHub incrementally

Usage (called by orchestrator, not directly):
    python -m agents.coder_agent --api-key <key> --task-id <id> [--base-url <url>]
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

# Add parent path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import (
    BASE_URL,
    OriexaClient,
    llm_json,
    smart_llm_call,
    claude_enhance_prompt,
    log_err,
    log_ok,
    log_think,
    log_warn,
)
from agents.git_ops import (
    init_repo,
    create_github_repo,
    commit_step,
    push_to_remote,
    should_push,
    append_commit_log,
    get_repo_url,
)
from agents.shell_executor import (
    run_shell_combined,
    run_npm_install,
    run_npx_create,
    append_build_log,
    log_command,
)

AGENT_NAME = "Coder"
WORKSPACE_DIR = Path(os.environ.get("AGENT_WORKSPACE_DIR", str(Path(__file__).parent.parent / "agent_works")))


# ═══════════════════════════════════════════════════════════════════════════
# PROGRESS EMITTER — writes ProgressStep JSON to progress.jsonl
# ═══════════════════════════════════════════════════════════════════════════

import time as _time

_progress_index: dict[int, int] = {}  # task_id -> next step index


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
    """Append a ProgressStep entry to progress.jsonl in the task workspace."""
    import json as _json
    import datetime as _dt

    idx = _progress_index.get(task_id, 0)
    _progress_index[task_id] = idx + 1

    step = {
        "index": idx,
        "subtask_id": subtask_id,
        "phase": phase,
        "title": title,
        "description": description,
        "detail": detail,
        "progress_pct": progress_pct,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "metadata": metadata or {},
    }

    progress_file = task_dir / "progress.jsonl"
    try:
        with open(progress_file, "a", encoding="utf-8") as f:
            f.write(_json.dumps(step) + "\n")
    except Exception as e:
        log_warn(f"Failed to write progress: {e}", AGENT_NAME)


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: PLAN — Break the task into implementation steps
# ═══════════════════════════════════════════════════════════════════════════

def plan_implementation(title: str, desc: str, reqs: str, past_errors: str = "", poster_context: str = "", complexity: str = "high") -> list[dict]:
    """
    Ask the LLM to break the task into implementation steps.
    Each step has a description and list of files to generate.
    """
    error_context = ""
    if past_errors:
        error_context = (
            f"\n\nPREVIOUS ATTEMPT FAILED WITH THIS ERROR:\n{past_errors}\n"
            "You must account for this in your plan and fix the issue.\n"
        )

    poster_section = ""
    if poster_context:
        poster_section = f"\n\nPoster's Requirements & Answers:\n{poster_context}\n"

    system = (
        "You are a Senior Software Architect planning an implementation. "
        "Break the task into 3-6 ordered implementation steps. "
        "Each step should be a logical unit of work (e.g. scaffold, config, "
        "core logic, API routes, UI components, tests). "
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n\n"
        "CRITICAL — PROJECT TYPE RULES (STRICTLY ENFORCED):\n"
        "- ALWAYS prioritize the latest versions of all technologies, frameworks, and tools.\n"
        "- CRITICAL: ALWAYS use the '@latest' tag for npx/npm/pip commands and specify 'latest' for all dependency versions in package.json/requirements.txt. NEVER use specific version numbers unless absolutely necessary to fix a known bug.\n"
        "- BE PROACTIVE: If you encounter an error, version conflict, or build failure, RESOLVE IT WHATEVER IT TAKES. You are empowered to change the project structure, switch tools, or adopt a completely different technical approach to bypass the blocker.\n"
        "- You MUST ONLY use JavaScript/TypeScript frontend or fullstack frameworks.\n"
        "- DEFAULT to 'nextjs' for ALL tasks: websites, web apps, dashboards, "
        "landing pages, portfolios, e-commerce, SaaS, tools with a UI, APIs, backends — everything.\n"
        "- Use 'react' ONLY if the task explicitly says 'React without Next.js' or 'Vite + React'.\n"
        "- Use 'vite' ONLY if the task explicitly specifies Vite as the build tool.\n"
        "- Use 'static' ONLY for pure HTML/CSS/JS with absolutely no framework needed.\n"
        "- NEVER use 'python' — Python is FORBIDDEN as a project type.\n"
        "- NEVER use 'node' standalone — if backend is needed, use Next.js API routes.\n"
        "- Backend logic MUST live inside the framework (Next.js API routes, server actions).\n"
        "- NO external database connections — use in-memory state or localStorage only.\n"
        "- When in doubt: choose 'nextjs'. It is ALWAYS the safe default. The NEXTJS framework MUST be prioritized before you proceed with implementation.\n"
        "- For 'nextjs' always use scaffold_command: "
        "'npx create-next-app@latest ./ --typescript --tailwind --eslint --app --no-src-dir --import-alias @/* --yes --force'\n\n"
        "CRITICAL — FILE LIST RULES:\n"
        "- Every step MUST list at least 2 files to create.\n"
        "- Each file must have a 'path' (relative) and 'description' (what it does).\n"
        "- Be specific: e.g. 'app/page.tsx', 'components/Hero.tsx', 'app/api/data/route.ts'.\n"
        "- NEVER leave the files array empty."
    )

    user = (
        f"Plan the implementation for this task:\n"
        f"Title: {title}\n"
        f"Description: {desc}\n"
        f"Requirements: {reqs}\n"
        f"{poster_section}"
        f"{error_context}\n"
        "Return a JSON object with:\n"
        '{\n'
        '  "project_type": "nextjs" | "react" | "vite" | "static",\n'
        '  "scaffold_command": "npx create-next-app@latest ./ --typescript --tailwind --eslint --app --no-src-dir --import-alias @/* --yes --force" or null,\n'
        '  "steps": [\n'
        '    {\n'
        '      "step_number": 1,\n'
        '      "description": "Set up project configuration",\n'
        '      "commit_message": "chore: add project configuration",\n'
        '      "files": [\n'
        '        {"path": "tsconfig.json", "description": "TypeScript config"},\n'
        '        {"path": "app/layout.tsx", "description": "Root layout component"}\n'
        '      ]\n'
        '  ],\n'
        '  "test_command": "npm run build"\n'
        '}\n'
    )

    result = llm_json(system, user, max_tokens=2048, complexity=complexity, provider="claude-sonnet")

    # ── Post-processing: enforce project type and scaffold ──
    if isinstance(result, dict):
        project_type = (result.get("project_type") or "").lower().strip()
        # Force nextjs for forbidden types
        if project_type in ("node", "python", "express", "flask", "django", "") or project_type not in ("nextjs", "react", "vite", "static"):
            log_warn(f"Plan used forbidden project_type '{project_type}' — forcing 'nextjs'", AGENT_NAME)
            result["project_type"] = "nextjs"
            result["scaffold_command"] = "npx create-next-app@latest ./ --typescript --tailwind --eslint --app --no-src-dir --import-alias @/* --yes --force"

        # Ensure scaffold command exists for nextjs
        if result.get("project_type") == "nextjs" and not result.get("scaffold_command"):
            result["scaffold_command"] = "npx create-next-app@latest ./ --typescript --tailwind --eslint --app --no-src-dir --import-alias @/* --yes --force"

        # Ensure steps have file lists
        for step in result.get("steps", []):
            if not step.get("files"):
                step_desc = step.get("description", "implementation")
                step["files"] = [
                    {"path": f"app/page.tsx", "description": f"Main page for: {step_desc}"},
                    {"path": f"components/{step_desc.replace(' ', '')}.tsx", "description": f"Component for: {step_desc}"},
                ]

    return result


def generate_step_code(
    step: dict,
    title: str,
    desc: str,
    reqs: str,
    blueprint: str,
    existing_files: list[str],
    skill_contents: list[str],
    poster_context: str = "",
    task_dir: Path = None,
    complexity: str = "high",
) -> list[dict]:
    """
    Generate code for a single implementation step.
    Returns a list of {path, content} dicts.
    Retries once if all files come back empty.
    """
    files_desc = "\n".join(
        f"  - {f['path']}: {f.get('description', '')}"
        for f in step.get("files", [])
    )
    existing_context = ""
    if existing_files:
        existing_context = (
            "\nFiles already created in the project:\n"
            + "\n".join(f"  - {f}" for f in existing_files[:30])
            + "\n"
        )

    system = (
        "You are a Senior Fullstack Developer producing production-ready code. "
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n"
        "Your response must be a JSON object with a single 'files' array.\n"
        "Each file has 'path' (relative) and 'content' (the full source code).\n"
        "CRITICAL: Every file MUST have real, working code in 'content'. "
        "NEVER return empty content or placeholder comments. Write complete, functional code."
    )
    if skill_contents:
        system += "\n\nYOU MUST STRICTLY FOLLOW THESE CAPABILITY SKILLS:\n\n" + "\n\n---\n\n".join(skill_contents)

    user = (
        f"You are implementing Step {step['step_number']}: {step['description']}\n\n"
        f"Overall Task: {title}\n"
        f"Description: {desc}\n"
        f"Requirements: {reqs}\n\n"
    )
    if poster_context:
        user += f"Poster's Requirements & Answers:\n{poster_context}\n\n"
    user += (
        f"Architectural Blueprint:\n{blueprint[:3000]}\n\n"
        f"{existing_context}\n"
        f"Files to create in THIS step:\n{files_desc}\n\n"
        "Return JSON: {\"files\": [{\"path\": \"...\", \"content\": \"...\"}]}\n"
        "IMPORTANT: Each file's 'content' MUST be complete, working source code. No placeholders."
    )

    # First attempt
    result = llm_json(system, user, max_tokens=16384, complexity=complexity)
    files = result.get("files", []) if isinstance(result, dict) else []
    
    if "_raw" in result and not files and task_dir:
        debug_file = task_dir / f".llm_debug_step_{step.get('step_number')}.txt"
        debug_file.write_text(result["_raw"], encoding="utf-8")
        log_warn(f"LLM produced invalid JSON. Saved raw output to {debug_file.name}", AGENT_NAME)

    # Validate: filter out files with empty or trivial content
    valid_files = [f for f in files if isinstance(f, dict) and f.get("path") and f.get("content", "").strip() and len(f.get("content", "").strip()) > 20]

    if not valid_files and files:
        # Retry once with more explicit instruction and higher intelligence
        log_warn(f"Step {step.get('step_number')}: Got {len(files)} files but all had empty/trivial content. Retrying with EXTREME model complexity...", AGENT_NAME)
        retry_user = user + (
            "\n\nWARNING: Your previous response had empty file contents. "
            "You MUST write complete, working source code for EVERY file. "
            "Do NOT return empty strings or placeholder comments."
        )
        result = llm_json(system, retry_user, max_tokens=16384, complexity="extreme")
        files = result.get("files", []) if isinstance(result, dict) else []
        
        if "_raw" in result and not files and task_dir:
            debug_file = task_dir / f".llm_debug_step_{step.get('step_number')}_retry.txt"
            debug_file.write_text(result["_raw"], encoding="utf-8")
            log_warn(f"LLM produced invalid JSON on retry. Saved raw output to {debug_file.name}", AGENT_NAME)
            
        valid_files = [f for f in files if isinstance(f, dict) and f.get("path") and f.get("content", "").strip() and len(f.get("content", "").strip()) > 20]

    if not valid_files and not files and "_raw" in result:
        # If it failed mapping JSON directly twice, try using Sonnet one last time explicitly with error context
        log_warn(f"Step {step.get('step_number')}: JSON extraction failed. Last resort retry with Claude Sonnet...", AGENT_NAME)
        last_resort_user = user + (
            f"\n\nERROR: Your previous response was invalid JSON. Ensure all properties are properly quoted and escape characters are valid:\n"
            f"```\n{result.get('_raw', '')[:1000]}\n```"
        )
        result = llm_json(system, last_resort_user, max_tokens=16384, complexity="extreme")
        files = result.get("files", []) if isinstance(result, dict) else []
        if "_raw" in result and not files and task_dir:
            debug_file = task_dir / f".llm_debug_step_{step.get('step_number')}_final.txt"
            debug_file.write_text(result["_raw"], encoding="utf-8")
        valid_files = [f for f in files if isinstance(f, dict) and f.get("path") and f.get("content", "").strip() and len(f.get("content", "").strip()) > 20]

    return valid_files


# ═══════════════════════════════════════════════════════════════════════════
# SKILL LOADER — Loads relevant skills based on task characteristics
# ═══════════════════════════════════════════════════════════════════════════

# Map of keyword patterns → skill SKILL.md file names to include
_SKILL_KEYWORD_MAP: list[tuple[list[str], list[str]]] = [
    # Frontend / React / Next.js
    (["react", "next", "nextjs", "frontend", "ui", "dashboard", "landing", "tailwind", "component"],
     ["react-best-practices", "composition-patterns", "frontend-design", "senior-frontend"]),
    # Backend / API
    (["api", "backend", "server", "fastapi", "flask", "express", "rest", "graphql", "database", "sql", "postgres"],
     ["senior-backend", "senior-architect"]),
    # Testing
    (["test", "tdd", "unit test", "e2e", "pytest", "jest", "playwright"],
     ["tdd-guide", "senior-qa"]),
    # DevOps / Deployment
    (["deploy", "docker", "ci/cd", "kubernetes", "vercel", "aws", "cloud", "infrastructure"],
     ["senior-devops", "vercel-deploy", "aws-solution-architect"]),
    # Data / ML
    (["data", "pipeline", "etl", "ml", "model", "training", "analytics", "spark"],
     ["senior-data-engineer", "senior-ml-engineer"]),
    # Security
    (["auth", "authentication", "security", "oauth", "jwt", "encryption"],
     ["senior-security"]),
    # Full-stack (always include)
    (["*"],
     ["senior-fullstack", "code-reviewer", "frontend-design"]),
]


def _load_skills_for_task(title: str, desc: str, reqs: str, plan: dict | None) -> list[str]:
    """
    Load relevant skill files from:
      1. f:/Oriexa/Oriexa/skills/*.md  (Oriexa API skills)
      2. f:/Oriexa/oriexa-api/.claude/skills/<name>/SKILL.md  (code quality skills)

    Selects skills based on task keywords to avoid overloading the prompt.
    """
    task_text = f"{title} {desc} {reqs}".lower()
    project_type = (plan or {}).get("project_type", "").lower()

    # Determine which skill dirs to include
    selected_skill_names: set[str] = set()
    for keywords, skill_names in _SKILL_KEYWORD_MAP:
        if keywords == ["*"] or any(kw in task_text or kw in project_type for kw in keywords):
            selected_skill_names.update(skill_names)

    contents: list[str] = []

    # 1. Load Oriexa API skill files (all of them — they're small)
    api_skills_dir = Path("f:/Oriexa/Oriexa/skills")
    if api_skills_dir.exists():
        for md_file in sorted(api_skills_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
                if text.strip():
                    contents.append(f"### Oriexa API Skill: {md_file.stem}\n\n{text}")
            except Exception:
                pass

    # 2. Load selected .claude/skills/ from oriexa-api repo
    claude_skills_dir = Path("f:/Oriexa/oriexa-api/.claude/skills")
    if claude_skills_dir.exists():
        for skill_name in sorted(selected_skill_names):
            skill_file = claude_skills_dir / skill_name / "SKILL.md"
            if skill_file.exists():
                try:
                    text = skill_file.read_text(encoding="utf-8")
                    # Trim to avoid token overflow — take first 1500 chars
                    if len(text) > 1500:
                        text = text[:1500] + "\n... [truncated for token limit]"
                    if text.strip():
                        contents.append(f"### Claude Skill: {skill_name}\n\n{text}")
                except Exception:
                    pass

    total_chars = sum(len(c) for c in contents)
    log_think(
        f"Loaded {len(contents)} skill sections "
        f"({total_chars // 1000}k chars): {', '.join(list(selected_skill_names)[:6])}",
        AGENT_NAME,
    )
    return contents


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PROCESS
# ═══════════════════════════════════════════════════════════════════════════

def process_task(client: OriexaClient, task_id: int) -> dict:
    try:
        task = client.get_task(task_id)
        if not task:
            return {"action": "error", "error": f"Task {task_id} not found."}

        # Load / initialize state
        task_dir = WORKSPACE_DIR / f"task_{task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)
        state_file = task_dir / ".swarm_state.json"
        log_think(f"Loading state from: {state_file}", AGENT_NAME)

        state = {
            "status": "coding",
            "current_step": 0,
            "total_steps": 0,
            "completed_steps": [],
            "commit_log": [],
            "iterations": 0,
            "files": [],
            "test_command": "echo 'No tests defined'",
        }
        if state_file.exists():
            with open(state_file, "r") as f:
                state = json.load(f)

        if state.get("status") != "coding":
            return {"action": "no_result", "reason": f"State is {state.get('status')}, not coding."}

        title = task.get("title") or ""
        desc = task.get("description") or ""
        reqs = task.get("requirements") or ""
        past_errors = state.get("test_errors", "")

        # ── Intelligence Escalation ──────────────────────────────────
        # Escalate complexity if we've failed multiple times
        plan_complexity = "high"
        if state.get("iterations", 0) >= 2:
            log_warn(f"Escalating agent intelligence to 'extreme' due to {state['iterations']} previous failures", AGENT_NAME)
            plan_complexity = "extreme"

        # ── Fetch poster conversation context ────────────────────────
        poster_context = ""
        try:
            messages = client.get_task_messages(task_id) or []
            # Collect poster messages and answered questions from remarks
            context_parts = []
            
            # Get answered questions from agent remarks
            remarks = task.get("agent_remarks", [])
            for remark in remarks:
                eval_data = remark.get("evaluation")
                if eval_data:
                    for q in eval_data.get("questions", []):
                        if q.get("answer"):
                            context_parts.append(f"Q: {q['text']} -> A: {q['answer']}")

            # Get poster's free-form text messages
            poster_msgs = [
                m for m in messages
                if m.get("sender_type") == "poster" and m.get("message_type") == "text"
            ]
            for m in poster_msgs[-10:]:
                content = m.get("content", "").strip()
                if content:
                    context_parts.append(f"Poster said: {content}")

            if context_parts:
                poster_context = "\n".join(context_parts)
                log_think(f"Loaded {len(context_parts)} poster answers/messages for context", AGENT_NAME)
        except Exception as e:
            log_warn(f"Could not fetch poster conversation: {e}", AGENT_NAME)

        # ── STEP 1: Git Repo (Create FIRST, before any code) ──────────
        log_think(f"Initializing Git repo for task #{task_id}...", AGENT_NAME)
        append_build_log(task_dir, f"=== Coder Agent starting for task #{task_id} ===")

        write_progress(task_dir, task_id, "planning", "Setting up workspace",
                       "Initializing git repository and workspace", "Creating task workspace...", 2.0)

        if not init_repo(task_dir):
            return {"action": "error", "error": "Failed to initialize git repo."}

        repo_url = create_github_repo(task_id, task_dir)
        if repo_url:
            log_ok(f"GitHub repo ready: {repo_url}", AGENT_NAME)
            state["repo_url"] = repo_url
        else:
            log_warn("GitHub repo creation failed — continuing with local git only.", AGENT_NAME)
            state["repo_url"] = get_repo_url(task_id)

        # ── STEP 3: Plan the implementation ───────────────────────────
        if not state.get("plan"):
            log_think("Planning implementation...", AGENT_NAME)
            write_progress(task_dir, task_id, "planning", "Analyzing requirements",
                           f"Breaking task into implementation steps (iteration {state.get('iterations', 0) + 1})",
                           "Architecting solution...", 5.0)

            plan = plan_implementation(title, desc, reqs, past_errors, poster_context, complexity=plan_complexity)
            if not plan or not plan.get("steps"):
                log_warn("Planning failed, falling back to single-step approach.", AGENT_NAME)
                plan = {
                    "project_type": "nextjs",
                    "scaffold_command": "npx create-next-app@latest ./ --typescript --tailwind --eslint --app --no-src-dir --import-alias @/* --yes",
                    "steps": [{"step_number": 1, "description": "Complete implementation", "commit_message": "feat: complete implementation", "files": []}],
                    "test_command": "echo 'No tests defined'",
                }

            state["plan"] = plan
            state["total_steps"] = len(plan.get("steps", []))
            state["test_command"] = plan.get("test_command", "echo 'No tests defined'")
            _save_state(state_file, state)

            # Commit the plan
            plan_file = task_dir / ".implementation_plan.json"
            plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
            h = commit_step(task_dir, "docs: add implementation plan")
            if h:
                append_commit_log(task_dir, h, "docs: add implementation plan")
                log_ok(f"Committed implementation plan [{h}]", AGENT_NAME)

            # Emit planning complete progress
            total = len(plan.get("steps", []))
            step_names = [s.get("description", f"Step {s.get('step_number', i+1)}") for i, s in enumerate(plan.get("steps", []))]
            write_progress(task_dir, task_id, "planning", "Implementation plan ready",
                           f"{total} steps planned: {' → '.join(step_names[:4])}{'...' if total > 4 else ''}",
                           f"Project type: {plan.get('project_type', 'unknown')}, {total} implementation steps",
                           10.0, metadata={"steps": total, "project_type": plan.get("project_type", "unknown")})
        else:
            plan = state["plan"]
            log_think(f"Resuming plan — {len(state.get('completed_steps', []))} of {state['total_steps']} steps done.", AGENT_NAME)

        # ── STEP 3: Scaffold (if needed) ──────────────────────────────
        scaffold_cmd = plan.get("scaffold_command")
        if scaffold_cmd and not state.get("scaffolded"):
            log_think(f"Scaffolding project: {scaffold_cmd}", AGENT_NAME)
            append_build_log(task_dir, f"Scaffold: {scaffold_cmd}")
            write_progress(task_dir, task_id, "execution", "Scaffolding project",
                           "Setting up project structure and boilerplate",
                           f"Running: {scaffold_cmd[:80]}", 15.0)

            # ── Clean up conflicting files before scaffolding ──
            # create-next-app fails if the directory is not empty.
            # We must move or remove files except state and lock.
            log_think("Cleaning up task directory for scaffolding...", AGENT_NAME)
            conflicting_files = [".build_log", ".dispatch_log", ".git", ".gitignore", "progress.jsonl", "tsconfig.json", "app", "components", "lib", "public", ".env", ".env.local"]
            for f in conflicting_files:
                p = task_dir / f
                if p.exists():
                    try:
                        if p.is_dir():
                            import shutil
                            shutil.rmtree(p)
                        else:
                            p.unlink()
                    except Exception as e:
                        log_warn(f"Could not remove {f} before scaffold: {e}", AGENT_NAME)

            rc, out = run_shell_combined(scaffold_cmd, task_dir, timeout=3600)
            log_command(task_dir, scaffold_cmd, rc, out)

            if rc == 0:
                h = commit_step(task_dir, f"chore: scaffold project ({plan.get('project_type', 'unknown')})")
                if h:
                    append_commit_log(task_dir, h, "chore: scaffold project")
                    log_ok(f"Scaffolded and committed [{h}]", AGENT_NAME)

                state["scaffolded"] = True
                _save_state(state_file, state)
            else:
                log_warn(f"Scaffold command failed (rc={rc}). Continuing anyway.", AGENT_NAME)
                state["scaffolded"] = True  # Don't retry
                _save_state(state_file, state)

        # ── STEP 4: Generate code for remaining Architectural blueprint ─
        log_think("Requesting architectural blueprint enhancement from Claude...", AGENT_NAME)
        write_progress(task_dir, task_id, "planning", "Enhancing architecture blueprint",
                       "AI is generating detailed architectural specification",
                       "Consulting Claude for deep technical blueprint...", 18.0)

        prompt = (
            f"You are the Coder Agent. We are building a solution for this task:\n"
            f"Title: {title}\nDescription: {desc}\nRequirements: {reqs}\n"
        )
        if past_errors:
            prompt += (
                f"\nCRITICAL: PREVIOUS TEST FAILED WITH THIS ERROR:\n{past_errors}\n"
                "YOU MUST RESOLVE THIS ISSUE WHATEVER IT TAKES. "
                "Do not simply retry the same code. If the current approach is failing, "
                "switch to a different architecture, change dependency versions, "
                "rewrite core logic, or try an entirely new technical strategy to bypass this blocker. "
                "You are empowered to be as aggressive as needed to achieve a passing build.\n"
            )

        enhanced_blueprint = claude_enhance_prompt(prompt)

        # Load skills — from the Oriexa skills dir AND from .claude/skills/ in both repos
        skill_contents = _load_skills_for_task(title, desc, reqs, plan)

        # ── STEP 5: Execute each step ─────────────────────────────────
        steps = plan.get("steps", [])
        completed_step_nums = {s["step_number"] for s in state.get("completed_steps", [])}
        existing_files = []

        # Collect files already written
        for s in state.get("completed_steps", []):
            existing_files.extend(s.get("files_written", []))

        for step in steps:
            step_num = step.get("step_number", 0)
            if step_num in completed_step_nums:
                continue  # Already done

            step_desc = step.get("description", f"Step {step_num}")
            commit_msg = step.get("commit_message", f"feat: {step_desc}")

            log_think(f"Step {step_num}/{len(steps)}: {step_desc}", AGENT_NAME)
            append_build_log(task_dir, f"Step {step_num}: {step_desc}")

            # Emit progress for this step starting
            step_pct = 20.0 + (step_num - 1) / max(len(steps), 1) * 60.0
            write_progress(task_dir, task_id, "execution",
                           f"Step {step_num}/{len(steps)}: {step_desc}",
                           f"Generating code for: {step_desc}",
                           f"Writing files for step {step_num}...",
                           step_pct, subtask_id=step_num,
                           metadata={"step": step_num, "total_steps": len(steps)})

            # Generate code for this step
            files = generate_step_code(
                step, title, desc, reqs, enhanced_blueprint,
                existing_files, skill_contents, poster_context, task_dir=task_dir,
                complexity=plan_complexity
            )

            if not files:
                log_warn(f"Step {step_num} generated no files — skipping.", AGENT_NAME)
                continue

            # Write files to disk
            files_written = []
            for f in files:
                file_path = task_dir / f["path"]
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(f["content"], encoding="utf-8")
                files_written.append(f["path"])
                existing_files.append(f["path"])

            log_think(f"  Wrote {len(files_written)} files: {', '.join(files_written[:5])}", AGENT_NAME)

            # Commit this step
            h = commit_step(task_dir, commit_msg)
            if h:
                append_commit_log(task_dir, h, commit_msg)
                log_ok(f"  Committed [{h}]: {commit_msg}", AGENT_NAME)

                # Push every few commits
                if should_push(task_dir):
                    push_to_remote(task_dir)
                    log_ok("  Pushed to GitHub", AGENT_NAME)

            # Emit step completed progress
            step_pct_done = 20.0 + step_num / max(len(steps), 1) * 60.0
            write_progress(task_dir, task_id, "execution",
                           f"Step {step_num} complete: {step_desc}",
                           f"Wrote {len(files_written)} files and committed",
                           f"Committed: {commit_msg}",
                           step_pct_done, subtask_id=step_num,
                           metadata={"files_written": files_written[:5], "commit": h or ""})

            # Track completed step
            state["current_step"] = step_num
            state["completed_steps"].append({
                "step_number": step_num,
                "description": step_desc,
                "commit": h,
                "files_written": files_written,
            })
            state["files"].extend(files)
            _save_state(state_file, state)

        # ── STEP 6: Install dependencies ──────────────────────────────
        if (task_dir / "package.json").exists():
            log_think("Installing npm dependencies...", AGENT_NAME)
            write_progress(task_dir, task_id, "review", "Installing dependencies",
                           "Running npm install to install project dependencies",
                           "npm install running...", 83.0)
            rc, out = run_npm_install(task_dir)
            log_command(task_dir, "npm install", rc, out)
            if rc == 0:
                log_ok("npm install succeeded.", AGENT_NAME)
                write_progress(task_dir, task_id, "review", "Dependencies installed",
                               "npm install completed successfully",
                               "All packages installed", 86.0)
            else:
                log_warn(f"npm install failed (rc={rc})", AGENT_NAME)

        # ── STEP 7: Final push ────────────────────────────────────────
        write_progress(task_dir, task_id, "delivery", "Pushing code",
                       "Pushing all commits to GitHub repository",
                       f"Pushing to {state.get('repo_url', 'GitHub')}...", 90.0)
        push_to_remote(task_dir)
        log_ok(f"All code pushed to {state.get('repo_url', 'GitHub')}", AGENT_NAME)

        write_progress(task_dir, task_id, "delivery", "Code complete",
                       "All implementation steps completed and pushed",
                       f"Repository: {state.get('repo_url', 'local git')}",
                       95.0, metadata={"repo_url": state.get("repo_url", "")})

        # ── Transition to testing ─────────────────────────────────────
        total_files = sum(len(s.get("files_written", [])) for s in state.get("completed_steps", []))
        total_commits = len(state.get("commit_log", []))

        # Validate: ensure we actually generated real source files
        if total_files == 0:
            log_err(f"CRITICAL: No source files were generated for task #{task_id}! "
                    f"All {len(steps)} steps produced empty output.", AGENT_NAME)
            
            # Infinite Resilience: Increment iterations and reset state to retry autonomously
            state["iterations"] = state.get("iterations", 0) + 1
            
            # Reset completed steps and plan to force a fresh start in the next tick
            log_warn(f"AUTONOMOUS RESET: Resetting plan to retry code generation (iteration {state['iterations']})", AGENT_NAME)
            state["plan"] = None
            state["completed_steps"] = []
            state["current_step"] = 0
            state["scaffolded"] = False
            
            # If we've failed many times, maybe the workspace is corrupted — we could flag for a wipe here
            # but for now we just reset the agent's internal state.
            
            _save_state(state_file, state)
            return {"action": "error", "error": f"No source files generated (attempt {state['iterations']}) — autonomously resetting"}

        state["status"] = "testing"
        state["iterations"] = state.get("iterations", 0) + 1
        _save_state(state_file, state)

        total_files = sum(len(s.get("files_written", [])) for s in state.get("completed_steps", []))
        total_commits = len(state.get("commit_log", []))

        log_ok(
            f"Coding complete for task #{task_id} — "
            f"{total_files} files, {total_commits} commits, "
            f"{len(state.get('completed_steps', []))} steps",
            AGENT_NAME
        )

        return {
            "action": "coded",
            "task_id": task_id,
            "files_written": total_files,
            "commits": total_commits,
            "repo_url": state.get("repo_url"),
        }

    except Exception as e:
        log_err(f"Exception during coding: {e}")
        log_err(traceback.format_exc().strip().splitlines()[-1])
        return {"action": "error", "error": str(e)}


def _save_state(state_file: Path, state: dict):
    """Save state to disk."""
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--task-id", type=int, required=True)
    args = parser.parse_args()

    client = OriexaClient(args.base_url, args.api_key)
    result = process_task(client, args.task_id)
    print(f"\n__RESULT__:{json.dumps(result, ensure_ascii=True)}")

if __name__ == "__main__":
    main()
