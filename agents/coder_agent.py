"""
Oriexa Coder Agent â€” Shell-Based, Step-by-Step Code Generator

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
    has_meaningful_implementation,
    verify_remote_has_main,
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# PROGRESS EMITTER â€” writes ProgressStep JSON to progress.jsonl
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# STEP 1: PLAN â€” Break the task into implementation steps
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

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
        "You are a world-class Software Architect AI agent. "
        "Given a task, you break it into implementable steps with DETAILED descriptions. "
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n\n"
        "CRITICAL â€” PROJECT TYPE RULES (STRICTLY ENFORCED):\n"
        "- ALWAYS prioritize the latest versions of all technologies, frameworks, and tools.\n"
        "- CRITICAL: ALWAYS use the '@latest' tag for npx/npm/pip commands and specify 'latest' for all dependency versions in package.json/requirements.txt. NEVER use specific version numbers unless absolutely necessary to fix a known bug.\n"
        "- BE PROACTIVE: If you encounter an error, version conflict, or build failure, RESOLVE IT WHATEVER IT TAKES. You are empowered to change the project structure, switch tools, or adopt a completely different technical approach to bypass the blocker.\n"
        "- You MUST ONLY use JavaScript/TypeScript frontend or fullstack frameworks.\n"
        "- DEFAULT to 'nextjs' for ALL tasks: websites, web apps, dashboards, "
        "landing pages, portfolios, e-commerce, SaaS, tools with a UI, APIs, backends â€” everything.\n"
        "- Use 'react' ONLY if the task explicitly says 'React without Next.js' or 'Vite + React'.\n"
        "- Use 'vite' ONLY if the task explicitly specifies Vite as the build tool.\n"
        "- Use 'static' ONLY for pure HTML/CSS/JS with absolutely no framework needed "
        "(e.g. the user asks for vanilla JS, plain HTML page, or a simple static site).\n"
        "- NEVER use 'python' â€” Python is FORBIDDEN as a project type.\n"
        "- NEVER use 'node' standalone â€” if backend is needed, use Next.js API routes.\n"
        "- Backend logic MUST live inside the framework (Next.js API routes, server actions).\n"
        "- NO external database connections â€” use in-memory state or localStorage only.\n"
        "- When in doubt: choose 'nextjs'. It is ALWAYS the safe default. The NEXTJS framework MUST be prioritized before you proceed with implementation.\n"
        "- For 'nextjs' always use scaffold_command: "
        "'npx create-next-app@latest ./ --typescript --tailwind --eslint --app --no-src-dir --import-alias @/* --yes --force'\n\n"
        "CRITICAL â€” STEP DESCRIPTION RULES:\n"
        "- Each step's 'description' MUST be a DETAILED paragraph (3-5 sentences minimum) "
        "explaining exactly what to implement, the visual design, behavior, and any edge cases.\n"
        "- For STATIC (vanilla HTML/CSS/JS) projects: describe the EXACT HTML structure, "
        "CSS styling approach (colors, fonts, layout), JavaScript behavior (event handlers, "
        "DOM manipulation), and how each file connects. The description must be detailed enough "
        "that a developer could implement it without seeing the task requirements again.\n"
        "- For NEXTJS / REACT projects: describe the component hierarchy, state management, "
        "props, hooks to use, styling approach (Tailwind classes), responsive breakpoints, "
        "animations, and API routes if needed. Each step must produce a COMPLETE, polished feature.\n"
        "- NEVER have vague descriptions like 'Set up project'. Instead: 'Create the root layout "
        "with Inter font, dark theme support, global CSS variables, and a responsive container'.\n\n"
        "CRITICAL â€” FILE LIST RULES:\n"
        "- Every step MUST list at least 2 files to create.\n"
        "- Each file must have a 'path' (relative) and 'description' (detailed: what it renders, "
        "what styles it applies, what interactivity it provides).\n"
        "- Be specific: e.g. 'app/page.tsx', 'components/Hero.tsx', 'app/api/data/route.ts'.\n"
        "- For static projects: ALWAYS include 'index.html' as the main entry point.\n"
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
        '      "description": "DETAILED PARAGRAPH describing exactly what to build, the visual design, behavior, and technical approach. At least 3-5 sentences.",\n'
        '      "commit_message": "chore: add project configuration",\n'
        '      "files": [\n'
        '        {"path": "app/layout.tsx", "description": "Root layout with Inter font, dark theme, responsive container, and global metadata"},\n'
        '        {"path": "app/page.tsx", "description": "Main page with hero section, feature cards, and call-to-action"}\n'
        '      ]\n'
        '  ],\n'
        '  "test_command": "npm run build"\n'
        '}\n'
    )

    result = llm_json(system, user, max_tokens=2048, complexity=complexity, provider="claude-sonnet")

    # â”€â”€ Post-processing: enforce project type and scaffold â”€â”€
    if isinstance(result, dict):
        project_type = (result.get("project_type") or "").lower().strip()
        # Force nextjs for forbidden types
        if project_type in ("node", "python", "express", "flask", "django", "") or project_type not in ("nextjs", "react", "vite", "static"):
            log_warn(f"Plan used forbidden project_type '{project_type}' â€” forcing 'nextjs'", AGENT_NAME)
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
        "You are a world-class Senior Fullstack Developer producing PRODUCTION-READY, "
        "POLISHED, COMPLETE code. You write code that compiles and runs perfectly on first try.\n"
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n"
        "Your response must be a JSON object with a single 'files' array.\n"
        "Each file has 'path' (relative) and 'content' (the COMPLETE, FULL source code).\n\n"
        "QUALITY RULES (STRICTLY ENFORCED):\n"
        "- Every file MUST have COMPLETE, REAL, WORKING code. NEVER return empty content.\n"
        "- For HTML files: include DOCTYPE, full head section with meta, title, linked stylesheets, "
        "and a complete body with semantic structure. The page must be visually appealing.\n"
        "- For CSS files: include a full design system â€” colors, typography, spacing, responsive "
        "breakpoints, hover effects, transitions. Make it look PROFESSIONAL, not bare-bones.\n"
        "- For JS files: include complete logic with proper error handling, event listeners, "
        "DOM manipulation, and comments explaining complex sections.\n"
        "- For React/Next.js: use proper TypeScript types, 'use client' directive where needed, "
        "proper imports, hooks, responsive Tailwind classes, and accessible HTML.\n"
        "- NEVER use placeholder text like 'TODO' or 'Add your code here'. Write the actual code.\n"
        "- NEVER import components or modules that don't exist in the project.\n"
        "- All code must be SELF-CONTAINED and FUNCTIONAL â€” it should work immediately."
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
        "Return JSON: {\"files\": [{\"path\": \"...\", \"content\": \"...\"}]}\n\n"
        "IMPORTANT REMINDERS:\n"
        "- Each file's 'content' MUST be complete, working source code â€” NOT fragments.\n"
        "- For static projects: HTML must be a full valid document with linked CSS/JS.\n"
        "- For Next.js/React: components must compile cleanly with proper imports and types.\n"
        "- Write code that a developer would be PROUD to ship. Quality over speed."
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# SKILL LOADER â€” Loads relevant skills based on task characteristics
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# Map of keyword patterns â†’ skill SKILL.md file names to include
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

    # 1. Load Oriexa API skill files (all of them â€” they're small)
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
                    # Trim to avoid token overflow â€” take first 1500 chars
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# FIX-ONLY MODE â€” Targeted error repair (no full re-gen)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _fix_build_errors(
    error_output: str,
    title: str,
    desc: str,
    reqs: str,
    blueprint: str,
    existing_files: list[str],
    skill_contents: list[str],
    poster_context: str,
    task_dir: Path,
    complexity: str = "high",
) -> list[dict]:
    """
    Given build/test error output, generate ONLY the fixed files.
    Reads the current broken files from disk, sends them + errors to the LLM,
    and gets back corrected versions. Does NOT regenerate files without errors.
    """
    import re

    # Extract file paths mentioned in the error output
    error_files: set[str] = set()
    # Match common error patterns: "./app/page.tsx(12,5):" or "Error in app/page.tsx" or "./app/page.tsx:12:5"
    for pattern in [
        r'[./]*([a-zA-Z0-9_/.-]+\.[a-zA-Z]+)\s*[\(:]\d+',    # file.tsx(12,5) or file.tsx:12:5
        r'Error.*?[./]*([a-zA-Z0-9_/.-]+\.[a-zA-Z]+)',        # Error in file.tsx
        r"Module not found.*?'([^']+)'",                       # Module not found: './something'
    ]:
        for match in re.finditer(pattern, error_output):
            fpath = match.group(1).lstrip('./')
            if fpath and not fpath.startswith('node_modules') and '.' in fpath:
                error_files.add(fpath)

    if not error_files:
        # Fallback: if we can't parse specific files, fix the main entry points
        for candidate in ["app/page.tsx", "app/layout.tsx", "pages/index.tsx"]:
            if (task_dir / candidate).exists():
                error_files.add(candidate)

    if not error_files:
        log_warn("Could not identify broken files from error output", AGENT_NAME)
        return []

    log_think(f"Fix-only: targeting {len(error_files)} file(s): {', '.join(list(error_files)[:8])}", AGENT_NAME)

    # Read current content of broken files
    file_contents = {}
    for fpath in error_files:
        full_path = task_dir / fpath
        if full_path.exists():
            try:
                file_contents[fpath] = full_path.read_text(encoding="utf-8")
            except Exception:
                pass

    # Build the fix prompt
    system = (
        "You are a Senior Developer fixing build errors. "
        "You will receive error output and the current source files. "
        "Fix ONLY the errors â€” do NOT rewrite files from scratch. "
        "Keep all existing functionality intact. Only modify what's broken. "
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n"
        "Return: {\"files\": [{\"path\": \"...\", \"content\": \"...\"}]}\n"
        "Each file must have the COMPLETE corrected source code."
    )

    files_section = ""
    for fpath, content in file_contents.items():
        # Limit content to avoid token overflow
        truncated = content[:4000] if len(content) > 4000 else content
        files_section += f"\n--- {fpath} ---\n{truncated}\n"

    user = (
        f"Fix these build errors:\n\n"
        f"ERROR OUTPUT:\n{error_output[-3000:]}\n\n"
        f"CURRENT FILES:\n{files_section}\n\n"
        f"Task: {title}\nDescription: {desc[:500]}\n\n"
        "Return the corrected files as JSON: {\"files\": [{\"path\": \"...\", \"content\": \"...\"}]}\n"
        "IMPORTANT: Only return files that need changes. Keep all existing code intact. "
        "Fix the specific errors shown above."
    )

    result = llm_json(system, user, max_tokens=16384, complexity=complexity)
    files = result.get("files", []) if isinstance(result, dict) else []
    valid_files = [
        f for f in files
        if isinstance(f, dict) and f.get("path") and f.get("content", "").strip() and len(f.get("content", "").strip()) > 20
    ]

    if not valid_files and "_raw" in result:
        debug_file = task_dir / ".llm_debug_fix.txt"
        debug_file.write_text(result["_raw"], encoding="utf-8")
        log_warn("Fix-only LLM returned invalid JSON. Saved debug output.", AGENT_NAME)

    return valid_files


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MAIN PROCESS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

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

        # Hard retry cap: block deployment instead of force-advancing bad code.
        MAX_CODING_ITERATIONS = 5
        iteration = state.get("iterations", 0)
        if iteration >= MAX_CODING_ITERATIONS:
            log_warn(
                f"Hit max coding iterations ({MAX_CODING_ITERATIONS}). "
                "Blocking further advancement until implementation is fixed.",
                AGENT_NAME,
            )
            state["status"] = "coding"
            state["test_errors"] = (
                "Max coding iterations reached. Deployment blocked until meaningful implementation succeeds."
            )
            _save_state(state_file, state)
            return {"action": "error", "task_id": task_id, "error": "max_coding_iterations_reached"}

        # Recover from stale state snapshots that mark steps complete with no code.
        if state.get("completed_steps") and not has_meaningful_implementation(task_dir):
            log_warn(
                "Stale state detected: completed_steps exist but repo has no meaningful files. Resetting coder state.",
                AGENT_NAME,
            )
            state["current_step"] = 0
            state["total_steps"] = 0
            state["completed_steps"] = []
            state["files"] = []
            state["plan"] = None
            state["cached_blueprint"] = ""
            _save_state(state_file, state)

        title = task.get("title") or ""
        desc = task.get("description") or ""
        reqs = task.get("requirements") or ""
        past_errors = state.get("test_errors", "")

        # â”€â”€ Progressive Intelligence Escalation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Iteration 0: high (default)
        # Iteration 1: high (same model, targeted fix)
        # Iteration 2+: extreme (upgrade to best available model)
        plan_complexity = "high"
        if iteration >= 2:
            log_warn(f"Escalating to 'extreme' intelligence (iteration {iteration})", AGENT_NAME)
            plan_complexity = "extreme"

        # â”€â”€ Fetch poster conversation context â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        # â”€â”€ STEP 1: Git Repo (Create FIRST, before any code) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
            return {"action": "error", "error": "GitHub repository creation/push failed"}

        # â”€â”€ STEP 3: Plan the implementation (ONCE â€” never re-plan) â”€â”€â”€
        if not state.get("plan"):
            log_think("Planning implementation (Claude Sonnet â€” one-time plan)...", AGENT_NAME)
            write_progress(task_dir, task_id, "planning", "Analyzing requirements",
                           "Breaking task into implementation steps",
                           "Architecting solution with Claude Sonnet...", 5.0)

            # Always use claude-sonnet for the plan â€” this only runs once
            plan = plan_implementation(title, desc, reqs, "", poster_context, complexity="high")
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

            total = len(plan.get("steps", []))
            step_names = [s.get("description", f"Step {s.get('step_number', i+1)}") for i, s in enumerate(plan.get("steps", []))]
            write_progress(task_dir, task_id, "planning", "Implementation plan ready",
                           f"{total} steps planned: {' â†’ '.join(step_names[:4])}{'...' if total > 4 else ''}",
                           f"Project type: {plan.get('project_type', 'unknown')}, {total} implementation steps",
                           10.0, metadata={"steps": total, "project_type": plan.get("project_type", "unknown")})
        else:
            plan = state["plan"]
            log_think(f"Resuming plan â€” {len(state.get('completed_steps', []))} of {state['total_steps']} steps done.", AGENT_NAME)

        # â”€â”€ STEP 3: Scaffold (if needed) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        scaffold_cmd = plan.get("scaffold_command")
        if scaffold_cmd and not state.get("scaffolded"):
            log_think(f"Scaffolding project: {scaffold_cmd}", AGENT_NAME)
            append_build_log(task_dir, f"Scaffold: {scaffold_cmd}")
            write_progress(task_dir, task_id, "execution", "Scaffolding project",
                           "Setting up project structure and boilerplate",
                           f"Running: {scaffold_cmd[:80]}", 15.0)

            # â”€â”€ Clean up conflicting files before scaffolding â”€â”€
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

        # â”€â”€ STEP 4: Architectural blueprint (cached â€” only generate once) â”€
        enhanced_blueprint = state.get("cached_blueprint", "")
        if not enhanced_blueprint:
            log_think("Generating architectural blueprint (one-time, Claude)...", AGENT_NAME)
            write_progress(task_dir, task_id, "planning", "Enhancing architecture blueprint",
                           "AI is generating detailed architectural specification",
                           "Consulting Claude for deep technical blueprint...", 18.0)

            prompt = (
                f"You are the Coder Agent. We are building a solution for this task:\n"
                f"Title: {title}\nDescription: {desc}\nRequirements: {reqs}\n"
            )
            enhanced_blueprint = claude_enhance_prompt(prompt)
            state["cached_blueprint"] = enhanced_blueprint
            _save_state(state_file, state)
        else:
            log_think("Using cached architectural blueprint (skipping LLM call)", AGENT_NAME)

        # Load skills â€” from the Oriexa skills dir AND from .claude/skills/ in both repos
        skill_contents = _load_skills_for_task(title, desc, reqs, plan)

        # â”€â”€ STEP 5: Execute steps OR fix errors â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        steps = plan.get("steps", [])
        completed_step_nums = {s["step_number"] for s in state.get("completed_steps", [])}
        existing_files = []

        # Collect files already written
        for s in state.get("completed_steps", []):
            existing_files.extend(s.get("files_written", []))

        # â”€â”€ FIX-ONLY MODE: If we have test_errors AND all steps are done,
        #    only fix the broken files instead of regenerating everything.
        if past_errors and len(completed_step_nums) == len(steps) and len(completed_step_nums) > 0:
            log_think(f"Fix-only mode: all {len(steps)} steps already completed. Fixing build errors...", AGENT_NAME)
            write_progress(task_dir, task_id, "execution", "Fixing build errors",
                           "Targeted fix â€” only rewriting files with errors",
                           "Analyzing error output to identify broken files...", 75.0)

            fix_files = _fix_build_errors(
                past_errors, title, desc, reqs, enhanced_blueprint,
                existing_files, skill_contents, poster_context, task_dir,
                plan_complexity,
            )
            if fix_files:
                files_written = []
                for f in fix_files:
                    file_path = task_dir / f["path"]
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(f["content"], encoding="utf-8")
                    files_written.append(f["path"])

                log_ok(f"Fixed {len(files_written)} files: {', '.join(files_written[:5])}", AGENT_NAME)
                h = commit_step(task_dir, f"fix: resolve build errors (iteration {iteration + 1})")
                if h:
                    append_commit_log(task_dir, h, "fix: resolve build errors")
                    push_to_remote(task_dir)
                    log_ok(f"Fix committed [{h}] and pushed", AGENT_NAME)
            else:
                log_warn(
                    "Fix-only mode produced no files. Resetting state so next run performs a full re-plan and implementation.",
                    AGENT_NAME,
                )
                state["current_step"] = 0
                state["total_steps"] = 0
                state["completed_steps"] = []
                state["files"] = []
                state["plan"] = None
                state["cached_blueprint"] = ""
                _save_state(state_file, state)
                return {"action": "error", "error": "fix_only_no_files_reset_state"}

        else:
            # â”€â”€ Normal mode: execute remaining steps â”€â”€
            for step in steps:
                step_num = step.get("step_number", 0)
                if step_num in completed_step_nums:
                    continue  # Already done

                step_desc = step.get("description", f"Step {step_num}")
                commit_msg = step.get("commit_message", f"feat: {step_desc}")

                log_think(f"Step {step_num}/{len(steps)}: {step_desc}", AGENT_NAME)
                append_build_log(task_dir, f"Step {step_num}: {step_desc}")

                step_pct = 20.0 + (step_num - 1) / max(len(steps), 1) * 60.0
                write_progress(task_dir, task_id, "execution",
                               f"Step {step_num}/{len(steps)}: {step_desc}",
                               f"Generating code for: {step_desc}",
                               f"Writing files for step {step_num}...",
                               step_pct, subtask_id=step_num,
                               metadata={"step": step_num, "total_steps": len(steps)})

                files = generate_step_code(
                    step, title, desc, reqs, enhanced_blueprint,
                    existing_files, skill_contents, poster_context, task_dir=task_dir,
                    complexity=plan_complexity
                )

                if not files:
                    log_warn(f"Step {step_num} generated no files â€” skipping.", AGENT_NAME)
                    continue

                files_written = []
                for f in files:
                    file_path = task_dir / f["path"]
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(f["content"], encoding="utf-8")
                    files_written.append(f["path"])
                    existing_files.append(f["path"])

                log_think(f"  Wrote {len(files_written)} files: {', '.join(files_written[:5])}", AGENT_NAME)

                h = commit_step(task_dir, commit_msg)
                if h:
                    append_commit_log(task_dir, h, commit_msg)
                    log_ok(f"  Committed [{h}]: {commit_msg}", AGENT_NAME)
                    if should_push(task_dir):
                        push_to_remote(task_dir)
                        log_ok("  Pushed to GitHub", AGENT_NAME)

                step_pct_done = 20.0 + step_num / max(len(steps), 1) * 60.0
                write_progress(task_dir, task_id, "execution",
                               f"Step {step_num} complete: {step_desc}",
                               f"Wrote {len(files_written)} files and committed",
                               f"Committed: {commit_msg}",
                               step_pct_done, subtask_id=step_num,
                               metadata={"files_written": files_written[:5], "commit": h or ""})

                state["current_step"] = step_num
                state["completed_steps"].append({
                    "step_number": step_num,
                    "description": step_desc,
                    "commit": h,
                    "files_written": files_written,
                })
                state["files"].extend(files)
                _save_state(state_file, state)

        # â”€â”€ STEP 6: Install dependencies â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        # â”€â”€ STEP 7: Final push â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        write_progress(task_dir, task_id, "delivery", "Pushing code",
                       "Pushing all commits to GitHub repository",
                       f"Pushing to {state.get('repo_url', 'GitHub')}...", 90.0)
        push_ok = push_to_remote(task_dir)
        if not push_ok:
            log_warn("Final push to GitHub failed.", AGENT_NAME)

        if not has_meaningful_implementation(task_dir):
            state["status"] = "coding"
            state["test_errors"] = (
                "Implementation quality gate failed: repository only has housekeeping files "
                "(e.g. .gitignore) and no real code/artifacts."
            )
            _save_state(state_file, state)
            return {"action": "error", "error": "No meaningful implementation files found"}

        if not verify_remote_has_main(task_dir):
            state["status"] = "coding"
            state["test_errors"] = (
                "GitHub sync gate failed: remote origin/main branch is missing. "
                "Code must be pushed before delivery."
            )
            _save_state(state_file, state)
            return {"action": "error", "error": "GitHub remote main branch not found"}

        log_ok(f"All code pushed to {state.get('repo_url', 'GitHub')}", AGENT_NAME)

        write_progress(task_dir, task_id, "delivery", "Code complete",
                       "All implementation steps completed and pushed",
                       f"Repository: {state.get('repo_url', 'local git')}",
                       95.0, metadata={"repo_url": state.get("repo_url", "")})

        # â”€â”€ Transition to testing (NEVER wipe plan or completed steps) â”€
        state["status"] = "testing"
        state["iterations"] = iteration + 1
        _save_state(state_file, state)

        total_files = sum(len(s.get("files_written", [])) for s in state.get("completed_steps", []))
        total_commits = len(state.get("commit_log", []))

        log_ok(
            f"Coding complete for task #{task_id} â€” "
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# CLI
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

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

