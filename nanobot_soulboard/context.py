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
        return f"""# Soulboard

You are the active soul {self.soul_id!r} running inside nanobot-soulboard.

## Runtime
{platform.platform()}

## Soulboard Rules
- When a user asks you to do something, you should use the correct tool to do it without frequently re-confirming the intent or ask for permission.
- Never spawn a subagent unless the user explicitly tells you to do so.
- MCP servers are available as `mcp_*`. If the user asks you whether a MCP server is connected, you should reply "yes" if you see such tools.
"""
