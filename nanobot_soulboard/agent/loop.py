"""Soulboard-specific agent loop."""

import asyncio
from dataclasses import dataclass

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools import mcp as mcp_tools
from nanobot.agent.tools.base import Tool

from nanobot_soulboard.agent.shell import SoulExecTool
from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.cron import SoulCronTool


@dataclass(frozen=True)
class SoulMcpReconnectRequest:
    """A reconnect request that must be fulfilled by the MCP owner task."""

    server_name: str
    tool_name: str
    future: asyncio.Future[Tool | None]


class SoulAgentLoop(AgentLoop):
    """AgentLoop subclass with soulboard-specific prompt and tool wiring."""

    def __init__(
        self,
        *args,
        soul_id: str,
        disabled_skills: list[str] | None = None,
        tool_overrides: dict[str, bool] | None = None,
        **kwargs,
    ):
        self.soul_id = soul_id
        self.tool_overrides = dict(tool_overrides or {})
        self._mcp_lifecycle_owned_by_soulboard = False
        self._mcp_connect_requested: asyncio.Event | None = None
        self._mcp_reconnect_requests: asyncio.Queue[SoulMcpReconnectRequest] | None = None
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
                    allow_patterns=self.exec_config.allow_patterns,
                    deny_patterns=self.exec_config.deny_patterns,
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
        self._apply_tool_overrides()

    def _apply_tool_overrides(self) -> None:
        for name, enabled in self.tool_overrides.items():
            if not enabled:
                self.tools.unregister(name)

    def use_soulboard_mcp_lifecycle(
        self,
        connect_requested: asyncio.Event,
        reconnect_requests: asyncio.Queue[SoulMcpReconnectRequest],
    ) -> None:
        self._mcp_lifecycle_owned_by_soulboard = True
        self._mcp_connect_requested = connect_requested
        self._mcp_reconnect_requests = reconnect_requests

    def _has_missing_mcp_servers(self) -> bool:
        return any(name not in self._mcp_stacks for name in self._mcp_servers)

    def _request_mcp_owner_connect(self) -> None:
        if self._mcp_connect_requested is not None and self._has_missing_mcp_servers():
            self._mcp_connect_requested.set()

    async def _reconnect_mcp_from_owner(
        self,
        server_name: str,
        tool_name: str,
        stale_tool: Tool,
    ) -> Tool | None:
        del stale_tool
        if self._mcp_reconnect_requests is None:
            self._request_mcp_owner_connect()
            return None
        future: asyncio.Future[Tool | None] = asyncio.get_running_loop().create_future()
        await self._mcp_reconnect_requests.put(
            SoulMcpReconnectRequest(
                server_name=server_name,
                tool_name=tool_name,
                future=future,
            )
        )
        return await future

    def _attach_soulboard_mcp_reconnect_handlers(self) -> None:
        for tool_name in list(self.tools.tool_names):
            tool = self.tools.get(tool_name)
            if isinstance(tool, mcp_tools._MCPWrapperBase):
                tool.set_reconnect_handler(self._reconnect_mcp_from_owner)

    async def connect_mcp_from_owner(self) -> None:
        await super()._connect_mcp()
        self._apply_tool_overrides()
        self._attach_soulboard_mcp_reconnect_handlers()

    async def reconnect_mcp_server_from_owner(
        self,
        server_name: str,
        tool_name: str,
    ) -> Tool | None:
        cfg = self._mcp_servers.get(server_name)
        if cfg is None:
            return None
        mcp_tools._unregister_server_tools(self, self.tools, server_name)
        await mcp_tools._close_server(self, server_name)
        connected = await mcp_tools.connect_mcp_servers({server_name: cfg}, self.tools)
        self._mcp_stacks.update(connected)
        self._mcp_connected = bool(self._mcp_stacks)
        if server_name not in connected:
            return None
        self._apply_tool_overrides()
        self._attach_soulboard_mcp_reconnect_handlers()
        return self.tools.get(tool_name)

    async def _connect_mcp(self) -> None:
        if self._mcp_lifecycle_owned_by_soulboard:
            self._request_mcp_owner_connect()
            return
        await self.connect_mcp_from_owner()
