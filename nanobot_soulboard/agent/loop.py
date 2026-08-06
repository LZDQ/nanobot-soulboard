"""Soulboard-specific agent loop."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from loguru import logger
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools import mcp as mcp_tools
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.session.goal_state import runner_wall_llm_timeout_s
from nanobot.session.manager import Session

from nanobot_soulboard.agent.shell import SoulExecTool
from nanobot_soulboard.agent.subagent import SoulSubagentManager
from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.cron import SoulCronTool


def _add_soul_context_to_tool_call_log(record: dict[str, Any]) -> None:
    """Add Soulboard routing context to upstream tool-call log messages."""
    if (
        record["name"] != "nanobot.agent.progress_hook"
        or record["function"] != "before_execute_tools"
        or not record["message"].startswith("Tool call: ")
    ):
        return

    soul_id = record["extra"].get("soul_id", "-")
    session_key = record["extra"].get("session_key", "-")
    record["message"] = (
        f"soul={soul_id} session={session_key} | {record['message']}"
    )


logger.configure(patcher=_add_soul_context_to_tool_call_log)


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
        self._mcp_connect_requested = asyncio.Event()
        self._mcp_reconnect_requests: asyncio.Queue[SoulMcpReconnectRequest] = (
            asyncio.Queue()
        )
        super().__init__(*args, disabled_skills=disabled_skills, **kwargs)
        self.context = SoulboardContextBuilder(
            self.workspace,
            soul_id=soul_id,
            timezone=self.context.timezone,
            disabled_skills=disabled_skills,
        )

    async def _run_agent_loop(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """Run upstream iterations with async-local Soulboard log context."""
        session = kwargs.get("session")
        session_key = (
            session.key
            if isinstance(session, Session)
            else kwargs.get("session_key")
        )
        with logger.contextualize(
            soul_id=self.soul_id,
            session_key=session_key or "-",
        ):
            return await super()._run_agent_loop(*args, **kwargs)

    def _register_default_tools(self) -> None:
        if not isinstance(self.subagents, SoulSubagentManager):
            upstream_manager = self.subagents
            self.subagents = SoulSubagentManager(
                provider=self.provider,
                workspace=self.workspace,
                bus=self.bus,
                model=self.model,
                tools_config=self.tools_config,
                max_tool_result_chars=self.max_tool_result_chars,
                restrict_to_workspace=self.restrict_to_workspace,
                disabled_skills=sorted(upstream_manager.disabled_skills),
                disabled_tools=self.disabled_tools,
                max_iterations=self.max_iterations,
                max_concurrent_subagents=upstream_manager.max_concurrent_subagents,
                llm_wall_timeout_for_session=lambda session_key: runner_wall_llm_timeout_s(
                    self.sessions,
                    session_key,
                ),
            )
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

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        media: list[str] | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        ephemeral: bool = False,
        tools: ToolRegistry | None = None,
        register_pending_queue: bool = False,
    ) -> OutboundMessage | None:
        """Upstream ``process_direct`` plus an opt-in mid-turn injection queue.

        With *register_pending_queue*, the session's injection queue is
        published for the duration of the turn (as bus dispatch does), so bus
        messages targeting the session — e.g. subagent results — are injected
        into this turn instead of dispatched as separate turns. The call then
        stays open while subagents spawned in the turn are still running.

        The queued branch mirrors ``AgentLoop.process_direct`` and must be
        kept in sync with it on upstream bumps.
        """
        if not register_pending_queue:
            return await super().process_direct(
                content=content,
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                media=media,
                on_progress=on_progress,
                on_stream=on_stream,
                on_stream_end=on_stream_end,
                ephemeral=ephemeral,
                tools=tools,
            )

        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [],
        )
        # Share the dispatch lock so direct calls serialize with bus turns.
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        try:
            async with lock:
                kwargs: dict[str, Any] = {
                    "session_key": session_key,
                    "on_progress": on_progress,
                    "on_stream": on_stream,
                    "on_stream_end": on_stream_end,
                    "ephemeral": ephemeral,
                }
                if tools is not None:
                    kwargs["tools"] = tools
                # Only the lock owner may publish the injection queue.
                pending: asyncio.Queue = asyncio.Queue(maxsize=20)
                self._pending_queues[session_key] = pending
                kwargs["pending_queue"] = pending
                try:
                    return await self._process_message(
                        msg,
                        **kwargs,
                    )
                finally:
                    # Drain any messages still in the pending queue and
                    # re-publish them to the bus so they are processed as
                    # fresh inbound messages rather than silently lost.
                    if self._pending_queues.get(session_key) is pending:
                        self._pending_queues.pop(session_key, None)
                    leftover = 0
                    while True:
                        try:
                            item = pending.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        await self.bus.publish_inbound(item)
                        leftover += 1
                    if leftover:
                        logger.info(
                            "Re-published {} leftover message(s) to bus for session {}",
                            leftover, session_key,
                        )
        finally:
            await self._runtime_events().run_status_changed(msg, session_key, "idle")
            self._runtime_events().clear_turn(session_key)

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """Persist subagent follow-ups as user turns, not upstream's assistant-role prefill.

        A message list ending in an assistant-role turn reads to most
        providers as "continue writing this", not "here is new information".
        That made the ghost-turn path (below) reliably reproduce the model
        just re-narrating its own prior status instead of reacting to the
        subagent's result. Using "user" here keeps the persisted shape
        identical to mid-turn injections (``AgentLoop._drain_pending``),
        which inject announces as user messages and do not exhibit the bug.

        Must be kept in sync with upstream ``AgentLoop._persist_subagent_followup``.
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
            m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
            for m in session.messages
        ):
            return False
        session.add_message(
            "user",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    async def _process_system_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        pending_queue: asyncio.Queue | None = None,
    ) -> OutboundMessage | None:
        """Upstream ``_process_system_message`` with the current turn always
        addressed as ``role="user"``, including for subagent announces.

        Upstream sets ``current_role="assistant"`` for subagent messages,
        which — combined with the assistant-role persistence this overrides
        above — leaves the LLM call ending in an assistant-role message.
        Providers treat that as a completion to continue, not new input to
        respond to, so the model silently ignores the announce and repeats
        its last status line. See ``_persist_subagent_followup`` above.

        Must be kept in sync with upstream ``AgentLoop._process_system_message``
        on version bumps.
        """
        channel, chat_id = (
            msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
        )
        logger.info("Processing system message from {}", msg.sender_id)
        key = msg.session_key_override or f"{channel}:{chat_id}"
        session = self.sessions.get_or_create(key)
        if self._restore_runtime_checkpoint(session):
            self.sessions.save(session)
        if self._restore_pending_user_turn(session):
            self.sessions.save(session)

        session, pending = self.auto_compact.prepare_session(session, key)
        if pending:
            logger.info("Memory compact triggered for session {}", key)

        await self.consolidator.maybe_consolidate_by_tokens(
            session,
            replay_max_messages=self._max_messages,
        )
        is_subagent = msg.sender_id == "subagent"
        if is_subagent and self._persist_subagent_followup(session, msg):
            logger.debug("Subagent result persisted for session {}", key)
            self.sessions.save(session)
        self._set_tool_context(
            channel, chat_id, msg.metadata.get("message_id"),
            msg.metadata, session_key=key,
        )
        history = session.get_history(
            max_messages=self._max_messages,
            max_tokens=self._replay_token_budget(),
            include_timestamps=True,
        )
        workspace_scope = self.workspace_scopes.for_message(msg, session.metadata)

        messages = self.context.build_messages(
            history=history,
            current_message="" if is_subagent else msg.content,
            channel=channel,
            chat_id=chat_id,
            current_role="user",
            sender_id=msg.sender_id,
            session_summary=pending,
            session_metadata=session.metadata,
            workspace=workspace_scope.project_path,
            runtime_state=self,
            inbound_message=msg,
            skip_runtime_lines=is_subagent,
            session_key=key,
            unified_session=self._unified_session,
        )
        t_wall = time.time()
        final_content, _, all_msgs, stop_reason, _ = await self._run_agent_loop(
            messages, session=session, channel=channel, chat_id=chat_id,
            message_id=msg.metadata.get("message_id"),
            metadata=msg.metadata,
            session_key=key,
            pending_queue=pending_queue,
        )
        wall_done = time.time()
        latency_ms = max(0, int((wall_done - t_wall) * 1000))
        self._save_turn(session, all_msgs, 1 + len(history), turn_latency_ms=latency_ms)
        self._runtime_events().record_turn_latency(key, latency_ms)
        session.enforce_file_cap(
            on_archive=partial(self.context.memory.raw_archive, session_key=key)
        )
        self._clear_runtime_checkpoint(session)
        self.sessions.save(session)
        self._schedule_background(
            self.consolidator.maybe_consolidate_by_tokens(
                session,
                replay_max_messages=self._max_messages,
            )
        )
        content = final_content or "Background task completed."
        outbound_metadata: dict[str, Any] = {}
        if channel == "slack" and key.startswith("slack:") and key.count(":") >= 2:
            outbound_metadata["slack"] = {"thread_ts": key.split(":", 2)[2]}
        if origin_message_id := msg.metadata.get("origin_message_id"):
            outbound_metadata["origin_message_id"] = origin_message_id
        return OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            metadata=outbound_metadata,
        )

    def _has_missing_mcp_servers(self) -> bool:
        return any(name not in self._mcp_stacks for name in self._mcp_servers)

    def _request_mcp_owner_connect(self) -> None:
        if self._has_missing_mcp_servers():
            self._mcp_connect_requested.set()

    async def _reconnect_mcp_from_owner(
        self,
        server_name: str,
        tool_name: str,
        stale_tool: Tool,
    ) -> Tool | None:
        del stale_tool
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
        self._request_mcp_owner_connect()
