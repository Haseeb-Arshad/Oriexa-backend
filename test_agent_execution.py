"""
Simulate orchestrator agent execution on ComingSoon badge task.
Demonstrates sequential phase execution with Git commits.
"""

import asyncio
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from app.orchestrator.git_helper import GitHelper
from app.orchestrator.progress import progress_tracker

# Colors for output
GREEN = '\033[92m'
BLUE = '\033[94m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'

async def init_workspace(task_id: int) -> Path:
    """Initialize workspace with git repo."""
    workspace = Path(tempfile.mkdtemp()) / f"task-{task_id}"
    workspace.mkdir(parents=True, exist_ok=True)

    # Initialize git
    subprocess.run(["git", "init"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "agent@oriexa.ai"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Oriexa Agent"], cwd=workspace, capture_output=True, check=True)

    # Add remote
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:Haseeb-Arshad/Oriexa.git"],
        cwd=workspace, capture_output=True
    )

    print(f"{CYAN}[INIT]{RESET} Workspace initialized at {workspace}")
    return workspace

async def simulate_triage(execution_id: int, workspace: Path) -> dict:
    """Triage phase: assess task complexity."""
    print(f"\n{YELLOW}{'='*70}{RESET}")
    print(f"{YELLOW}[PHASE 1: TRIAGE]{RESET}")
    print(f"{YELLOW}{'='*70}{RESET}")

    progress_tracker.add_step(execution_id, "triage", "start",
        detail="Taking a close look at 'ComingSoon badge component' to understand the requirements")
    print(f"{GREEN}[DONE]{RESET} Reading task: Create ComingSoon badge component")

    await asyncio.sleep(0.5)

    progress_tracker.add_step(execution_id, "triage", "thinking",
        detail="Assessing clarity, complexity, and whether any questions need to be asked first")
    print(f"{GREEN}[DONE]{RESET} Assessing complexity: LOW")
    print(f"{GREEN}[DONE]{RESET} Clarity score: 95/100")

    await asyncio.sleep(0.5)

    progress_tracker.add_step(execution_id, "triage", "done",
        detail="Complexity: low. Everything looks clear — ready to start planning.",
        metadata={"complexity": "low", "clarity_score": 95})
    print(f"{GREEN}[DONE]{RESET} Triage complete - ready to plan")

    return {"complexity": "low", "needs_clarification": False}

async def simulate_planning(execution_id: int, workspace: Path) -> dict:
    """Planning phase: create task plan."""
    print(f"\n{YELLOW}{'='*70}{RESET}")
    print(f"{YELLOW}[PHASE 2: PLANNING]{RESET}")
    print(f"{YELLOW}{'='*70}{RESET}")

    progress_tracker.add_step(execution_id, "planning", "start",
        detail="Designing a step-by-step blueprint to build this the right way")
    print(f"{GREEN}[DONE]{RESET} Creating execution plan...")

    await asyncio.sleep(0.3)

    progress_tracker.add_step(execution_id, "planning", "exploring",
        detail="Scanning the workspace, reading existing files, mapping out the landscape")
    print(f"{GREEN}[DONE]{RESET} Scanning project structure")

    await asyncio.sleep(0.3)

    # Create plan file
    plan_file = workspace / "plan.md"
    plan_file.write_text("""# ComingSoon Badge Component Plan

## Subtask 1: Create Component
- Create src/components/ComingSoonBadge.tsx
- Implement React functional component
- Add size props (sm, md, lg)
- Add gradient styling (purple to blue)

## Subtask 2: Add Exports
- Create src/components/index.ts
- Export ComingSoonBadge component

## Subtask 3: Create Demo
- Create src/app/demo/page.tsx
- Showcase all sizes
- Show animations
- Include documentation

## Subtask 4: Verify
- Test all sizes
- Verify responsive design
- Check animations work
- Validate Tailwind usage

## Subtask 5: Document
- Write usage instructions
- Add feature list
- Create code examples
""")

    progress_tracker.add_step(execution_id, "planning", "done",
        detail="Created a 5-step plan: Create component, Add exports, Create demo, Verify, Document",
        metadata={"subtask_count": 5})
    print(f"{GREEN}[DONE]{RESET} Plan created with 5 subtasks")

    # GIT COMMIT AFTER PLANNING
    print(f"\n{CYAN}[GIT]{RESET} Committing plan...")
    git = GitHelper(str(workspace))
    await git.add_all()
    await git.commit("Phase: Planning - Created plan with 5 subtasks")

    progress_tracker.add_step(execution_id, "planning", "committed",
        detail="Plan committed to version control")
    print(f"{GREEN}[DONE]{RESET} Committed to git: 'Phase: Planning - Created plan with 5 subtasks'")

    return {"plan": ["Create component", "Add exports", "Create demo", "Verify", "Document"]}

async def simulate_execution(execution_id: int, workspace: Path) -> dict:
    """Execution phase: implement the component."""
    print(f"\n{YELLOW}{'='*70}{RESET}")
    print(f"{YELLOW}[PHASE 3: EXECUTION]{RESET}")
    print(f"{YELLOW}{'='*70}{RESET}")

    progress_tracker.add_step(execution_id, "execution", "start",
        detail="Executing 5 subtask(s) — writing code, running commands, building it out")
    print(f"{GREEN}[DONE]{RESET} Starting implementation...")

    await asyncio.sleep(0.2)

    # Subtask 1: Create component
    progress_tracker.add_step(execution_id, "execution", "writing",
        detail="Subtask 1/5: Creating ComingSoonBadge.tsx component")
    print(f"{GREEN}[DONE]{RESET} Creating src/components/ComingSoonBadge.tsx")

    components_dir = workspace / "src" / "components"
    components_dir.mkdir(parents=True, exist_ok=True)

    component_code = """'use client';

import { useState } from 'react';

type SizeType = 'sm' | 'md' | 'lg';

interface ComingSoonBadgeProps {
  size?: SizeType;
  text?: string;
}

export const ComingSoonBadge: React.FC<ComingSoonBadgeProps> = ({
  size = 'md',
  text = 'Coming Soon',
}) => {
  const [scale, setScale] = useState(1);

  const sizeClasses = {
    sm: 'px-2 py-1 text-xs',
    md: 'px-3 py-1.5 text-sm',
    lg: 'px-4 py-2 text-base',
  };

  return (
    <div
      className="inline-block"
      onMouseEnter={() => setScale(1.05)}
      onMouseLeave={() => setScale(1)}
    >
      <div
        className={`
          ${sizeClasses[size]}
          font-semibold
          rounded-full
          bg-gradient-to-r from-purple-500 to-blue-500
          text-white
          shadow-lg
          transition-transform duration-200
          flex items-center justify-center
          whitespace-nowrap
          cursor-default
          animate-pulse
        `}
        style={{
          transform: `scale(${scale})`,
        }}
      >
        {text}
      </div>
    </div>
  );
};

export default ComingSoonBadge;
"""
    (components_dir / "ComingSoonBadge.tsx").write_text(component_code)
    await asyncio.sleep(0.3)

    # Subtask 2: Add exports
    progress_tracker.add_step(execution_id, "execution", "writing",
        detail="Subtask 2/5: Creating component exports")
    print(f"{GREEN}[DONE]{RESET} Creating src/components/index.ts")

    (components_dir / "index.ts").write_text(
        "export { ComingSoonBadge } from './ComingSoonBadge';\nexport default {};\n"
    )
    await asyncio.sleep(0.2)

    # Subtask 3: Create demo
    progress_tracker.add_step(execution_id, "execution", "writing",
        detail="Subtask 3/5: Creating demo page")
    print(f"{GREEN}[DONE]{RESET} Creating src/app/demo/page.tsx")

    app_dir = workspace / "src" / "app" / "demo"
    app_dir.mkdir(parents=True, exist_ok=True)

    demo_code = """'use client';

import { ComingSoonBadge } from '@/components';

export default function DemoPage() {
  return (
    <div className="min-h-screen bg-slate-900 p-8">
      <h1 className="text-4xl font-bold text-white mb-12">ComingSoon Badge Demo</h1>

      <div className="space-y-12">
        <div className="bg-slate-800 rounded-lg p-8">
          <h2 className="text-xl font-semibold text-white mb-6">Small</h2>
          <ComingSoonBadge size="sm" text="Coming Soon" />
        </div>

        <div className="bg-slate-800 rounded-lg p-8">
          <h2 className="text-xl font-semibold text-white mb-6">Medium (Default)</h2>
          <ComingSoonBadge size="md" text="Coming Soon" />
        </div>

        <div className="bg-slate-800 rounded-lg p-8">
          <h2 className="text-xl font-semibold text-white mb-6">Large</h2>
          <ComingSoonBadge size="lg" text="Coming Soon" />
        </div>
      </div>
    </div>
  );
}
"""
    (app_dir / "page.tsx").write_text(demo_code)
    await asyncio.sleep(0.2)

    # Subtask 4: Run tests
    progress_tracker.add_step(execution_id, "execution", "testing",
        detail="Subtask 4/5: Running tests - 3 component variants passing")
    print(f"{GREEN}[DONE]{RESET} Testing component (all tests passing)")
    await asyncio.sleep(0.3)

    # Subtask 5: Documentation
    progress_tracker.add_step(execution_id, "execution", "writing",
        detail="Subtask 5/5: Creating documentation")
    print(f"{GREEN}[DONE]{RESET} Creating component documentation")
    (components_dir / "README.md").write_text(
        "# ComingSoon Badge Component\n\nReusable badge with gradient and animations.\n\nSizes: sm, md, lg\n"
    )
    await asyncio.sleep(0.2)

    progress_tracker.add_step(execution_id, "execution", "done",
        detail="3 file(s) created. 1 file(s) modified.",
        metadata={"files_created": 3, "files_modified": 1})
    print(f"{GREEN}[DONE]{RESET} Implementation complete")

    # GIT COMMIT AFTER EXECUTION
    print(f"\n{CYAN}[GIT]{RESET} Committing implementation...")
    git = GitHelper(str(workspace))
    await git.add_all()
    await git.commit("Phase: Execution - 3 files created, 1 file modified")

    progress_tracker.add_step(execution_id, "execution", "committed",
        detail="Implementation committed to version control")
    print(f"{GREEN}[DONE]{RESET} Committed to git: 'Phase: Execution - 3 files created, 1 file modified'")

    return {"files_created": 3, "files_modified": 1}

async def simulate_review(execution_id: int, workspace: Path) -> dict:
    """Review phase: validate deliverable."""
    print(f"\n{YELLOW}{'='*70}{RESET}")
    print(f"{YELLOW}[PHASE 4: REVIEW]{RESET}")
    print(f"{YELLOW}{'='*70}{RESET}")

    progress_tracker.add_step(execution_id, "review", "start",
        detail="Putting on the reviewer hat — checking everything against the original requirements")
    print(f"{GREEN}[DONE]{RESET} Starting quality review...")

    await asyncio.sleep(0.3)

    progress_tracker.add_step(execution_id, "review", "thinking",
        detail="Evaluating completeness, correctness, code quality, and test coverage")
    print(f"{GREEN}[DONE]{RESET} Checking: completeness, correctness, quality")
    print(f"{GREEN}[DONE]{RESET} Validating responsive design")
    print(f"{GREEN}[DONE]{RESET} Verifying Tailwind CSS usage")

    await asyncio.sleep(0.3)

    # Create review report
    (workspace / "REVIEW_REPORT.md").write_text("""# Code Review Report

## Component Review: ComingSoonBadge

[PASS] Completeness: All requirements met
[PASS] Type Safety: Full TypeScript coverage
[PASS] Responsiveness: Mobile-friendly design
[PASS] Accessibility: Semantic HTML
[PASS] Code Quality: Follows React best practices
[PASS] Styling: Tailwind CSS only, no external CSS

Quality Score: 94/100
Status: APPROVED
""")

    progress_tracker.add_step(execution_id, "review", "done",
        detail="Quality score: 94/100 — looking great, ready for delivery!",
        metadata={"score": 94, "passed": True})
    print(f"{GREEN}[DONE]{RESET} Review complete - Quality score: 94/100")

    # GIT COMMIT AFTER REVIEW
    print(f"\n{CYAN}[GIT]{RESET} Committing review results...")
    git = GitHelper(str(workspace))
    await git.add_all()
    await git.commit("Phase: Review Complete - Quality score: 94/100")

    progress_tracker.add_step(execution_id, "review", "committed",
        detail="Review results committed to version control")
    print(f"{GREEN}[DONE]{RESET} Committed to git: 'Phase: Review Complete - Quality score: 94/100'")

    return {"score": 94, "passed": True}

async def simulate_delivery(execution_id: int, workspace: Path) -> dict:
    """Delivery phase: finalize and deliver."""
    print(f"\n{YELLOW}{'='*70}{RESET}")
    print(f"{YELLOW}[PHASE 5: DELIVERY]{RESET}")
    print(f"{YELLOW}{'='*70}{RESET}")

    progress_tracker.add_step(execution_id, "delivery", "start",
        detail="Everything passed review — packaging it all up with a bow on top")
    print(f"{GREEN}[DONE]{RESET} Preparing deliverable...")

    await asyncio.sleep(0.3)

    # Create deliverable manifest
    (workspace / "DELIVERABLE.md").write_text("""# ComingSoon Badge Component - Deliverable

## Completion Status
[DONE] All requirements met
[DONE] All tests passing
[DONE] Quality review: 94/100
[DONE] Code review: Approved

## Files Delivered
- src/components/ComingSoonBadge.tsx
- src/components/index.ts
- src/app/demo/page.tsx
- src/components/README.md
- REVIEW_REPORT.md

## Features Included
[DONE] 3 sizes (sm, md, lg)
[DONE] Gradient background
[DONE] Hover animations
[DONE] Pulse effect
[DONE] Responsive design
[DONE] Tailwind CSS only

## Ready for Production
This component is production-ready and can be imported and used immediately.
""")

    progress_tracker.add_step(execution_id, "delivery", "submitting",
        detail="Uploading the deliverable with a complete summary of what was built")
    print(f"{GREEN}[DONE]{RESET} Creating deliverable manifest")
    await asyncio.sleep(0.3)

    progress_tracker.add_step(execution_id, "delivery", "done",
        detail="Successfully delivered! 5 file(s) included in the final package.",
        metadata={"files_count": 5})
    print(f"{GREEN}[DONE]{RESET} Deliverable complete")

    # GIT COMMIT AFTER DELIVERY
    print(f"\n{CYAN}[GIT]{RESET} Committing final deliverable...")
    git = GitHelper(str(workspace))
    await git.add_all()
    await git.commit("Phase: Delivery Complete - Task delivered with 5 file(s)")

    progress_tracker.add_step(execution_id, "delivery", "committed",
        detail="Final deliverable committed to version control")
    print(f"{GREEN}[DONE]{RESET} Committed to git: 'Phase: Delivery Complete - Task delivered with 5 file(s)'")

    return {"status": "delivered", "files_count": 5}

async def show_git_history(workspace: Path):
    """Show the git commit history."""
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}[GIT HISTORY]{RESET}")
    print(f"{BLUE}{'='*70}{RESET}\n")

    result = subprocess.run(
        ["git", "log", "--oneline", "-10"],
        cwd=workspace,
        capture_output=True,
        text=True
    )

    if result.stdout:
        for line in result.stdout.strip().split('\n'):
            print(f"{CYAN}{line}{RESET}")

    print()

    # Show phase commits specifically
    result = subprocess.run(
        ["git", "log", "--grep=Phase", "--oneline"],
        cwd=workspace,
        capture_output=True,
        text=True
    )

    if result.stdout:
        print(f"{GREEN}Phase Commits:{RESET}")
        for line in result.stdout.strip().split('\n'):
            print(f"  {GREEN}[DONE]{RESET} {line}")

async def main():
    """Run full agent simulation."""
    execution_id = 42
    task_name = "ComingSoon Badge Component"

    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}Oriexa Agent Execution Simulator{RESET}")
    print(f"{BLUE}{'='*70}{RESET}")
    print(f"{YELLOW}Task: {task_name}{RESET}")
    print(f"{YELLOW}Execution ID: {execution_id}{RESET}")
    print(f"{YELLOW}Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}\n")

    # Initialize
    workspace = await init_workspace(execution_id)

    try:
        # Execute phases
        await simulate_triage(execution_id, workspace)
        await simulate_planning(execution_id, workspace)
        await simulate_execution(execution_id, workspace)
        await simulate_review(execution_id, workspace)
        await simulate_delivery(execution_id, workspace)

        # Show results
        await show_git_history(workspace)

        # Show progress tracker
        print(f"{BLUE}{'='*70}{RESET}")
        print(f"{BLUE}[PROGRESS TRACKER]{RESET}")
        print(f"{BLUE}{'='*70}{RESET}\n")

        steps = progress_tracker.get_steps(execution_id)
        print(f"Total steps recorded: {len(steps)}\n")

        for i, step in enumerate(steps[-10:], 1):  # Show last 10
            print(f"{GREEN}[{i}]{RESET} {step.phase.upper()} - {step.description}")

        print(f"\n{GREEN}{'='*70}{RESET}")
        print(f"{GREEN}EXECUTION COMPLETE{RESET}")
        print(f"{GREEN}{'='*70}{RESET}")
        print(f"{GREEN}[DONE]{RESET} All phases executed sequentially")
        print(f"{GREEN}[DONE]{RESET} All commits performed")
        print(f"{GREEN}[DONE]{RESET} Progress tracked")
        print(f"{GREEN}[DONE]{RESET} Files created and delivered")
        print()

    finally:
        # Keep workspace for inspection
        print(f"{YELLOW}Workspace preserved at: {workspace}{RESET}")
        print(f"{YELLOW}Run: cd {workspace} && git log --oneline{RESET}")

if __name__ == "__main__":
    asyncio.run(main())
