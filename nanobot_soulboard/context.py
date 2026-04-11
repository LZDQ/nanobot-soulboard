"""Soulboard-specific context builder."""

import platform
from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.agent.skills import SkillsLoader


class SoulboardContextBuilder(ContextBuilder):
    """Build soulboard system prompts without delegating to upstream assembly."""

    SYSTEM_FILENAME = "SYSTEM.md"

    def __init__(self, workspace: Path, soul_id: str):
        super().__init__(workspace)
        self.soul_id = soul_id
        self.workspace_skills = SkillsLoader(workspace, builtin_skills_dir=workspace / ".soulboard-no-builtin-skills")

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
    ) -> str:
        del skill_names
        del channel
        system_path = self.workspace / self.SYSTEM_FILENAME
        if system_path.exists():
            base_prompt = system_path.read_text(encoding="utf-8")
        else:
            base_prompt = self._build_default_system_prompt()
        workspace_skills_prompt = self._build_workspace_skills_prompt()
        if workspace_skills_prompt:
            return f"{base_prompt}\n\n---\n\n{workspace_skills_prompt}"
        return base_prompt

    def _build_default_system_prompt(self) -> str:
        return f"""# Soulboard

You are the active soul {self.soul_id!r} running inside nanobot-soulboard.

## Runtime
{platform.platform()}

## Soulboard Rules
- When a user asks you to do something, you should use the correct tool to do it without frequently re-confirming the intent or ask for permission.
- Never spawn a subagent unless the user explicitly tells you to do so.
- MCP servers are available as `mcp_*`. If the user asks you whether a MCP server is connected, you should reply "yes" if you see such tools.
"""

    def _build_workspace_skills_prompt(self) -> str:
        parts: list[str] = []

        always_skills = self.workspace_skills.get_always_skills()
        if always_skills:
            always_content = self.workspace_skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.workspace_skills.build_skills_summary()
        if skills_summary:
            parts.append(
                "# Skills\n\n"
                "The following workspace skills extend your capabilities. To use a skill, read its SKILL.md file "
                "using the read_file tool.\n\n"
                f"{skills_summary}"
            )

        return "\n\n".join(parts)
