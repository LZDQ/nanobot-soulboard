"""Soulboard-specific subagent management."""

from collections.abc import Callable
from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.agent.skills import SkillsLoader
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ToolsConfig
from nanobot.providers.base import LLMProvider
from nanobot.utils.prompt_templates import render_template

from nanobot_soulboard.agent.search import replace_grep_tool
from nanobot_soulboard.agent.shell import SoulExecTool


class SoulSubagentManager(SubagentManager):
    """Build subagent tool registries with Soulboard's downstream policy."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        tools_config: ToolsConfig | None = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        disabled_tools: set[str] | None = None,
        timezone: str | None = None,
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
    ):
        self.disabled_tools = set(disabled_tools or set())
        self.timezone = timezone
        super().__init__(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=model,
            tools_config=tools_config,
            max_tool_result_chars=max_tool_result_chars,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
            max_iterations=max_iterations,
            max_concurrent_subagents=max_concurrent_subagents,
            llm_wall_timeout_for_session=llm_wall_timeout_for_session,
        )

    def _build_subagent_prompt(self, workspace: Path | None = None) -> str:
        """Build a subagent prompt using the owning soul's timezone."""
        root = workspace or self.workspace
        time_context = ContextBuilder._build_runtime_context(
            None,
            None,
            self.timezone,
        )
        skills_summary = SkillsLoader(
            root,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_context,
            workspace=str(root),
            skills_summary=skills_summary or "",
        )

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
    ) -> ToolRegistry:
        root = self.workspace if workspace is None else workspace
        config = tools_config if tools_config is not None else self._subagent_tools_config()
        registry = super()._build_tools(workspace=root, tools_config=config)
        replace_grep_tool(registry)

        if config.exec.enable and registry.has("exec"):
            registry.unregister("exec")
            registry.register(
                SoulExecTool(
                    workspace=root,
                    timeout=config.exec.timeout,
                    restrict_to_workspace=config.restrict_to_workspace,
                    sandbox=config.exec.sandbox,
                    path_append=config.exec.path_append,
                    allowed_env_keys=config.exec.allowed_env_keys,
                    timezone=self.timezone,
                    allow_patterns=config.exec.allow_patterns,
                    deny_patterns=config.exec.deny_patterns,
                )
            )

        for name in self.disabled_tools:
            registry.unregister(name)

        return registry
