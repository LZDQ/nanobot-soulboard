"""Soulboard-specific context builder."""

from __future__ import annotations

import platform
from pathlib import Path

from nanobot.agent.context import ContextBuilder


class SoulboardContextBuilder(ContextBuilder):
    """Build soulboard system prompts without delegating to upstream assembly."""

    SYSTEM_FILENAME = "SYSTEM.md"

    def __init__(self, workspace: Path, soul_id: str):
        super().__init__(workspace)
        self.soul_id = soul_id

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        del skill_names
        system_path = self.workspace / self.SYSTEM_FILENAME
        if system_path.exists():
            return system_path.read_text(encoding="utf-8")
        return self._build_default_system_prompt()

    def _build_default_system_prompt(self) -> str:
        workspace_path = self.workspace.expanduser().resolve()
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# Soulboard

You are the active soul "{self.soul_id}" running inside nanobot-soulboard.

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Use this workspace as the source of truth for project files and instructions.
- If {self.SYSTEM_FILENAME} exists here, it overrides this default prompt entirely.

## Soulboard Rules
- Only one soul runs at a time.
- State intent before tool calls, but never claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
"""
