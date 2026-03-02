"""SkillResolver — loads and selects skills from the registry for agent prompt injection."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Project root: two levels up from this file (app/orchestrator/skills.py -> project root)
PROJECT_ROOT = Path(__file__).parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / ".claude" / "skills"
REGISTRY_PATH = SKILLS_DIR / "SKILL-REGISTRY.md"

# The frontend design skill is ALWAYS injected, regardless of task type
MANDATORY_SKILLS = ["frontend-design"]

# Mapping from task_type to agent role names used in the registry's
# "Agent-to-Skill Mapping" section
TASK_TYPE_TO_AGENT_ROLES: dict[str, list[str]] = {
    "frontend": ["Web Frontend Agent", "Design Agent"],
    "backend": ["API/Backend Agent"],
    "fullstack": ["Full-Stack Agent", "Web Frontend Agent", "API/Backend Agent"],
    "general": ["Full-Stack Agent"],
}


class SkillResolver:
    """Resolves, loads, and formats skills for injection into agent prompts.

    Reads the SKILL-REGISTRY.md once, caches the parsed agent-to-skill mapping,
    then provides a method to get formatted skill content for a given task context.
    """

    def __init__(self) -> None:
        self._agent_skill_map: dict[str, dict[str, list[str]]] | None = None

    def _parse_registry(self) -> dict[str, dict[str, list[str]]]:
        """Parse the Agent-to-Skill Mapping section from SKILL-REGISTRY.md.

        Returns a dict like:
            {
                "Web Frontend Agent": {
                    "primary": ["frontend-design", "react-best-practices", ...],
                    "secondary": ["d3-visualization", ...],
                },
                ...
            }
        """
        if self._agent_skill_map is not None:
            return self._agent_skill_map

        mapping: dict[str, dict[str, list[str]]] = {}
        try:
            content = REGISTRY_PATH.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            logger.warning("Could not read skill registry at %s: %s", REGISTRY_PATH, exc)
            self._agent_skill_map = mapping
            return mapping

        # Find the "## Agent-to-Skill Mapping" section
        section_match = re.search(
            r"## Agent-to-Skill Mapping\s*\n(.*?)(?=\n## |\Z)",
            content,
            re.DOTALL,
        )
        if not section_match:
            logger.warning("Could not find Agent-to-Skill Mapping section in registry")
            self._agent_skill_map = mapping
            return mapping

        section = section_match.group(1)
        current_agent: str | None = None

        for line in section.splitlines():
            line = line.strip()
            if line.startswith("### "):
                current_agent = line[4:].strip()
                mapping[current_agent] = {"primary": [], "secondary": []}
            elif current_agent and line.startswith("Primary:"):
                skills = [s.strip().strip("`") for s in line[8:].split(",")]
                mapping[current_agent]["primary"] = skills
            elif current_agent and line.startswith("Secondary:"):
                skills = [s.strip().strip("`") for s in line[10:].split(",")]
                mapping[current_agent]["secondary"] = skills

        self._agent_skill_map = mapping
        return mapping

    def _get_skills_for_task_type(self, task_type: str) -> list[str]:
        """Return an ordered list of skill directory names for a task_type.

        Primary skills come first, then secondary. Duplicates are removed.
        Mandatory skills are always prepended.
        """
        mapping = self._parse_registry()
        agent_roles = TASK_TYPE_TO_AGENT_ROLES.get(task_type, ["Full-Stack Agent"])

        seen: set[str] = set()
        ordered: list[str] = []

        # Always start with mandatory skills
        for skill in MANDATORY_SKILLS:
            if skill not in seen:
                ordered.append(skill)
                seen.add(skill)

        # Add primary skills from all matching agent roles
        for role in agent_roles:
            role_skills = mapping.get(role, {})
            for skill in role_skills.get("primary", []):
                if skill not in seen:
                    ordered.append(skill)
                    seen.add(skill)

        # Add secondary skills from all matching agent roles
        for role in agent_roles:
            role_skills = mapping.get(role, {})
            for skill in role_skills.get("secondary", []):
                if skill not in seen:
                    ordered.append(skill)
                    seen.add(skill)

        # Also add Deployment Agent primary skills for all tasks
        deploy_skills = mapping.get("Deployment Agent", {})
        for skill in deploy_skills.get("primary", []):
            if skill not in seen:
                ordered.append(skill)
                seen.add(skill)

        return ordered

    def _load_skill_content(self, skill_dir_name: str) -> str | None:
        """Load the SKILL.md content for a given skill directory name."""
        skill_path = SKILLS_DIR / skill_dir_name / "SKILL.md"
        try:
            if skill_path.exists():
                return skill_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to read skill %s: %s", skill_dir_name, exc)
        return None

    def resolve(
        self,
        task_type: str,
        max_skills: int = 5,
    ) -> str:
        """Resolve and format skill content for prompt injection.

        Args:
            task_type: One of "frontend", "backend", "fullstack", "general".
            max_skills: Maximum number of skills to load (to control prompt size).

        Returns:
            Formatted string to inject into the agent's execution prompt.
            Empty string if no skills are loaded.
        """
        skill_names = self._get_skills_for_task_type(task_type)[:max_skills]

        loaded_sections: list[str] = []
        for skill_name in skill_names:
            content = self._load_skill_content(skill_name)
            if content:
                # Strip YAML frontmatter if present
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        content = parts[2].strip()

                loaded_sections.append(
                    f"### Skill: {skill_name}\n\n{content}"
                )

        if not loaded_sections:
            return ""

        header = (
            "\n\n## Skill Guidelines\n"
            "The following specialized guidelines apply to this task. "
            "Follow them carefully throughout your implementation.\n\n"
        )
        return header + "\n\n---\n\n".join(loaded_sections) + "\n"


# Module-level singleton for easy import
skill_resolver = SkillResolver()
