"""Soulboard-specific shell tools."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.agent.tools.shell import ExecTool


@tool_parameters(
    tool_parameters_schema(
        command=StringSchema("The shell command to execute"),
        timeout=IntegerSchema(
            60,
            description=(
                "Timeout in seconds. Increase for long-running commands "
                "like compilation or installation (default 60, max 600)."
            ),
            minimum=1,
            maximum=600,
        ),
        required=["command"],
    )
)
class SoulExecTool(ExecTool):
    """Workspace-pinned exec tool without an exposed working directory parameter."""

    def __init__(
        self,
        workspace: Path,
        timeout: int = 60,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        sandbox: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
    ):
        self.workspace = workspace
        super().__init__(
            timeout=timeout,
            working_dir=str(workspace),
            deny_patterns=deny_patterns,
            allow_patterns=allow_patterns,
            restrict_to_workspace=restrict_to_workspace,
            sandbox=sandbox,
            path_append=path_append,
            allowed_env_keys=allowed_env_keys,
        )

    async def execute(
        self,
        command: str,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> str:
        return await super().execute(
            command=command,
            working_dir=str(self.workspace),
            timeout=timeout,
        )
