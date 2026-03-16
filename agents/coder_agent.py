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
import hashlib
import json
import os
import re
import sys
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path

# Add parent path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.base_agent import (
    BASE_URL,
    OriexaClient,
    llm_json,
    smart_llm_call,
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
    "--app --no-src-dir --import-alias @/* --yes --force --no-git --skip-install"
)
SCAFFOLD_TIMEOUT_SECONDS = int(os.environ.get("SCAFFOLD_TIMEOUT_SECONDS", "7200"))
MAX_CODING_ITERATIONS = int(os.environ.get("MAX_CODING_ITERATIONS", "12"))
MAX_CODER_FAILURE_REPEATS = int(os.environ.get("MAX_CODER_FAILURE_REPEATS", "4"))
DEFAULT_STATIC_TEST_COMMAND = 'echo "Static project verification passed"'
_FRAMEWORK_PROJECT_TYPES = {"nextjs", "react", "vite"}
_PROTECTED_FRAMEWORK_CORE_FILES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "tsconfig.json",
    "jsconfig.json",
    "next-env.d.ts",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.ts",
    "postcss.config.js",
    "postcss.config.mjs",
    "postcss.config.cjs",
    "tailwind.config.js",
    "tailwind.config.mjs",
    "tailwind.config.cjs",
    "tailwind.config.ts",
    "eslint.config.js",
    "eslint.config.mjs",
    ".eslintrc.json",
}


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


def _format_step_file_targets(step: dict, limit: int = 5) -> str:
    files = step.get("files", [])
    if not isinstance(files, list) or not files:
        return "planned files were not specified"

    labels: list[str] = []
    for file_info in files[:limit]:
        if not isinstance(file_info, dict):
            continue
        path = str(file_info.get("path") or "").strip()
        description = str(file_info.get("description") or "").strip()
        if not path:
            continue
        labels.append(f"{path} ({description})" if description else path)

    if not labels:
        return "planned files were not specified"

    if len(files) > limit:
        labels.append(f"+{len(files) - limit} more")

    return ", ".join(labels)


def _format_written_file_summary(files_written: list[str], limit: int = 5) -> str:
    if not files_written:
        return "no files written"
    preview = files_written[:limit]
    suffix = f", +{len(files_written) - limit} more" if len(files_written) > limit else ""
    return ", ".join(preview) + suffix


@contextmanager
def _build_log_heartbeat(task_dir: Path, label: str, interval_seconds: int = 15):
    stop_event = threading.Event()
    started_at = _time.monotonic()

    def _heartbeat_loop() -> None:
        while not stop_event.wait(interval_seconds):
            elapsed = int(_time.monotonic() - started_at)
            append_build_log(task_dir, f"{label} ({elapsed}s elapsed)")

    worker = threading.Thread(target=_heartbeat_loop, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop_event.set()
        worker.join(timeout=1)


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
        "vite.config.js",
        "vite.config.ts",
        "vite.config.mjs",
        "postcss.config.js",
        "postcss.config.mjs",
        "postcss.config.cjs",
        "tailwind.config.js",
        "tailwind.config.ts",
        "tailwind.config.mjs",
        "tailwind.config.cjs",
        "eslint.config.js",
        "eslint.config.mjs",
        ".eslintrc.json",
        "jsconfig.json",
        "app",
        "src",
        "components",
        "lib",
        "public",
        ".env",
        ".env.local",
        "node_modules",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
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

    normalized_cmd = _canonicalize_next_scaffold_command(scaffold_cmd)

    node_major = _detect_node_major(task_dir)
    if node_major is not None and node_major < 20:
        log_think(
            f"Detected Node.js v{node_major}; keeping the official create-next-app@latest command "
            "instead of pinning an older Next.js version.",
            AGENT_NAME,
        )

    return normalized_cmd


def _canonicalize_next_scaffold_command(scaffold_cmd: str | None) -> str:
    normalized_cmd = str(scaffold_cmd or "").strip() or DEFAULT_NEXT_SCAFFOLD_COMMAND
    if "create-next-app" not in normalized_cmd:
        return DEFAULT_NEXT_SCAFFOLD_COMMAND

    normalized_cmd = re.sub(r"create-next-app@[^ ]+", "create-next-app@latest", normalized_cmd)
    if "create-next-app@" not in normalized_cmd:
        normalized_cmd = normalized_cmd.replace("create-next-app", "create-next-app@latest", 1)
    if "--no-git" not in normalized_cmd:
        normalized_cmd = f"{normalized_cmd} --no-git"
    if "--skip-install" not in normalized_cmd:
        normalized_cmd = f"{normalized_cmd} --skip-install"
    return normalized_cmd


def _is_framework_project(project_type: str | None) -> bool:
    return str(project_type or "").lower().strip() in _FRAMEWORK_PROJECT_TYPES


def _normalize_rel_path(path: str | None) -> str:
    return str(path or "").replace("\\", "/").lstrip("./").strip().lower()


def _protected_core_files_for_project(project_type: str | None) -> set[str]:
    return set(_PROTECTED_FRAMEWORK_CORE_FILES) if _is_framework_project(project_type) else set()


def _hash_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_protected_core_snapshot(task_dir: Path, project_type: str | None) -> dict[str, str | None]:
    return {
        rel_path: _hash_file(task_dir / rel_path)
        for rel_path in sorted(_protected_core_files_for_project(project_type))
    }


def _ensure_protected_core_snapshot(task_dir: Path, state: dict) -> bool:
    project_type = str(((state or {}).get("plan") or {}).get("project_type") or "").lower()
    if not _is_framework_project(project_type):
        state.pop("protected_core_snapshot", None)
        return False

    snapshot = state.get("protected_core_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return False

    state["protected_core_snapshot"] = _capture_protected_core_snapshot(task_dir, project_type)
    return True


def _sanitize_step_files_for_project(files: list[dict], project_type: str | None, step_number: int) -> list[dict]:
    protected = _protected_core_files_for_project(project_type)
    if not protected:
        return files

    safe_files = []
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        if _normalize_rel_path(file_info.get("path")) in protected:
            continue
        safe_files.append(file_info)

    if safe_files:
        return safe_files

    return _default_files_for_project_type(str(project_type or "").lower(), step_number)


def _partition_generated_files(
    files: list[dict],
    project_type: str | None,
) -> tuple[list[dict], list[str]]:
    protected = _protected_core_files_for_project(project_type)
    if not protected:
        return files, []

    safe_files: list[dict] = []
    blocked_paths: list[str] = []
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        path = str(file_info.get("path") or "").strip()
        if _normalize_rel_path(path) in protected:
            blocked_paths.append(path)
            continue
        safe_files.append(file_info)

    return safe_files, blocked_paths


def _run_scaffold_command(scaffold_cmd: str, task_dir: Path) -> tuple[str, int, str]:
    effective_cmd = _normalize_scaffold_command(scaffold_cmd, task_dir)
    rc, out = run_shell_combined(effective_cmd, task_dir, timeout=SCAFFOLD_TIMEOUT_SECONDS)

    if rc == 0:
        return effective_cmd, rc, out

    if _looks_like_engine_mismatch(out):
        guidance = (
            "Scaffold engine mismatch detected while using the official create-next-app@latest command. "
            "The coder will not pin an older Next.js version automatically; choose a smaller compatible "
            "project type when the task allows it or use a newer Node.js runtime."
        )
        log_warn(guidance, AGENT_NAME)
        append_build_log(task_dir, guidance)
        out = f"{out.rstrip()}\n\n{guidance}".strip()

    return effective_cmd, rc, out


def _workspace_integrity_issues(task_dir: Path, state: dict | None = None) -> list[str]:
    issues: list[str] = []
    pkg_path = task_dir / "package.json"
    lock_path = task_dir / "package-lock.json"
    plan = (state or {}).get("plan") or {}
    project_type = str(plan.get("project_type") or "").lower()
    snapshot = (state or {}).get("protected_core_snapshot")

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

    if _is_framework_project(project_type) and isinstance(snapshot, dict) and snapshot:
        for rel_path, previous_hash in snapshot.items():
            current_hash = _hash_file(task_dir / rel_path)
            if current_hash == previous_hash:
                continue
            if previous_hash is None and current_hash is not None:
                issues.append(f"protected framework core file '{rel_path}' was created unexpectedly")
            elif previous_hash is not None and current_hash is None:
                issues.append(f"protected framework core file '{rel_path}' was removed unexpectedly")
            else:
                issues.append(f"protected framework core file '{rel_path}' changed unexpectedly")

    return issues


def _reset_corrupt_workspace(task_dir: Path, state: dict, issues: list[str]) -> dict:
    log_warn(
        "Workspace integrity check failed. Resetting for a clean re-scaffold: "
        + "; ".join(issues),
        AGENT_NAME,
    )
    append_build_log(task_dir, "Workspace integrity reset: " + "; ".join(issues))
    append_build_log(task_dir, "Reinstalling framework scaffold from a clean state to avoid dependency loops.")
    _cleanup_scaffold_artifacts(task_dir)
    state["status"] = "coding"
    state["scaffolded"] = False
    state["current_step"] = 0
    state["completed_steps"] = []
    state["files"] = []
    state["test_errors"] = "Workspace was auto-reset because project structure became invalid."
    state["protected_core_snapshot"] = {}
    if not state.get("plan"):
        state["total_steps"] = 0
    return state


GENERIC_STEP_PATTERNS = (
    "complete implementation",
    "implement the task",
    "finish the app",
    "build the project",
)

_FOCUS_NOISE_PATTERNS = (
    r"\b(?:i|we)\s+(?:want|need|would like|wish)\s+you\s+to\s+"
    r"(?:create|build|implement|design|develop|make|finish)\s+(?:a|an|the)?\b",
    r"\b(?:can|could|would)\s+you\s+(?:please\s+)?"
    r"(?:create|build|implement|design|develop|make|finish)\s+(?:a|an|the)?\b",
    r"\bplease\s+(?:create|build|implement|design|develop|make|finish)\s+(?:a|an|the)?\b",
    r"\b(?:create|build|implement|design|develop|make|finish)\s+(?:me\s+)?(?:a|an|the)?\b",
)

_STATIC_HINT_KEYWORDS = (
    "vanilla js",
    "plain html",
    "plain css",
    "plain javascript",
    "no framework",
    "single html",
    "static site",
)

_SIMPLE_BROWSER_APP_KEYWORDS = (
    "tic-tac-toe",
    "tic tac toe",
    "memory game",
    "snake game",
    "pong",
    "browser game",
    "simple game",
    "calculator",
    "counter",
    "timer",
    "countdown",
    "quiz",
)

_NEXT_REQUIRED_KEYWORDS = (
    "next.js",
    "nextjs",
    "app router",
    "server action",
    "route handler",
    "api route",
    "middleware",
    "server-side",
    "ssr",
)

_COMPLEX_APP_KEYWORDS = (
    "dashboard",
    "auth",
    "login",
    "signup",
    "database",
    "postgres",
    "prisma",
    "api",
    "backend",
    "server",
    "upload",
    "websocket",
    "multiplayer",
)

_REACT_HINT_KEYWORDS = (
    "react without next",
    "react app",
    "react only",
)


def _task_text(*parts: str) -> str:
    return re.sub(r"\s+", " ", " ".join(part.strip() for part in parts if part)).strip().lower()


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _infer_project_type(title: str, desc: str, reqs: str) -> str:
    text = _task_text(title, desc, reqs)
    no_backend_hint = any(
        phrase in text
        for phrase in ("without backend", "without a backend", "no backend", "no server", "self-contained")
    )

    if "vite" in text:
        return "vite"
    if _contains_any(text, _NEXT_REQUIRED_KEYWORDS):
        return "nextjs"
    if _contains_any(text, _STATIC_HINT_KEYWORDS):
        return "static"
    if _contains_any(text, _SIMPLE_BROWSER_APP_KEYWORDS) and (no_backend_hint or not _contains_any(text, _COMPLEX_APP_KEYWORDS)):
        return "static"
    if _contains_any(text, _REACT_HINT_KEYWORDS):
        return "react"
    return "nextjs"


def _infer_task_shape(title: str, desc: str, reqs: str) -> str:
    text = _task_text(title, desc, reqs)
    if any(pattern in text for pattern in ("tic-tac-toe", "tic tac toe", "game", "board", "player x", "player o")):
        return "game"
    if any(pattern in text for pattern in ("calculator", "timer", "counter", "quiz", "countdown")):
        return "tool"
    return "experience"


def _prefers_deterministic_plan(title: str, desc: str, reqs: str) -> bool:
    text = _task_text(title, desc, reqs)
    return (
        _infer_project_type(title, desc, reqs) == "static"
        and _contains_any(text, _SIMPLE_BROWSER_APP_KEYWORDS)
        and not _contains_any(text, _COMPLEX_APP_KEYWORDS)
    )


def _default_scaffold_command(project_type: str) -> str | None:
    if project_type == "nextjs":
        return DEFAULT_NEXT_SCAFFOLD_COMMAND
    return None


def _default_test_command(project_type: str) -> str:
    if project_type == "static":
        return DEFAULT_STATIC_TEST_COMMAND
    return "npm run build"


def _default_files_for_project_type(project_type: str, step_number: int) -> list[dict]:
    if project_type == "static":
        return [
            {
                "path": "index.html",
                "description": "Single-page HTML shell with semantic structure, live status text, and the main interactive surface.",
            },
            {
                "path": "styles.css",
                "description": "Responsive styling, visual system, and polished interaction states for the browser experience.",
            },
            {
                "path": "script.js",
                "description": "Client-side state, event handlers, and edge-case handling for the interactive experience.",
            },
        ]
    return [
        {"path": "app/page.tsx", "description": "Primary page implementing the planned user flow."},
        {"path": f"components/Step{step_number}Panel.tsx", "description": "Supporting component for this implementation step."},
    ]


def _clean_focus_sentence(text: str) -> str:
    sentence = re.split(r"[.!?\n]", text, maxsplit=1)[0]
    for pattern in _FOCUS_NOISE_PATTERNS:
        sentence = re.sub(pattern, " ", sentence, flags=re.IGNORECASE)
    sentence = re.sub(
        r"^(?:create|build|implement|design|develop|set up|setup|finish|complete|make)\s+",
        "",
        sentence,
        flags=re.IGNORECASE,
    )
    sentence = re.sub(r"\b(?:please|kindly)\b", " ", sentence, flags=re.IGNORECASE)
    sentence = re.sub(r"\s+", " ", sentence).strip(" ,:;-")
    sentence = re.sub(r"\b(?:a|an|the)\s*$", "", sentence, flags=re.IGNORECASE).strip(" ,:;-")
    return sentence


def _summarize_focus(*parts: str, max_words: int = 8) -> str:
    for part in parts:
        cleaned = _clean_focus_sentence(part.strip()) if part else ""
        if not cleaned:
            continue
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", cleaned)
        if words:
            return " ".join(words[:max_words]).lower()
    return "the requested experience"


def _step_log_label(description: str, max_words: int = 14) -> str:
    sentence = re.split(r"[.!?\n]", (description or "").strip(), maxsplit=1)[0].strip()
    words = sentence.split()
    if len(words) <= max_words:
        return sentence or "Implementation step"
    return " ".join(words[:max_words]) + "..."


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
    if any(path.endswith(("app/page.tsx", "pages/index.tsx", "index.html")) for path in file_paths):
        return f"feat: build {focus}"
    if any("/components/" in f"/{path}" or path.startswith("components/") for path in file_paths):
        return f"feat: add UI for {focus}"
    if "fix" in step_desc.lower() or "error" in step_desc.lower():
        return f"fix: resolve {focus}"
    return f"feat: implement {focus}"


def _build_fallback_plan(title: str, desc: str, reqs: str, past_errors: str = "") -> dict:
    focus = _summarize_focus(title, desc, reqs)
    project_type = _infer_project_type(title, desc, reqs)
    task_shape = _infer_task_shape(title, desc, reqs)
    error_hint = ""
    if past_errors:
        error_hint = (
            " Account for the latest failure context while choosing dependency versions, imports, "
            "and build tooling so the next test pass does not repeat the same blocker."
        )

    if project_type == "static":
        logic_step = (
            f"Implement the core {focus} logic in plain JavaScript. Handle real state transitions, user input, "
            "and edge cases instead of placeholder interactions so the page is genuinely usable."
        )
        if task_shape == "game":
            logic_step = (
                f"Implement the full {focus} gameplay loop in plain JavaScript. Track turn state, win and draw "
                "conditions, round resets, and visible status updates so the game is playable without any manual setup."
            )
        return {
            "project_type": "static",
            "scaffold_command": None,
            "steps": [
                {
                    "step_number": 1,
                    "description": (
                        f"Create a production-ready single-page shell for {focus}. Build semantic HTML, a responsive layout, "
                        "and a clear visual system in CSS so the experience feels intentional on desktop and mobile."
                        f"{error_hint}"
                    ),
                    "commit_message": "feat: establish browser app shell",
                    "files": [
                        {"path": "index.html", "description": "Main browser entry point with semantic structure and controls."},
                        {"path": "styles.css", "description": "Responsive design system, layout, and interaction styling."},
                    ],
                },
                {
                    "step_number": 2,
                    "description": logic_step,
                    "commit_message": f"feat: build {focus}",
                    "files": [
                        {"path": "script.js", "description": "Client-side state, event handlers, and core interaction logic."},
                        {"path": "index.html", "description": "Markup hooks and status regions required by the interactive logic."},
                    ],
                },
                {
                    "step_number": 3,
                    "description": (
                        "Polish the browser experience for autonomous delivery. Tighten responsive spacing, keyboard and focus states, "
                        "status messaging, and dependency-free compatibility so the tester sees a stable result immediately."
                    ),
                    "commit_message": "fix: harden browser flow and delivery polish",
                    "files": [
                        {"path": "styles.css", "description": "Refined responsive styling, accessibility states, and final polish."},
                        {"path": "script.js", "description": "Final edge-case handling, resets, and stable runtime behavior."},
                    ],
                },
            ],
            "test_command": DEFAULT_STATIC_TEST_COMMAND,
        }

    return {
        "project_type": "nextjs",
        "scaffold_command": DEFAULT_NEXT_SCAFFOLD_COMMAND,
        "steps": [
            {
                "step_number": 1,
                "description": (
                    f"Establish a compatible Next.js foundation for {focus}. Create the root layout, "
                    "global styling primitives, and a responsive app shell that stays within the scaffolded "
                    "project structure. Make the initial experience production-ready so "
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
                    "states, and remove fragile library usage or imports that would break npm install or npm run build. "
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
    inferred_project_type = _infer_project_type(title, desc, reqs)
    if not isinstance(plan, dict):
        return _build_fallback_plan(title, desc, reqs, past_errors)

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return _build_fallback_plan(title, desc, reqs, past_errors)

    detailed_steps = [
        step for step in steps
        if not _is_generic_step_description(str(step.get("description", "")))
    ]
    if not detailed_steps:
        return _build_fallback_plan(title, desc, reqs, past_errors)

    project_type = str(plan.get("project_type") or inferred_project_type).lower().strip()
    if project_type not in {"nextjs", "react", "vite", "static"}:
        project_type = inferred_project_type

    normalized_steps: list[dict] = []
    for idx, raw_step in enumerate(steps, start=1):
        step = dict(raw_step or {})
        files = step.get("files")
        if not isinstance(files, list) or not files:
            files = _default_files_for_project_type(project_type, idx)
        files = _sanitize_step_files_for_project(files, project_type, idx)
        step_desc = str(step.get("description") or "").strip()
        if _is_generic_step_description(step_desc):
            focus = _summarize_focus(title, desc, reqs)
            if project_type == "static":
                step_desc = (
                    f"Implement a concrete browser-ready slice of {focus} for step {idx}. Ship real HTML structure, "
                    "responsive CSS, and JavaScript behavior so the page is usable without any additional framework setup."
                )
            else:
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

    scaffold_command = plan.get("scaffold_command")
    if project_type == "static":
        scaffold_command = None
    elif project_type == "nextjs":
        scaffold_command = _canonicalize_next_scaffold_command(scaffold_command)

    test_command = str(plan.get("test_command") or "").strip()
    if project_type == "static" and test_command in {"", "npm run build"}:
        test_command = DEFAULT_STATIC_TEST_COMMAND
    elif not test_command:
        test_command = _default_test_command(project_type)

    return {
        "project_type": project_type,
        "scaffold_command": scaffold_command,
        "steps": normalized_steps,
        "test_command": test_command,
    }


def _coder_failure_signature(summary: str) -> str:
    normalized = re.sub(r"\b\d+\b", "#", (summary or "").lower())
    normalized = re.sub(r"`[^`]+`", "`path`", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:220]


def _track_coder_failure(state: dict, summary: str) -> tuple[int, int, str]:
    signature = _coder_failure_signature(summary)
    repeated = (
        state.get("coder_repeated_failure_count", 0) + 1
        if state.get("last_coder_failure_signature") == signature
        else 1
    )
    total = state.get("coder_failure_count", 0) + 1
    state["last_coder_failure_signature"] = signature
    state["coder_repeated_failure_count"] = repeated
    state["coder_failure_count"] = total
    return repeated, total, signature


def _reset_coder_failure_tracking(state: dict) -> None:
    state["last_coder_failure_signature"] = ""
    state["coder_repeated_failure_count"] = 0
    state["coder_failure_count"] = 0


def _return_coder_failure(
    *,
    state_file: Path,
    task_dir: Path,
    state: dict,
    task_id: int,
    error_code: str,
    detail: str,
) -> dict:
    repeated, total, signature = _track_coder_failure(state, detail)
    terminal = repeated >= MAX_CODER_FAILURE_REPEATS or total >= MAX_CODING_ITERATIONS
    state["status"] = "failed" if terminal else "coding"
    state["test_errors"] = detail
    _save_state(state_file, state)

    write_progress(
        task_dir,
        task_id,
        "execution",
        "Coding halted after repeated failures" if terminal else "Coding blocked - retrying",
        "The coder hit the same blocker repeatedly and the task is being marked failed for manual intervention."
        if terminal
        else "The coder hit a blocker and will retry with preserved failure context.",
        detail,
        18.0,
        metadata={
            "failure_signature": signature,
            "repeated_failure_count": repeated,
            "failure_count": total,
            "terminal": terminal,
        },
    )
    if terminal:
        append_build_log(task_dir, f"Coding halted after repeated failures: {detail}")
    return {"action": "error", "task_id": task_id, "error": error_code, "terminal": terminal}


def _compose_blueprint_from_plan(title: str, desc: str, reqs: str, plan: dict) -> str:
    steps = plan.get("steps", [])
    rendered_steps: list[str] = []
    for step in steps:
        files = ", ".join(
            str(file.get("path", "")).strip()
            for file in step.get("files", [])
            if isinstance(file, dict) and file.get("path")
        )
        rendered_steps.append(
            f"Step {step.get('step_number')}: {step.get('description', '').strip()}\n"
            f"Files: {files or 'unspecified'}"
        )

    return (
        f"Task title: {title}\n"
        f"Task description: {desc}\n"
        f"Requirements: {reqs}\n"
        f"Project type: {plan.get('project_type', 'nextjs')}\n"
        f"Scaffold command: {plan.get('scaffold_command') or 'none'}\n\n"
        "Implementation blueprint:\n"
        + "\n\n".join(rendered_steps)
    ).strip()


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1: PLAN — Break the task into implementation steps
# ═══════════════════════════════════════════════════════════════════════════

def plan_implementation(title: str, desc: str, reqs: str, past_errors: str = "", poster_context: str = "", complexity: str = "high") -> dict:
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

    recommended_project_type = _infer_project_type(title, desc, reqs)
    recommended_scaffold_command = _default_scaffold_command(recommended_project_type)
    recommended_test_command = _default_test_command(recommended_project_type)

    system = (
        "You are a world-class Software Architect AI agent. "
        "Given a task, you break it into implementable steps with DETAILED descriptions. "
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n\n"
        "CRITICAL - PROJECT TYPE RULES (STRICTLY ENFORCED):\n"
        "- Use official scaffold and install commands so the package manager resolves the current latest compatible releases.\n"
        "- NEVER hand-write dependency or scaffold version numbers into package.json, lockfiles, or scaffold commands.\n"
        "- If the latest framework tooling is incompatible with the worker runtime, do NOT pin an older release yourself. Choose a smaller compatible project type when the task allows it, or leave the runtime issue for recovery.\n"
        "- BE PROACTIVE: If you encounter an error, version conflict, or build failure, RESOLVE IT WHATEVER IT TAKES. You are empowered to change the project structure, switch tools, or adopt a completely different technical approach to bypass the blocker.\n"
        "- You MUST ONLY use JavaScript/TypeScript frontend or fullstack frameworks.\n"
        "- Choose the SMALLEST compatible project type that satisfies the task.\n"
        "- Use 'nextjs' when the task genuinely needs routing, API routes, server rendering, auth, or a multi-page app shell.\n"
        "- Use 'react' only if the task explicitly asks for React without Next.js.\n"
        "- Use 'vite' only if the task explicitly specifies Vite as the build tool.\n"
        "- Use 'static' for browser-only games, calculators, quizzes, landing pages, and other self-contained single-page experiences.\n"
        "- NEVER use 'python' — Python is FORBIDDEN as a project type.\n"
        "- NEVER use 'node' standalone — if backend is needed, use Next.js API routes.\n"
        "- Backend logic MUST live inside the framework (Next.js API routes, server actions).\n"
        "- NO external database connections — use in-memory state or localStorage only.\n"
        "- When the task is a small browser game or toy app, avoid Next.js unless the prompt clearly requires it.\n"
        "- For 'nextjs' always use scaffold_command: "
        f"'{DEFAULT_NEXT_SCAFFOLD_COMMAND}'\n\n"
        "PACKAGE INTEGRITY RULES:\n"
        "- Do NOT remove package.json, next-env.d.ts, app/, src/app/, or pages/ once scaffolded.\n"
        "- Do NOT hand-edit package.json, lockfiles, or framework config during normal implementation steps.\n"
        "- If a dependency is needed, rely on the scaffold or package-manager install workflow to resolve versions instead of inventing a version string.\n"
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
        f"Recommended project type for this task: {recommended_project_type}\n"
        f"Recommended scaffold command: {recommended_scaffold_command or 'null'}\n"
        f"Recommended test command: {recommended_test_command}\n"
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
    project_type: str = "",
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
    protected_core_files = sorted(_protected_core_files_for_project(project_type))

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
        "- NEVER hand-edit dependency versions or return package.json/lockfile edits during normal feature work.\n"
        "- Never output a repo state that would leave only package-lock.json without package.json.\n"
        "- All code must be SELF-CONTAINED and FUNCTIONAL - it should work immediately."
    )
    if protected_core_files:
        system += (
            "\n- For framework projects, DO NOT modify protected scaffold files such as package managers, "
            "TypeScript config, or framework config during normal feature steps."
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
    if protected_core_files:
        user += (
            "\n- Protected scaffold files for this framework project: "
            + ", ".join(protected_core_files)
            + ". Do not return edits for any of them. Solve the feature using app/, src/app/, pages/, components/, lib/, hooks/, public/, and styles instead."
        )

    # First attempt
    result = llm_json(system, user, max_tokens=16384, complexity=complexity)
    files = result.get("files", []) if isinstance(result, dict) else []
    
    if "_raw" in result and not files and task_dir:
        debug_file = task_dir / f".llm_debug_step_{step.get('step_number')}.txt"
        debug_file.write_text(result["_raw"], encoding="utf-8")
        log_warn(f"LLM produced invalid JSON. Saved raw output to {debug_file.name}", AGENT_NAME)

    def _validate_files(raw_files: list[dict]) -> tuple[list[dict], list[str]]:
        valid = [
            f for f in raw_files
            if isinstance(f, dict) and f.get("path") and f.get("content", "").strip() and len(f.get("content", "").strip()) > 20
        ]
        return _partition_generated_files(valid, project_type)

    # Validate: filter out files with empty or trivial content
    valid_files, blocked_paths = _validate_files(files)
    if blocked_paths:
        blocked = ", ".join(blocked_paths[:6])
        log_warn(
            f"Step {step.get('step_number')}: rejected edits to protected core files: {blocked}",
            AGENT_NAME,
        )
        if task_dir:
            append_build_log(task_dir, f"Rejected protected core file edits: {blocked}")

    if not valid_files and files:
        # Retry once with more explicit instruction and higher intelligence
        log_warn(f"Step {step.get('step_number')}: Got {len(files)} files but all had empty/trivial content. Retrying with EXTREME model complexity...", AGENT_NAME)
        retry_user = user + (
            "\n\nWARNING: Your previous response had empty file contents. "
            "You MUST write complete, working source code for EVERY file. "
            "Do NOT return empty strings or placeholder comments."
        )
        if blocked_paths:
            retry_user += (
                "\nDo NOT modify protected framework core files such as package.json, lockfiles, or framework config. "
                "Return only safe feature files."
            )
        result = llm_json(system, retry_user, max_tokens=16384, complexity="extreme")
        files = result.get("files", []) if isinstance(result, dict) else []
        
        if "_raw" in result and not files and task_dir:
            debug_file = task_dir / f".llm_debug_step_{step.get('step_number')}_retry.txt"
            debug_file.write_text(result["_raw"], encoding="utf-8")
            log_warn(f"LLM produced invalid JSON on retry. Saved raw output to {debug_file.name}", AGENT_NAME)
            
        valid_files, blocked_paths = _validate_files(files)
        if blocked_paths:
            blocked = ", ".join(blocked_paths[:6])
            log_warn(
                f"Step {step.get('step_number')}: retry still touched protected core files: {blocked}",
                AGENT_NAME,
            )
            if task_dir:
                append_build_log(task_dir, f"Retry rejected protected core file edits: {blocked}")

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
        valid_files, blocked_paths = _validate_files(files)
        if blocked_paths and task_dir:
            append_build_log(task_dir, "Final retry still attempted protected framework core file edits.")

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
    project_type: str = "",
    complexity: str = "high",
) -> list[dict]:
    """
    Given build/test error output, generate ONLY the fixed files.
    Reads the current broken files from disk, sends them + errors to the LLM,
    and gets back corrected versions. Does NOT regenerate files without errors.
    """
    import re

    lowered_error = error_output.lower()
    protected_core_files = sorted(_protected_core_files_for_project(project_type))
    protected_core_set = {_normalize_rel_path(path) for path in protected_core_files}

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

    compatibility_markers = (
        "npm install",
        "package.json",
        "dependency",
        "module not found",
        "cannot find module",
        "unsupported engine",
        "ebadengine",
        "next build",
        "turbopack",
        "webpack is configured while turbopack is not",
        "lightningcss",
        "tailwind",
        "postcss",
    )
    compatibility_candidates = [
        "package.json",
        "next.config.js",
        "next.config.mjs",
        "postcss.config.js",
        "postcss.config.mjs",
        "tailwind.config.js",
        "tailwind.config.ts",
        "tsconfig.json",
        "app/globals.css",
        "src/app/globals.css",
        "app/page.tsx",
        "src/app/page.tsx",
    ]
    if any(marker in lowered_error for marker in compatibility_markers):
        for candidate in compatibility_candidates:
            if _normalize_rel_path(candidate) in protected_core_set:
                continue
            if (task_dir / candidate).exists():
                error_files.add(candidate)

    error_files = {
        fpath for fpath in error_files
        if _normalize_rel_path(fpath) not in protected_core_set
    }

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
        "Fix ONLY the errors - do NOT rewrite files from scratch. "
        "Keep all existing functionality intact. Only modify what's broken. "
        "If the failure is caused by incompatible dependencies, build tooling, or configuration, "
        "do NOT hand-edit package.json, lockfiles, or framework config to pin versions. "
        "Instead, remove the incompatible dependency usage from safe application files or replace it with a simpler compatible approach. "
        "Prefer stable, production-safe dependencies and configurations over experimental or Node-incompatible ones. "
        "Never keep the same broken dependency or build flag if it is still causing the failure. "
        "YOU MUST OUTPUT ONLY VALID JSON. NO CONVERSATIONAL TEXT.\n"
        "Return: {\"files\": [{\"path\": \"...\", \"content\": \"...\"}]}\n"
        "Each file must have the COMPLETE corrected source code."
    )
    if protected_core_files:
        system += (
            "\nProtected framework core files are off-limits in this mode: "
            + ", ".join(protected_core_files)
            + ". Return only safe application-file changes."
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
        "Repair policy:\n"
        "- Do not stop at diagnosis only; return concrete file changes.\n"
        "- If a package or library is incompatible with the runtime or build toolchain, replace it or remove it.\n"
        "- If Turbopack or a cutting-edge build path is failing, prefer the stable compatible build path.\n"
        "- Do not hand-write dependency or scaffold versions.\n"
        "- If a dependency requires a newer Node version than the worker provides, remove that dependency usage or replace it with a simpler native implementation in safe application files.\n"
        "- Simplify the implementation if that is the fastest path to a passing install/build/test.\n\n"
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
    valid_files, blocked_paths = _partition_generated_files(valid_files, project_type)
    if blocked_paths:
        blocked = ", ".join(blocked_paths[:6])
        append_build_log(task_dir, f"Fix-only rejected protected core file edits: {blocked}")

    if not valid_files:
        log_warn("Fix-only mode returned no valid files. Retrying with compatibility-first fallback.", AGENT_NAME)
        fallback_user = (
            user
            + "\n\nFallback mode: you must return at least one changed file. "
              "Return only safe application-file changes. If the scaffold itself is broken, the workspace will be reinstalled separately."
        )
        result = llm_json(system, fallback_user, max_tokens=16384, complexity="extreme")
        files = result.get("files", []) if isinstance(result, dict) else []
        valid_files = [
            f for f in files
            if isinstance(f, dict) and f.get("path") and f.get("content", "").strip() and len(f.get("content", "").strip()) > 20
        ]
        valid_files, blocked_paths = _partition_generated_files(valid_files, project_type)
        if blocked_paths:
            blocked = ", ".join(blocked_paths[:6])
            append_build_log(task_dir, f"Fix-only retry rejected protected core file edits: {blocked}")

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

        iteration = state.get("iterations", 0)
        if iteration >= MAX_CODING_ITERATIONS:
            log_warn(
                f"Soft coding iteration limit reached ({iteration}/{MAX_CODING_ITERATIONS}). "
                "Continuing with compatibility-first recovery instead of stopping.",
                AGENT_NAME,
            )
            state["repair_strategy"] = "compatibility-first-recovery"
            write_progress(
                task_dir,
                task_id,
                "execution",
                "Escalating repair strategy",
                "Multiple coding attempts have already run; switching to compatibility-first recovery instead of stopping.",
                "Prefer replacing incompatible libraries, configs, or build flags over repeating the same fix.",
                18.0,
                metadata={"iteration": iteration, "soft_limit": MAX_CODING_ITERATIONS},
            )
            _save_state(state_file, state)

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
        if iteration >= 2 or state.get("repair_strategy") == "compatibility-first-recovery":
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
            return _return_coder_failure(
                state_file=state_file,
                task_dir=task_dir,
                state=state,
                task_id=task_id,
                error_code="git_repo_init_failed",
                detail="Git initialization failed before coding could start.",
            )

        repo_url = create_github_repo(task_id, task_dir, title)
        if repo_url:
            log_ok(f"GitHub repo ready: {repo_url}", AGENT_NAME)
            state["repo_url"] = repo_url
        else:
            return _return_coder_failure(
                state_file=state_file,
                task_dir=task_dir,
                state=state,
                task_id=task_id,
                error_code="github_repo_creation_failed",
                detail="GitHub repository creation or initial push failed before implementation could continue.",
            )

        # ── STEP 3: Plan the implementation (ONCE — never re-plan) ───
        if not state.get("plan"):
            deterministic_plan = _prefers_deterministic_plan(title, desc, reqs)
            log_think(
                "Planning implementation with deterministic static planner..."
                if deterministic_plan
                else "Planning implementation (Claude Sonnet — one-time plan)...",
                AGENT_NAME,
            )
            write_progress(task_dir, task_id, "planning", "Analyzing requirements",
                           "Breaking task into implementation steps",
                           "Using a fast deterministic browser-app planner for this task..."
                           if deterministic_plan
                           else "Architecting solution with Claude Sonnet...", 5.0)

            if deterministic_plan:
                plan = _build_fallback_plan(title, desc, reqs)
            else:
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
            step_names = [_step_log_label(s.get("description", f"Step {s.get('step_number', i+1)}")) for i, s in enumerate(plan.get("steps", []))]
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
                scaffold_summary = summarize_failure_output(executed_cmd, out)
                log_warn(f"Scaffold command failed (rc={rc}). Will continue without marking scaffold complete.", AGENT_NAME)
                append_build_log(task_dir, f"Scaffold failed (rc={rc}): {out[:800]}")
                write_progress(task_dir, task_id, "execution", "Scaffold failed",
                               "Project scaffold command failed before implementation could continue",
                               scaffold_summary, 15.0,
                               metadata={"diagnosis": scaffold_summary, "exit_code": rc})
                # Keep scaffolded=False so a future coding retry can attempt again
                state["scaffolded"] = False
                _save_state(state_file, state)

        if _ensure_protected_core_snapshot(task_dir, state):
            append_build_log(task_dir, "Captured protected framework core file snapshot for future integrity checks.")
            _save_state(state_file, state)

        # ── STEP 4: Architectural blueprint (cached — only generate once) ─
        enhanced_blueprint = state.get("cached_blueprint", "")
        if not enhanced_blueprint:
            log_think("Synthesizing execution blueprint from the implementation plan...", AGENT_NAME)
            write_progress(task_dir, task_id, "planning", "Preparing execution blueprint",
                           "Turning the approved implementation plan into a coding blueprint",
                           "Reusing the implementation plan instead of making another slow planning round-trip.", 18.0)

            enhanced_blueprint = _compose_blueprint_from_plan(title, desc, reqs, plan)
            state["cached_blueprint"] = enhanced_blueprint
            _save_state(state_file, state)
        else:
            log_think("Using cached execution blueprint", AGENT_NAME)

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
            append_build_log(task_dir, f"Fix-only recovery started: {failure_summary}")
            write_progress(task_dir, task_id, "execution", "Fixing build errors",
                           "Targeted fix — only rewriting files with errors",
                           failure_summary, 75.0,
                           metadata={"diagnosis": failure_summary, "iteration": iteration + 1})

            with _build_log_heartbeat(task_dir, "Still fixing build errors"):
                fix_files = _fix_build_errors(
                    past_errors, title, desc, reqs, enhanced_blueprint,
                    existing_files, skill_contents, poster_context, task_dir,
                    project_type,
                    plan_complexity,
                )
            if fix_files:
                files_written = []
                for f in fix_files:
                    file_path = task_dir / f["path"]
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(f["content"], encoding="utf-8")
                    files_written.append(f["path"])
                    append_build_log(task_dir, f"Updated {f['path']} during fix-only recovery")

                log_ok(f"Fixed {len(files_written)} files: {', '.join(files_written[:5])}", AGENT_NAME)
                fix_commit = _derive_commit_message(
                    f"fix: resolve build errors iteration {iteration + 1}",
                    failure_summary,
                    [{"path": path} for path in files_written],
                )
                h = commit_step(task_dir, fix_commit)
                if h:
                    append_commit_log(task_dir, h, fix_commit)
                    append_build_log(task_dir, f"Committed fix-only recovery as {h}: {fix_commit}")
                    push_to_remote(task_dir)
                    log_ok(f"Fix committed [{h}] and pushed", AGENT_NAME)
                    append_build_log(task_dir, "Pushed fix-only recovery to GitHub")

                post_fix_issues = _workspace_integrity_issues(task_dir, state)
                if post_fix_issues:
                    state = _reset_corrupt_workspace(task_dir, state, post_fix_issues)
                    _save_state(state_file, state)
                    return _return_coder_failure(
                        state_file=state_file,
                        task_dir=task_dir,
                        state=state,
                        task_id=task_id,
                        error_code="protected_core_drift_after_fix",
                        detail=(
                            "Protected framework files drifted during recovery, so the agent reset the project "
                            "and will reinstall the scaffold cleanly on the next coding pass."
                        ),
                    )
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
                return _return_coder_failure(
                    state_file=state_file,
                    task_dir=task_dir,
                    state=state,
                    task_id=task_id,
                    error_code="fix_only_no_files_reset_state",
                    detail="Fix-only recovery produced no code changes, so the coder had to reset its plan state.",
                )

        else:
            # ── Normal mode: execute remaining steps ──
            for step in steps:
                step_num = step.get("step_number", 0)
                if step_num in completed_step_nums:
                    continue  # Already done

                step_desc = step.get("description", f"Step {step_num}")
                commit_msg = _derive_commit_message(step.get("commit_message"), step_desc, step.get("files"))
                step_label = _step_log_label(step_desc)
                step_targets = _format_step_file_targets(step)

                log_think(f"Step {step_num}/{len(steps)}: {step_label}", AGENT_NAME)
                append_build_log(task_dir, f"Step {step_num}: {step_label}")
                append_build_log(task_dir, f"Step {step_num} plan: {step_targets}")

                step_pct = 20.0 + (step_num - 1) / max(len(steps), 1) * 60.0
                write_progress(task_dir, task_id, "execution",
                               f"Step {step_num}/{len(steps)}: {step_label}",
                               step_desc,
                               f"Drafting {len(step.get('files', [])) or 'the planned'} file(s): {step_targets}",
                               step_pct, subtask_id=step_num,
                               metadata={"step": step_num, "total_steps": len(steps), "planned_files": step.get("files", [])})

                append_build_log(task_dir, f"Generating code for step {step_num} with model output...")
                with _build_log_heartbeat(task_dir, f"Still generating code for step {step_num}"):
                    files = generate_step_code(
                        step, title, desc, reqs, enhanced_blueprint,
                        existing_files, skill_contents, poster_context, task_dir=task_dir,
                        project_type=plan.get("project_type", ""),
                        complexity=plan_complexity
                    )

                if not files:
                    log_warn(f"Step {step_num} generated no files — skipping.", AGENT_NAME)
                    append_build_log(task_dir, f"Step {step_num} returned no files from the model")
                    continue

                files_written = []
                for f in files:
                    file_path = task_dir / f["path"]
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(f["content"], encoding="utf-8")
                    files_written.append(f["path"])
                    existing_files.append(f["path"])
                    size_bytes = len(f.get("content", "").encode("utf-8"))
                    append_build_log(task_dir, f"Wrote {f['path']} ({size_bytes} bytes)")

                log_think(f"  Wrote {len(files_written)} files: {', '.join(files_written[:5])}", AGENT_NAME)
                append_build_log(task_dir, f"Step {step_num} wrote {len(files_written)} file(s): {_format_written_file_summary(files_written)}")

                h = commit_step(task_dir, commit_msg)
                if h:
                    append_commit_log(task_dir, h, commit_msg)
                    log_ok(f"  Committed [{h}]: {commit_msg}", AGENT_NAME)
                    append_build_log(task_dir, f"Committed step {step_num} as {h}: {commit_msg}")
                    if should_push(task_dir):
                        push_to_remote(task_dir)
                        log_ok("  Pushed to GitHub", AGENT_NAME)
                        append_build_log(task_dir, f"Pushed step {step_num} commit to GitHub")
                else:
                    log_warn(f"  Commit skipped for step {step_num} (no staged changes).", AGENT_NAME)
                    append_build_log(task_dir, f"Commit skipped for step {step_num}: no staged changes")
                    continue

                step_pct_done = 20.0 + step_num / max(len(steps), 1) * 60.0
                write_progress(task_dir, task_id, "execution",
                               f"Step {step_num} complete: {step_label}",
                               f"Wrote {len(files_written)} files and committed",
                               f"Committed {len(files_written)} file(s): {_format_written_file_summary(files_written)}",
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

                post_step_issues = _workspace_integrity_issues(task_dir, state)
                if post_step_issues:
                    state = _reset_corrupt_workspace(task_dir, state, post_step_issues)
                    _save_state(state_file, state)
                    return _return_coder_failure(
                        state_file=state_file,
                        task_dir=task_dir,
                        state=state,
                        task_id=task_id,
                        error_code=f"protected_core_drift_step_{step_num}",
                        detail=(
                            "Protected framework files drifted during implementation, so the agent reset the workspace "
                            "and will reinstall the scaffold cleanly before continuing."
                        ),
                    )

        completed_count = len(state.get("completed_steps", []))
        total_steps = len(steps)
        if total_steps > 0 and completed_count < total_steps:
            return _return_coder_failure(
                state_file=state_file,
                task_dir=task_dir,
                state=state,
                task_id=task_id,
                error_code=f"incomplete_implementation_{completed_count}_of_{total_steps}",
                detail=(
                    f"Implementation incomplete: only {completed_count}/{total_steps} steps were committed. "
                    "Continue coding instead of advancing."
                ),
            )

        # ── STEP 6: Install dependencies ──────────────────────────────
        if (task_dir / "package.json").exists():
            log_think("Installing npm dependencies...", AGENT_NAME)
            append_build_log(task_dir, "Installing npm dependencies...")
            write_progress(task_dir, task_id, "review", "Installing dependencies",
                           "Running npm install to install project dependencies",
                           "npm install running...", 83.0)
            with _build_log_heartbeat(task_dir, "npm install still running"):
                rc, out = run_npm_install(task_dir)
            log_command(task_dir, "npm install", rc, out)
            if rc == 0:
                log_ok("npm install succeeded.", AGENT_NAME)
                append_build_log(task_dir, "npm install completed successfully")
                write_progress(task_dir, task_id, "review", "Dependencies installed",
                               "npm install completed successfully",
                               "All packages installed", 86.0)
            else:
                install_summary = summarize_failure_output("npm install", out)
                log_warn(f"npm install failed (rc={rc})", AGENT_NAME)
                append_build_log(task_dir, f"npm install failed: {install_summary}")
                write_progress(task_dir, task_id, "review", "Dependency install failed",
                               "npm install failed; the tester will block deployment until this is fixed",
                               install_summary, 84.0,
                               metadata={"diagnosis": install_summary, "exit_code": rc})

        if not has_meaningful_implementation(task_dir):
            return _return_coder_failure(
                state_file=state_file,
                task_dir=task_dir,
                state=state,
                task_id=task_id,
                error_code="no_meaningful_implementation",
                detail=(
                    "Implementation quality gate failed: repository only has housekeeping files "
                    "(e.g. .gitignore) and no real code/artifacts."
                ),
            )

        # ── STEP 7: Final push ────────────────────────────────────────
        append_build_log(task_dir, f"Preparing final push to {state.get('repo_url', 'GitHub')}")
        write_progress(task_dir, task_id, "delivery", "Pushing code",
                       "Pushing all commits to GitHub repository",
                       f"Pushing to {state.get('repo_url', 'GitHub')}...", 90.0)
        with _build_log_heartbeat(task_dir, "Final push still running"):
            push_ok = push_to_remote(task_dir)
        if not push_ok:
            log_warn("Final push to GitHub failed.", AGENT_NAME)
            append_build_log(task_dir, "Final push to GitHub failed")

        if not verify_remote_has_main(task_dir):
            return _return_coder_failure(
                state_file=state_file,
                task_dir=task_dir,
                state=state,
                task_id=task_id,
                error_code="github_remote_main_missing",
                detail=(
                    "GitHub sync gate failed: remote origin/main branch is missing. "
                    "Code must be pushed before delivery."
                ),
            )

        if not verify_remote_head_matches_local(task_dir):
            return _return_coder_failure(
                state_file=state_file,
                task_dir=task_dir,
                state=state,
                task_id=task_id,
                error_code="github_remote_behind_local",
                detail=(
                    "GitHub sync gate failed: remote origin/main is behind local HEAD. "
                    "Latest implementation was not pushed successfully."
                ),
            )

        log_ok(f"All code pushed to {state.get('repo_url', 'GitHub')}", AGENT_NAME)
        append_build_log(task_dir, f"All code pushed to {state.get('repo_url', 'GitHub')}")

        write_progress(task_dir, task_id, "delivery", "Code complete",
                       "All implementation steps completed and pushed",
                       f"Repository: {state.get('repo_url', 'local git')}",
                       95.0, metadata={"repo_url": state.get("repo_url", "")})

        # ── Transition to testing (NEVER wipe plan or completed steps) ─
        _reset_coder_failure_tracking(state)
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
        if "state" in locals() and "state_file" in locals() and "task_dir" in locals():
            try:
                return _return_coder_failure(
                    state_file=state_file,
                    task_dir=task_dir,
                    state=state,
                    task_id=task_id,
                    error_code="coder_exception",
                    detail=f"Unexpected coder exception: {e}",
                )
            except Exception:
                pass
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


