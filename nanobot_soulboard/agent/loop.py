"""Soulboard-specific agent loop."""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger
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


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


class SoulAgentLoop(AgentLoop):
    """AgentLoop subclass with soulboard-specific prompt and tool wiring."""

    def __init__(
        self,
        *args,
        soul_id: str,
        disabled_skills: list[str] | None = None,
        disabled_tools: list[str] | None = None,
        **kwargs,
    ):
        self.soul_id = soul_id
        self.disabled_tools = set(disabled_tools or [])
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
        if self.cron_service:
            self.tools.unregister("cron")
            self.tools.register(
                SoulCronTool(
                    self.cron_service,
                    default_timezone=self.context.timezone or "UTC",
                )
            )
        self._apply_disabled_tools()

    def _apply_disabled_tools(self) -> None:
        for name in self.disabled_tools:
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

    async def _close_mcp_stack_from_owner(
        self,
        server_name: str,
        server_stack: AsyncExitStack,
    ) -> None:
        try:
            await server_stack.aclose()
        except asyncio.CancelledError:
            if _current_task_is_cancelling():
                raise
            logger.debug("MCP server '{}' cleanup cancelled by SDK", server_name)
        except (RuntimeError, BaseExceptionGroup) as e:
            logger.debug("MCP server '{}' cleanup error: {}", server_name, e)

    async def _connect_single_mcp_server_from_owner(
        self,
        name: str,
        cfg: Any,
    ) -> tuple[str, AsyncExitStack | None]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.sse import sse_client
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client

        server_stack = AsyncExitStack()
        await server_stack.__aenter__()
        try:
            transport_type = cfg.type
            if not transport_type:
                if cfg.command:
                    transport_type = "stdio"
                elif cfg.url:
                    transport_type = (
                        "sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp"
                    )
                else:
                    logger.warning("MCP server '{}': no command or url configured, skipping", name)
                    await self._close_mcp_stack_from_owner(name, server_stack)
                    return name, None

            if transport_type in {"sse", "streamableHttp"}:
                ok, error = mcp_tools.validate_url_target(cfg.url)
                if not ok:
                    logger.warning(
                        "MCP server '{}': blocked unsafe URL {} ({})",
                        name,
                        cfg.url,
                        error,
                    )
                    await self._close_mcp_stack_from_owner(name, server_stack)
                    return name, None

            if transport_type == "stdio":
                command, args, env = mcp_tools._normalize_windows_stdio_command(
                    cfg.command,
                    cfg.args,
                    cfg.env or None,
                )
                params = StdioServerParameters(
                    command=command,
                    args=args,
                    env=env,
                    cwd=cfg.cwd or None,
                )
                read, write = await server_stack.enter_async_context(stdio_client(params))
            elif transport_type == "sse":
                if not await mcp_tools._probe_http_url(cfg.url):
                    logger.warning("MCP server '{}': {} unreachable, skipping", name, cfg.url)
                    await self._close_mcp_stack_from_owner(name, server_stack)
                    return name, None

                def httpx_client_factory(
                    headers: dict[str, str] | None = None,
                    timeout: httpx.Timeout | None = None,
                    auth: httpx.Auth | None = None,
                ) -> httpx.AsyncClient:
                    merged_headers = {
                        "Accept": "application/json, text/event-stream",
                        **(cfg.headers or {}),
                        **(headers or {}),
                    }
                    return httpx.AsyncClient(
                        headers=merged_headers or None,
                        event_hooks={"request": [mcp_tools._validate_mcp_request_url]},
                        follow_redirects=True,
                        timeout=timeout,
                        auth=auth,
                    )

                read, write = await server_stack.enter_async_context(
                    sse_client(cfg.url, httpx_client_factory=httpx_client_factory)
                )
            elif transport_type == "streamableHttp":
                if not await mcp_tools._probe_http_url(cfg.url):
                    logger.warning("MCP server '{}': {} unreachable, skipping", name, cfg.url)
                    await self._close_mcp_stack_from_owner(name, server_stack)
                    return name, None

                http_client = await server_stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=cfg.headers or None,
                        event_hooks={"request": [mcp_tools._validate_mcp_request_url]},
                        follow_redirects=True,
                        timeout=None,
                    )
                )
                read, write, _ = await server_stack.enter_async_context(
                    streamable_http_client(cfg.url, http_client=http_client)
                )
            else:
                logger.warning("MCP server '{}': unknown transport type '{}'", name, transport_type)
                await self._close_mcp_stack_from_owner(name, server_stack)
                return name, None

            session = await server_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            enabled_tools = set(cfg.enabled_tools)
            allow_all_tools = "*" in enabled_tools
            registered_count = 0
            matched_enabled_tools: set[str] = set()
            available_raw_names = [tool_def.name for tool_def in tools.tools]
            available_wrapped_names = [
                mcp_tools._sanitize_name(f"mcp_{name}_{tool_def.name}")
                for tool_def in tools.tools
            ]
            for tool_def in tools.tools:
                wrapped_name = mcp_tools._sanitize_name(f"mcp_{name}_{tool_def.name}")
                if (
                    not allow_all_tools
                    and tool_def.name not in enabled_tools
                    and wrapped_name not in enabled_tools
                ):
                    logger.debug(
                        "MCP: skipping tool '{}' from server '{}' (not in enabledTools)",
                        wrapped_name,
                        name,
                    )
                    continue
                wrapper = mcp_tools.MCPToolWrapper(
                    session,
                    name,
                    tool_def,
                    tool_timeout=cfg.tool_timeout,
                )
                self.tools.register(wrapper)
                logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)
                registered_count += 1
                if enabled_tools:
                    if tool_def.name in enabled_tools:
                        matched_enabled_tools.add(tool_def.name)
                    if wrapped_name in enabled_tools:
                        matched_enabled_tools.add(wrapped_name)

            if enabled_tools and not allow_all_tools:
                unmatched_enabled_tools = sorted(enabled_tools - matched_enabled_tools)
                if unmatched_enabled_tools:
                    logger.warning(
                        "MCP server '{}': enabledTools entries not found: {}. Available raw names: {}. "
                        "Available wrapped names: {}",
                        name,
                        ", ".join(unmatched_enabled_tools),
                        ", ".join(available_raw_names) or "(none)",
                        ", ".join(available_wrapped_names) or "(none)",
                    )

            try:
                resources_result = await session.list_resources()
                for resource in resources_result.resources:
                    wrapper = mcp_tools.MCPResourceWrapper(
                        session,
                        name,
                        resource,
                        resource_timeout=cfg.tool_timeout,
                    )
                    self.tools.register(wrapper)
                    registered_count += 1
                    logger.debug(
                        "MCP: registered resource '{}' from server '{}'",
                        wrapper.name,
                        name,
                    )
            except Exception as e:
                logger.debug("MCP server '{}': resources not supported or failed: {}", name, e)

            try:
                prompts_result = await session.list_prompts()
                for prompt in prompts_result.prompts:
                    wrapper = mcp_tools.MCPPromptWrapper(
                        session,
                        name,
                        prompt,
                        prompt_timeout=cfg.tool_timeout,
                    )
                    self.tools.register(wrapper)
                    registered_count += 1
                    logger.debug("MCP: registered prompt '{}' from server '{}'", wrapper.name, name)
            except Exception as e:
                logger.debug("MCP server '{}': prompts not supported or failed: {}", name, e)

            logger.info(
                "MCP server '{}': connected, {} capabilities registered", name, registered_count
            )
            return name, server_stack
        except asyncio.CancelledError:
            mcp_tools._unregister_server_tools(self, self.tools, name)
            await self._close_mcp_stack_from_owner(name, server_stack)
            if _current_task_is_cancelling():
                raise
            logger.warning("MCP server '{}': connection cancelled by SDK", name)
            return name, None
        except BaseException as e:
            mcp_tools._unregister_server_tools(self, self.tools, name)
            await self._close_mcp_stack_from_owner(name, server_stack)
            logger.warning("MCP server '{}': failed to connect: {}", name, e)
            return name, None

    async def _connect_mcp_servers_from_owner(
        self,
        mcp_servers: dict[str, Any],
    ) -> dict[str, AsyncExitStack]:
        server_stacks: dict[str, AsyncExitStack] = {}
        for name, cfg in mcp_servers.items():
            result_name, stack = await self._connect_single_mcp_server_from_owner(name, cfg)
            if stack is not None:
                server_stacks[result_name] = stack
        return server_stacks

    async def connect_mcp_from_owner(self) -> None:
        missing_servers = {
            name: cfg for name, cfg in self._mcp_servers.items() if name not in self._mcp_stacks
        }
        if self._mcp_connecting or not missing_servers:
            return
        self._mcp_connecting = True
        try:
            connected = await self._connect_mcp_servers_from_owner(missing_servers)
            self._mcp_stacks.update(connected)
            self._mcp_connected = bool(self._mcp_stacks)
            if connected:
                logger.info("MCP connected servers: {}", sorted(connected))
            else:
                logger.warning("No MCP servers connected successfully (will retry next message)")
        finally:
            self._mcp_connecting = False
        self._apply_disabled_tools()
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
        stack = self._mcp_stacks.pop(server_name, None)
        if stack is not None:
            await self._close_mcp_stack_from_owner(server_name, stack)
        connected = await self._connect_mcp_servers_from_owner({server_name: cfg})
        self._mcp_stacks.update(connected)
        self._mcp_connected = bool(self._mcp_stacks)
        if server_name not in connected:
            return None
        self._apply_disabled_tools()
        self._attach_soulboard_mcp_reconnect_handlers()
        return self.tools.get(tool_name)

    async def _connect_mcp(self) -> None:
        if self._mcp_lifecycle_owned_by_soulboard:
            self._request_mcp_owner_connect()
            return
        await self.connect_mcp_from_owner()
