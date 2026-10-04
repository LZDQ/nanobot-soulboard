"""Soulboard-specific context builder."""

import platform
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from nanobot.agent.context import ContextBuilder, runtime_lines
from nanobot.session.goal_state import goal_state_runtime_lines


class SoulboardContextBuilder(ContextBuilder):
    """Build soulboard system prompts without delegating to upstream assembly."""

    SYSTEM_FILENAME = "SYSTEM.md"

    def __init__(
        self,
        workspace: Path,
        soul_id: str,
        timezone: str | None = None,
        disabled_skills: list[str] | None = None,
        include_timestamps: bool = True,
        include_runtime_context: bool = True,
    ):
        super().__init__(workspace, timezone=timezone, disabled_skills=disabled_skills)
        self.soul_id = soul_id
        self.include_timestamps = include_timestamps
        self.include_runtime_context = include_runtime_context

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        sender_id: str | None = None,
        session_summary: str | None = None,
        session_metadata: Mapping[str, Any] | None = None,
        current_runtime_lines: Sequence[str] | None = None,
        workspace: Path | None = None,
        runtime_state: Any | None = None,
        inbound_message: Any | None = None,
        skip_runtime_lines: bool = False,
        include_memory_recent_history: bool = True,
        session_key: str | None = None,
        unified_session: bool = False,
    ) -> list[dict[str, Any]]:
        """Build model messages with optional runtime metadata."""
        root = workspace or self.workspace
        user_content = self._build_user_content(current_message, media)
        merged: str | list[dict[str, Any]] = user_content

        if self.include_runtime_context:
            extra = [*goal_state_runtime_lines(session_metadata)]
            if runtime_state is not None and inbound_message is not None:
                extra.extend(
                    runtime_lines(
                        runtime_state,
                        inbound_message,
                        root,
                        skip=skip_runtime_lines,
                    )
                )
            if current_runtime_lines:
                extra.extend(line for line in current_runtime_lines if line)
            runtime_context = self._build_runtime_context(
                channel,
                chat_id,
                self.timezone,
                sender_id=sender_id,
                supplemental_lines=extra or None,
            )
            if isinstance(user_content, str):
                merged = f"{user_content}\n\n{runtime_context}"
            else:
                merged = user_content + [{"type": "text", "text": runtime_context}]

        messages = [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    skill_names,
                    channel=channel,
                    session_summary=session_summary,
                    workspace=root,
                    include_memory_recent_history=include_memory_recent_history,
                    session_key=session_key,
                    unified_session=unified_session,
                ),
            },
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
        **kwargs: Any,
    ) -> str:
        # build_messages() calls build_system_prompt with upstream-compatible
        # extra kwargs (session_summary, workspace,
        # include_memory_recent_history, session_key, unified_session).
        # Soulboard builds its prompt purely from SYSTEM.md + skills, so we
        # accept and ignore them to stay call-compatible.
        del skill_names, channel, kwargs
        system_path = self.workspace / self.SYSTEM_FILENAME
        if system_path.exists():
            base_prompt = system_path.read_text(encoding="utf-8")
        else:
            base_prompt = self._build_default_system_prompt()
        skills_prompt = self._build_skills_prompt()
        if skills_prompt:
            return f"{base_prompt}\n\n---\n\n{skills_prompt}"
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

    def _build_skills_prompt(self) -> str:
        parts: list[str] = []

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(
                "# Skills\n\n"
                "The following skills extend your capabilities. To use a skill, read its SKILL.md file "
                "using the read_file tool.\n\n"
                f"{skills_summary}"
            )

        return "\n\n".join(parts)
