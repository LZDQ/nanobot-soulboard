"""Soulboard-specific subagent management."""

from pathlib import Path
from typing import Callable

from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ToolsConfig
from nanobot.providers.base import LLMProvider

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
        max_iterations: int | None = None,
        max_concurrent_subagents: int | None = None,
        llm_wall_timeout_for_session: Callable[[str | None], float | None] | None = None,
    ):
        self.disabled_tools = set(disabled_tools or set())
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

    def _build_tools(
        self,
        workspace: Path | None = None,
        tools_config: ToolsConfig | None = None,
    ) -> ToolRegistry:
        root = self.workspace if workspace is None else workspace
        config = tools_config if tools_config is not None else self._subagent_tools_config()
        registry = super()._build_tools(workspace=root, tools_config=config)

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
                    allow_patterns=config.exec.allow_patterns,
                    deny_patterns=config.exec.deny_patterns,
                )
            )

        for name in self.disabled_tools:
            registry.unregister(name)

        return registry
