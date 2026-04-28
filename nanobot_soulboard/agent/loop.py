"""Soulboard-specific agent loop."""

from nanobot.agent.loop import AgentLoop

from nanobot_soulboard.agent.shell import SoulExecTool
from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.cron import SoulCronTool


class SoulAgentLoop(AgentLoop):
    """AgentLoop subclass with soulboard-specific prompt and tool wiring."""

    def __init__(
        self,
        *args,
        soul_id: str,
        disabled_skills: list[str] | None = None,
        **kwargs,
    ):
        self.soul_id = soul_id
        super().__init__(*args, disabled_skills=disabled_skills, **kwargs)
        self.context = SoulboardContextBuilder(
            self.workspace,
            soul_id=soul_id,
            timezone=self.context.timezone,
            disabled_skills=disabled_skills,
        )

    def _register_default_tools(self) -> None:
        super()._register_default_tools()
        if self.exec_config.enable:
            self.tools.unregister("exec")
            self.tools.register(
                SoulExecTool(
                    workspace=self.workspace,
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                )
            )
        self.tools.unregister("spawn")
        if self.cron_service:
            self.tools.unregister("cron")
            self.tools.register(
                SoulCronTool(
                    self.cron_service,
                    default_timezone=self.context.timezone or "UTC",
                )
            )
