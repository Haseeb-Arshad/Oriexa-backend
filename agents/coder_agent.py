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
import re
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
    verify_remote_head_matches_local,
)
from agents.shell_executor import (
    run_shell_combined,
    run_npm_install,
    append_build_log,
    log_command,
    summarize_failure_output,
)
from app.services.agent_workspaces import (
    ensure_local_workspace,
    load_swarm_state,
    write_swarm_state,
)

AGENT_NAME = "Coder"
WORKSPACE_DIR = Path(os.environ.get("AGENT_WORKSPACE_DIR", str(Path(__file__).parent.parent / "agent_works")))
DEFAULT_NEXT_SCAFFOLD_COMMAND = (
    "npx create-next-app@latest ./ --typescript --tailwind --eslint "
    "--app --no-src-dir --import-alias @/* --yes --force --no-git"
)
NEXT15_SCAFFOLD_COMMAND = (
    "npx create-next-app@15 ./ --typescript --tailwind --eslint "
    "--app --no-src-dir --import-alias @/* --yes --force --no-git"
)
SCAFFOLD_TIMEOUT_SECONDS = int(os.environ.get("SCAFFOLD_TIMEOUT_SECONDS", "7200"))


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


def _parse_node_major(raw: str) -> int | None:
    match = re.search(r"v(\d+)", raw or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _detect_node_major(task_dir: Path) -> int | None:
    rc, output = run_shell_combined("node -v", task_dir, timeout=30)
    if rc != 0:
        return None
    return _parse_node_major(output)


def _looks_like_engine_mismatch(output: str) -> bool:
    lowered = (output or "").lower()
    return (
        "ebadengine" in lowered
        or "unsupported engine" in lowered
        or "requires node" in lowered
    )


def _cleanup_scaffold_artifacts(task_dir: Path) -> None:
    conflicting_files = [
        ".build_log",
        ".dispatch_log",
        ".agent_lock",
        ".implementation_plan.json",
        ".swarm_state.json",
        ".gitignore",
        "progress.jsonl",
        "README.md",
        "tsconfig.json",
        "next-env.d.ts",
        "next.config.js",
        "next.config.ts",
        "next.config.mjs",
        "postcss.config.js",
        "postcss.config.mjs",
        "eslint.config.js",
        "eslint.config.mjs",
        ".eslintrc.json",
        "jsconfig.json",
        "app",
        "components",
        "lib",
        "public",
        ".env",
        ".env.local",
        "node_modules",
        "package-lock.json",
        "package.json",
    ]
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


def _normalize_scaffold_command(scaffold_cmd: str, task_dir: Path) -> str:
    if "create-next-app" not in scaffold_cmd:
        return scaffold_cmd

    normalized_cmd = scaffold_cmd
    if "--no-git" not in normalized_cmd:
        normalized_cmd = f"{normalized_cmd} --no-git"

    node_major = _detect_node_major(task_dir)
    if node_major is not None and node_major < 20:
        normalized = re.sub(r"create-next-app@[^ ]+", "create-next-app@15", normalized_cmd)
        if normalized == normalized_cmd and "create-next-app@" not in normalized_cmd:
            normalized = normalized_cmd.replace("create-next-app", "create-next-app@15", 1)
        if normalized != normalized_cmd:
            log_think(
                f"Detected Node.js v{node_major}; using create-next-app@15 for runtime compatibility",
                AGENT_NAME,
            )
        return normalized

    return normalized_cmd


def _run_scaffold_command(scaffold_cmd: str, task_dir: Path) -> tuple[str, int, str]:
    effective_cmd = _normalize_scaffold_command(scaffold_cmd, task_dir)
    rc, out = run_shell_combined(effective_cmd, task_dir, timeout=SCAFFOLD_TIMEOUT_SECONDS)

    if rc == 0:
        return effective_cmd, rc, out

    if _looks_like_engine_mismatch(out) and "create-next-app@15" not in effective_cmd:
        fallback_cmd = re.sub(r"create-next-app@[^ ]+", "create-next-app@15", effective_cmd)
        if fallback_cmd == effective_cmd and "create-next-app@" not in effective_cmd:
            fallback_cmd = effective_cmd.replace("create-next-app", "create-next-app@15", 1)
        if fallback_cmd != effective_cmd:
            log_warn(
                "Scaffold hit a Node engine mismatch; retrying with create-next-app@15",
                AGENT_NAME,
            )
            append_build_log(task_dir, "Scaffold engine mismatch detected; retrying with create-next-app@15")
            _cleanup_scaffold_artifacts(task_dir)
            rc, out = run_shell_combined(fallback_cmd, task_dir, timeout=SCAFFOLD_TIMEOUT_SECONDS)
            return fallback_cmd, rc, out

    return effective_cmd, rc, out


def _workspace_integrity_issues(task_dir: Path, state: dict | None = None) -> list[str]:
    issues: list[str] = []
    pkg_path = task_dir / "package.json"
    lock_path = task_dir / "package-lock.json"
    plan = (state or {}).get("plan") or {}
    project_type = str(plan.get("project_type") or "").lower()

    if lock_path.exists() and not pkg_path.exists():
        issues.append("package-lock.json exists but package.json is missing")

    if (state or {}).get("scaffolded") and not pkg_path.exists():
        issues.append("workspace is marked scaffolded but package.json is missing")

    if project_type in {"nextjs", "react", "vite"} and not pkg_path.exists():
        issues.append(f"{project_type} project is missing package.json")

    if not pkg_path.exists():
        return issues

    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except Exception:
        return issues + ["package.json is unreadable or invalid JSON"]

    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    def _major(version_spec: str | None) -> int | None:
        match = re.search(r"(\d+)", str(version_spec or ""))
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    if project_type == "nextjs":
        for dep_name in ("next", "react", "react-dom"):
            if dep_name not in deps:
                issues.append(f"package.json is missing required dependency '{dep_name}'")

        react_major = _major(deps.get("react"))
        react_dom_major = _major(deps.get("react-dom"))
        if react_major and react_dom_major and react_major != react_dom_major:
            issues.append(
                f"package.json has mismatched React majors: react={deps.get('react')} react-dom={deps.get('react-dom')}"
            )

        if not any((task_dir / candidate).exists() for candidate in ("app", "src/app", "pages")):
            issues.append("Next.js workspace is missing app/, src/app/, or pages/")

    return issues


def _reset_corrupt_workspace(task_dir: Path, state: dict, issues: list[str]) -> dict:
    log_warn(
        "Workspace integrity check failed. Resetting for a clean re-scaffold: "
        + "; ".join(issues),
        AGENT_NAME,
    )
    append_build_log(task_dir, "Workspace integrity reset: " + "; ".join(issues))
    _cleanup_scaffold_artifacts(task_dir)
    state["status"] = "coding"
    state["scaffolded"] = False
    state["current_step"] = 0
    state["completed_steps"] = []
    state["files"] = []
    state["test_errors"] = "Workspace was auto-reset because project structure became invalid."
    if not state.get("plan"):
        state["total_steps"] = 0
    return state


GENERIC_STEP_PATTERNS = (
    "complete implementation",
    "implement the task",
    "finish the app",
    "build the project",
)


def _summarize_focus(*parts: str, max_words: int = 8) -> str:
    text = " ".join(part.strip() for part in parts if part).strip()
    if not text:
        return "the requested experience"
    sentence = re.split(r"[.!?\n]", text, maxsplit=1)[0]
    sentence = re.sub(
        r"^(create|build|implement|design|develop|set up|setup|finish|complete)\s+",
        "",
        sentence,
        flags=re.IGNORECASE,
    )
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", sentence)
    if not words:
        return "the requested experience"
    return " ".join(words[:max_words]).lower()


def _is_generic_step_description(description: str) -> bool:
    normalized = re.sub(r"\s+", " ", (description or "").strip()).lower()
    if not normalized:
        return True
    if any(pattern in normalized for pattern in GENERIC_STEP_PATTERNS):
        return True
    return len(normalized.split()) < 6


def _derive_commit_message(
    proposed: str | None,
    step_desc: str,
    files: list[dict] | None = None,
) -> str:
    normalized = re.sub(r"\s+", " ", (proposed or "").strip())
    if normalized and not _is_generic_step_description(normalized.replace("feat:", "").replace("fix:", "")):
        return normalized

    file_paths = [str(f.get("path", "")).replace("\\", "/") for f in (files or []) if isinstance(f, dict)]
    focus = _summarize_focus(step_desc)

    if any(path.endswith(("package.json", "tsconfig.json", "next.config.ts", "next.config.js")) for path in file_paths):
        return "chore: configure compatible project foundation"
    if any(path.endswith(("app/layout.tsx", "app/globals.css")) for path in file_paths):
        return "feat: establish application shell"
    if any(path.endswith(("app/page.tsx", "pages/index.tsx")) for path in file_paths):
        return f"feat: build {focus}"
    if any("/components/" in f"/{path}" or path.startswith("components/") for path in file_paths):
        return f"feat: add UI for {focus}"
    if "fix" in step_desc.lower() or "error" in step_desc.lower():
        return f"fix: resolve {focus}"
    return f"feat: implement {focus}"


def _build_fallback_plan(title: str, desc: str, reqs: str, past_errors: str = "") -> dict:
    focus = _summarize_focus(title, desc, reqs)
    error_hint = ""
    if past_errors:
        error_hint = (
            " Account for the latest failure context while choosing dependency versions, imports, "
            "and build tooling so the next test pass does not repeat the same blocker."
        )
    return {
        "project_type": "nextjs",
        "scaffold_command": DEFAULT_NEXT_SCAFFOLD_COMMAND,
        "steps": [
            {
                "step_number": 1,
                "description": (
                    f"Establish a compatible Next.js foundation for {focus}. Create the root layout, "
                    "global styling primitives, and any package or config updates required for a clean "
                    "install and production build. Make the app shell responsive and production-ready so "
                    "later feature work lands on stable scaffolding."
                    f"{error_hint}"
                ),
                "commit_message": "chore: configure compatible project foundation",
                "files": [
                    {"path": "app/layout.tsx", "description": "Application shell, metadata, and shared layout structure."},
                    {"path": "app/globals.css", "description": "Global design tokens, layout rules, and responsive styling baseline."},
                ],
            },
            {
                "step_number": 2,
                "description": (
                    f"Implement the main {focus} experience in the primary page and supporting components. "
                    "Translate the task requirements into concrete UI sections, data presentation, and user "
                    "interactions instead of generic placeholder content. Ensure the page reflects the task's "
                    "actual workflows, edge states, and visual hierarchy."
                ),
                "commit_message": f"feat: build {focus}",
                "files": [
                    {"path": "app/page.tsx", "description": "Primary user-facing page implementing the task's main workflow."},
                    {"path": "components/MainExperience.tsx", "description": "Reusable UI component(s) backing the page experience."},
                ],
            },
            {
                "step_number": 3,
                "description": (
                    "Harden the implementation for autonomous delivery. Add any supporting helpers, polish incomplete "
                    "states, and remove fragile dependencies or imports that would break npm install or npm run build. "
                    "Finish with production-focused cleanup so the tester sees clear progress and a stable build."
                ),
                "commit_message": "fix: harden production flow and build stability",
                "files": [
                    {"path": "components/StatusPanel.tsx", "description": "Support component for empty, error, or completion states."},
                    {"path": "lib/mock-data.ts", "description": "Supporting data or helpers required to keep the UI self-contained."},
                ],
            },
        ],
        "test_command": "npm run build",
    }


def _normalize_plan(plan: dict | None, title: str, desc: str, reqs: str, past_errors: str = "") -> dict:
    if not isinstance(plan, dict):
        return _build_fallback_plan(title, desc, reqs, past_errors)

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return _build_fallback_plan(title, desc, reqs, past_errors)

    generic_count = sum(
        1
        for step in steps
        if _is_generic_step_description(str(step.get("description", "")))
    )
    if len(steps) < 2 or generic_count == len(steps):
        return _build_fallback_plan(title, desc, reqs, past_errors)

    normalized_steps: list[dict] = []
    for idx, raw_step in enumerate(steps, start=1):
        step = dict(raw_step or {})
        files = step.get("files")
        if not isinstance(files, list) or not files:
            files = [
                {"path": "app/page.tsx", "description": "Primary page implementing the planned user flow."},
                {"path": f"components/Step{idx}Panel.tsx", "description": "Supporting component for this implementation step."},
            ]
        step_desc = str(step.get("description") or "").strip()
        if _is_generic_step_description(step_desc):
            focus = _summarize_focus(title, desc, reqs)
            step_desc = (
                f"Implement a concrete slice of {focus} for step {idx}. Focus on real user-facing behavior, "
                "wire the listed files together, and avoid placeholder code so the output is testable and ready "
                "for the next build pass."
            )

        step["step_number"] = idx
        step["description"] = step_desc
        step["files"] = files
        step["commit_message"] = _derive_commit_message(step.get("commit_message"), step_desc, files)
        normalized_steps.append(step)

    project_type = str(plan.get("project_type") or "nextjs").lower().strip()
    if project_type not in {"nextjs", "react", "vite", "static"}:
        project_type = "nextjs"

    scaffold_command = plan.get("scaffold_command")
    if project_type == "nextjs":
        scaffold_command = scaffold_command or DEFAULT_NEXT_SCAFFOLD_COMMAND

    return {
        "project_type": project_type,
        "scaffold_command": scaffold_command,
        "steps": normalized_steps,
        "test_command": plan.get("test_command") or "npm run build",
    }


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
        "You are a world-class Software Architect AI agent. "
        "Given a task, you break it into implementable steps with DETAILED descriptions. "
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n\n"
        "CRITICAL — PROJECT TYPE RULES (STRICTLY ENFORCED):\n"
        "- Prefer the latest versions of technologies that are COMPATIBLE with the installed runtime on the machine.\n"
        "- CRITICAL: DO NOT choose package or scaffold versions that exceed the server's Node.js runtime. If Next.js latest would fail on the installed Node version, use a compatible create-next-app release instead.\n"
        "- BE PROACTIVE: If you encounter an error, version conflict, or build failure, RESOLVE IT WHATEVER IT TAKES. You are empowered to change the project structure, switch tools, or adopt a completely different technical approach to bypass the blocker.\n"
        "- You MUST ONLY use JavaScript/TypeScript frontend or fullstack frameworks.\n"
        "- DEFAULT to 'nextjs' for ALL tasks: websites, web apps, dashboards, "
        "landing pages, portfolios, e-commerce, SaaS, tools with a UI, APIs, backends — everything.\n"
        "- Use 'react' ONLY if the task explicitly says 'React without Next.js' or 'Vite + React'.\n"
        "- Use 'vite' ONLY if the task explicitly specifies Vite as the build tool.\n"
        "- Use 'static' ONLY for pure HTML/CSS/JS with absolutely no framework needed "
        "(e.g. the user asks for vanilla JS, plain HTML page, or a simple static site).\n"
        "- NEVER use 'python' — Python is FORBIDDEN as a project type.\n"
        "- NEVER use 'node' standalone — if backend is needed, use Next.js API routes.\n"
        "- Backend logic MUST live inside the framework (Next.js API routes, server actions).\n"
        "- NO external database connections — use in-memory state or localStorage only.\n"
        "- When in doubt: choose 'nextjs'. It is ALWAYS the safe default. The NEXTJS framework MUST be prioritized before you proceed with implementation.\n"
        "- For 'nextjs' always use scaffold_command: "
        f"'{DEFAULT_NEXT_SCAFFOLD_COMMAND}'\n\n"
        "PACKAGE INTEGRITY RULES:\n"
        "- Do NOT remove package.json, next-env.d.ts, app/, src/app/, or pages/ once scaffolded.\n"
        "- If you change package.json, keep next/react/react-dom present and compatible.\n"
        "- react and react-dom MUST stay on the same major version.\n"
        "- Never leave the repo in a partial state with only package-lock.json or only node_modules.\n\n"
        "CRITICAL — STEP DESCRIPTION RULES:\n"
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
        "CRITICAL — FILE LIST RULES:\n"
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
        f'  "scaffold_command": "{DEFAULT_NEXT_SCAFFOLD_COMMAND}" or null,\n'
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
    return _normalize_plan(result, title, desc, reqs, past_errors)


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
        "- For CSS files: include a full design system — colors, typography, spacing, responsive "
        "breakpoints, hover effects, transitions. Make it look PROFESSIONAL, not bare-bones.\n"
        "- For JS files: include complete logic with proper error handling, event listeners, "
        "DOM manipulation, and comments explaining complex sections.\n"
        "- For React/Next.js: use proper TypeScript types, 'use client' directive where needed, "
        "proper imports, hooks, responsive Tailwind classes, and accessible HTML.\n"
        "- NEVER use placeholder text like 'TODO' or 'Add your code here'. Write the actual code.\n"
        "- NEVER import components or modules that don't exist in the project.\n"
        "- Preserve scaffold integrity: do not delete package.json, next-env.d.ts, app/, src/app/, or pages/.\n"
        "- If editing package.json, keep next/react/react-dom installed and keep react/react-dom on the same major version.\n"
        "- Never output a repo state that would leave only package-lock.json without package.json.\n"
        "- All code must be SELF-CONTAINED and FUNCTIONAL — it should work immediately."
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
        "- Each file's 'content' MUST be complete, working source code — NOT fragments.\n"
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


# ═══════════════════════════════════════════════════════════════════════════
# SKILL LOADER — Loads relevant skills based on task characteristics
# ═══════════════════════════════════════════════════════════════════════════

# Map of keyword patterns → skill SKILL.md file names to include
_SKILL_KEYWORD_MAP: list[tuple[list[str], list[str]]] = [
    # Frontend / React / Next.js
    (["react", "next", "nextjs", "frontend", "ui", "dashboard", "landing", "tailwind", "component"],
     ["react-best-practices", "composition-patterns", "frontend-design", "senior-frontend", "vercel-deploy"]),
    # Frontend visual/design polish ("frontend taste")
    (["design", "aesthetic", "beautiful", "polish", "animation", "hero", "layout", "ux", "ui/ux", "responsive"],
     ["frontend-design", "theme-factory", "senior-frontend"]),
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
    Load relevant skill files from repo-local paths first, with legacy Windows
    absolute path fallbacks.

    Selects skills based on task keywords to avoid overloading the prompt.
    """
    task_text = f"{title} {desc} {reqs}".lower()
    project_type = (plan or {}).get("project_type", "").lower()

    # Determine which skill dirs to include
    selected_skill_names: set[str] = set()
    for keywords, skill_names in _SKILL_KEYWORD_MAP:
        if keywords == ["*"] or any(kw in task_text or kw in project_type for kw in keywords):
            selected_skill_names.update(skill_names)

    # Hard guarantee: frontend implementation always carries frontend taste + architecture patterns.
    if project_type in {"nextjs", "react", "vite", "static"}:
        selected_skill_names.update(
            {"frontend-design", "composition-patterns", "senior-frontend"}
        )

    contents: list[str] = []
    loaded_sections: list[str] = []
    repo_root = Path(__file__).resolve().parent.parent
    env_skill_dirs = [
        Path(p.strip())
        for p in (os.environ.get("ORIEXA_SKILLS_DIRS", "") or "").split(",")
        if p.strip()
    ]
    env_claude_skill_dirs = [
        Path(p.strip())
        for p in (os.environ.get("ORIEXA_CLAUDE_SKILLS_DIRS", "") or "").split(",")
        if p.strip()
    ]

    api_skills_candidates = [
        repo_root / "skills",
        repo_root.parent / "Oriexa" / "skills",
        repo_root.parent / "oriexa" / "skills",
        *env_skill_dirs,
    ]
    claude_skills_candidates = [
        repo_root / ".claude" / "skills",
        repo_root.parent / "Oriexa" / ".claude" / "skills",
        repo_root.parent / "oriexa" / ".claude" / "skills",
        *env_claude_skill_dirs,
    ]

    # 1. Load API skill markdown files (all of them if present)
    seen_api_files: set[Path] = set()
    for api_skills_dir in api_skills_candidates:
        if not api_skills_dir.exists():
            continue
        for md_file in sorted(api_skills_dir.glob("*.md")):
            resolved = md_file.resolve()
            if resolved in seen_api_files:
                continue
            seen_api_files.add(resolved)
            try:
                text = md_file.read_text(encoding="utf-8")
                if text.strip():
                    contents.append(f"### Oriexa API Skill: {md_file.stem}\n\n{text}")
                    loaded_sections.append(md_file.stem)
            except Exception:
                pass

    # 2. Load selected .claude skills
    loaded_skill_names: set[str] = set()
    for claude_skills_dir in claude_skills_candidates:
        if not claude_skills_dir.exists():
            continue
        for skill_name in sorted(selected_skill_names):
            if skill_name in loaded_skill_names:
                continue
            skill_file = claude_skills_dir / skill_name / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                text = skill_file.read_text(encoding="utf-8")
                if len(text) > 1500:
                    text = text[:1500] + "\n... [truncated for token limit]"
                if text.strip():
                    contents.append(f"### Claude Skill: {skill_name}\n\n{text}")
                    loaded_skill_names.add(skill_name)
                    loaded_sections.append(skill_name)
            except Exception:
                pass

    total_chars = sum(len(c) for c in contents)
    loaded_preview = ", ".join(loaded_sections[:8]) if loaded_sections else "none"
    log_think(
        f"Loaded {len(contents)} skill sections "
        f"({total_chars // 1000}k chars). Selected={', '.join(list(selected_skill_names)[:6])} "
        f"Loaded={loaded_preview}",
        AGENT_NAME,
    )
    return contents

# ═══════════════════════════════════════════════════════════════════════════
# FIX-ONLY MODE — Targeted error repair (no full re-gen)
# ═══════════════════════════════════════════════════════════════════════════

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
        "Fix ONLY the errors — do NOT rewrite files from scratch. "
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


# ═══════════════════════════════════════════════════════════════════════════
# MAIN PROCESS
# ═══════════════════════════════════════════════════════════════════════════

def process_task(client: OriexaClient, task_id: int) -> dict:
    try:
        task = client.get_task(task_id)
        if not task:
            return {"action": "error", "error": f"Task {task_id} not found."}

        # Load / initialize state
        task_dir, _, rehydrated = ensure_local_workspace(
            task_id,
            task_status=task.get("status"),
            workspace_root=WORKSPACE_DIR,
        )
        state_file = task_dir / ".swarm_state.json"
        if rehydrated:
            log_think(f"Rehydrated workspace from GitHub for task #{task_id}", AGENT_NAME)
        log_think(f"Loading state from: {state_file}", AGENT_NAME)

        state = load_swarm_state(task_id, workspace_dir=task_dir, default={
            "status": "coding",
            "current_step": 0,
            "total_steps": 0,
            "completed_steps": [],
            "commit_log": [],
            "iterations": 0,
            "files": [],
            "test_command": "echo 'No tests defined'",
        })

        if state.get("status") != "coding":
            return {"action": "no_result", "reason": f"State is {state.get('status')}, not coding."}

        integrity_issues = _workspace_integrity_issues(task_dir, state)
        if integrity_issues:
            state = _reset_corrupt_workspace(task_dir, state, integrity_issues)
            _save_state(state_file, state)

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

        # ── Progressive Intelligence Escalation ──────────────────────
        # Iteration 0: high (default)
        # Iteration 1: high (same model, targeted fix)
        # Iteration 2+: extreme (upgrade to best available model)
        plan_complexity = "high"
        if iteration >= 2:
            log_warn(f"Escalating to 'extreme' intelligence (iteration {iteration})", AGENT_NAME)
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
            return {"action": "error", "error": "GitHub repository creation/push failed"}

        # ── STEP 3: Plan the implementation (ONCE — never re-plan) ───
        if not state.get("plan"):
            log_think("Planning implementation (Claude Sonnet — one-time plan)...", AGENT_NAME)
            write_progress(task_dir, task_id, "planning", "Analyzing requirements",
                           "Breaking task into implementation steps",
                           "Architecting solution with Claude Sonnet...", 5.0)

            # Always use claude-sonnet for the plan — this only runs once
            plan = plan_implementation(title, desc, reqs, "", poster_context, complexity="high")
            if not plan or not plan.get("steps"):
                log_warn("Planning failed, falling back to deterministic multi-step plan.", AGENT_NAME)
                plan = _build_fallback_plan(title, desc, reqs)

            state["plan"] = plan
            state["total_steps"] = len(plan.get("steps", []))
            state["test_command"] = plan.get("test_command", "echo 'No tests defined'")
            _save_state(state_file, state)

            plan_file = task_dir / ".implementation_plan.json"
            plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

            total = len(plan.get("steps", []))
            step_names = [s.get("description", f"Step {s.get('step_number', i+1)}") for i, s in enumerate(plan.get("steps", []))]
            write_progress(task_dir, task_id, "planning", "Implementation plan ready",
                           f"{total} steps planned: {' → '.join(step_names[:4])}{'...' if total > 4 else ''}",
                           f"Project type: {plan.get('project_type', 'unknown')}, {total} implementation steps",
                           10.0, metadata={"steps": total, "project_type": plan.get("project_type", "unknown"), "subtasks": step_names})
        else:
            plan = _normalize_plan(state["plan"], title, desc, reqs, past_errors)
            if plan != state["plan"]:
                state["plan"] = plan
                state["total_steps"] = len(plan.get("steps", []))
                state["test_command"] = plan.get("test_command", state.get("test_command", "npm run build"))
                _save_state(state_file, state)
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
            _cleanup_scaffold_artifacts(task_dir)

            executed_cmd, rc, out = _run_scaffold_command(scaffold_cmd, task_dir)
            log_command(task_dir, executed_cmd, rc, out)

            if rc == 0:
                h = commit_step(task_dir, f"chore: scaffold project ({plan.get('project_type', 'unknown')})")
                if h:
                    append_commit_log(task_dir, h, "chore: scaffold project")
                    log_ok(f"Scaffolded and committed [{h}]", AGENT_NAME)

                state["scaffolded"] = True
                _save_state(state_file, state)
            else:
                log_warn(f"Scaffold command failed (rc={rc}). Will continue without marking scaffold complete.", AGENT_NAME)
                append_build_log(task_dir, f"Scaffold failed (rc={rc}): {out[:800]}")
                # Keep scaffolded=False so a future coding retry can attempt again
                state["scaffolded"] = False
                _save_state(state_file, state)

        # ── STEP 4: Architectural blueprint (cached — only generate once) ─
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

        # Load skills — from the Oriexa skills dir AND from .claude/skills/ in both repos
        skill_contents = _load_skills_for_task(title, desc, reqs, plan)

        # ── STEP 5: Execute steps OR fix errors ────────────────────────
        steps = plan.get("steps", [])
        completed_step_nums = {s["step_number"] for s in state.get("completed_steps", [])}
        existing_files = []

        # Collect files already written
        for s in state.get("completed_steps", []):
            existing_files.extend(s.get("files_written", []))

        # ── FIX-ONLY MODE: If we have test_errors AND all steps are done,
        #    only fix the broken files instead of regenerating everything.
        if past_errors and len(completed_step_nums) == len(steps) and len(completed_step_nums) > 0:
            failure_summary = summarize_failure_output("build/test verification", past_errors)
            log_think(f"Fix-only mode: all {len(steps)} steps already completed. Fixing build errors...", AGENT_NAME)
            write_progress(task_dir, task_id, "execution", "Fixing build errors",
                           "Targeted fix — only rewriting files with errors",
                           failure_summary, 75.0,
                           metadata={"diagnosis": failure_summary, "iteration": iteration + 1})

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
                fix_commit = _derive_commit_message(
                    f"fix: resolve build errors iteration {iteration + 1}",
                    failure_summary,
                    [{"path": path} for path in files_written],
                )
                h = commit_step(task_dir, fix_commit)
                if h:
                    append_commit_log(task_dir, h, fix_commit)
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
            # ── Normal mode: execute remaining steps ──
            for step in steps:
                step_num = step.get("step_number", 0)
                if step_num in completed_step_nums:
                    continue  # Already done

                step_desc = step.get("description", f"Step {step_num}")
                commit_msg = _derive_commit_message(step.get("commit_message"), step_desc, step.get("files"))

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
                    log_warn(f"Step {step_num} generated no files — skipping.", AGENT_NAME)
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
                else:
                    log_warn(f"  Commit skipped for step {step_num} (no staged changes).", AGENT_NAME)
                    continue

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

        completed_count = len(state.get("completed_steps", []))
        total_steps = len(steps)
        if total_steps > 0 and completed_count < total_steps:
            state["status"] = "coding"
            state["test_errors"] = (
                f"Implementation incomplete: only {completed_count}/{total_steps} steps were committed. "
                "Continue coding instead of advancing."
            )
            _save_state(state_file, state)
            return {
                "action": "error",
                "task_id": task_id,
                "error": f"incomplete_implementation_{completed_count}_of_{total_steps}",
            }

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
                install_summary = summarize_failure_output("npm install", out)
                log_warn(f"npm install failed (rc={rc})", AGENT_NAME)
                write_progress(task_dir, task_id, "review", "Dependency install failed",
                               "npm install failed; the tester will block deployment until this is fixed",
                               install_summary, 84.0,
                               metadata={"diagnosis": install_summary, "exit_code": rc})

        if not has_meaningful_implementation(task_dir):
            state["status"] = "coding"
            state["test_errors"] = (
                "Implementation quality gate failed: repository only has housekeeping files "
                "(e.g. .gitignore) and no real code/artifacts."
            )
            _save_state(state_file, state)
            return {"action": "error", "error": "No meaningful implementation files found"}

        # ── STEP 7: Final push ────────────────────────────────────────
        write_progress(task_dir, task_id, "delivery", "Pushing code",
                       "Pushing all commits to GitHub repository",
                       f"Pushing to {state.get('repo_url', 'GitHub')}...", 90.0)
        push_ok = push_to_remote(task_dir)
        if not push_ok:
            log_warn("Final push to GitHub failed.", AGENT_NAME)

        if not verify_remote_has_main(task_dir):
            state["status"] = "coding"
            state["test_errors"] = (
                "GitHub sync gate failed: remote origin/main branch is missing. "
                "Code must be pushed before delivery."
            )
            _save_state(state_file, state)
            return {"action": "error", "error": "GitHub remote main branch not found"}

        if not verify_remote_head_matches_local(task_dir):
            state["status"] = "coding"
            state["test_errors"] = (
                "GitHub sync gate failed: remote origin/main is behind local HEAD. "
                "Latest implementation was not pushed successfully."
            )
            _save_state(state_file, state)
            return {"action": "error", "error": "GitHub remote main is behind local HEAD"}

        log_ok(f"All code pushed to {state.get('repo_url', 'GitHub')}", AGENT_NAME)

        write_progress(task_dir, task_id, "delivery", "Code complete",
                       "All implementation steps completed and pushed",
                       f"Repository: {state.get('repo_url', 'local git')}",
                       95.0, metadata={"repo_url": state.get("repo_url", "")})

        # ── Transition to testing (NEVER wipe plan or completed steps) ─
        state["status"] = "testing"
        state["iterations"] = iteration + 1
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
    try:
        task_id = int(state_file.parent.name.split("_", 1)[1])
    except Exception:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        return

    write_swarm_state(task_id, state, workspace_dir=state_file.parent)


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


