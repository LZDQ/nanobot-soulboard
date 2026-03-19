"""Soulboard-specific context builder."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder


class SoulboardContextBuilder(ContextBuilder):
    """Inject soulboard runtime metadata without changing upstream prompt assembly."""

    def __init__(self, workspace: Path, soul_id: str):
        super().__init__(workspace)
        self.soul_id = soul_id

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        base = super().build_system_prompt(skill_names)
        soulboard_block = (
            "# Soulboard\n\n"
            f"- Active soul: {self.soul_id}\n"
            f"- Soul workspace: {self.workspace.expanduser().resolve()}\n"
            "- Working directory changes are disabled in this runtime.\n"
        )
        return f"{base}\n\n---\n\n{soulboard_block}"
