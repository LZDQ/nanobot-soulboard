"""Single-process runtime management for nanobot-soulboard."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger
from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.command.router import CommandContext
from nanobot.config.loader import save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.agent.tools.cron import CronTool
from nanobot.cron.types import CronJob
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import Session, SessionManager

from nanobot_soulboard.config import (
    SoulOverrides,
    SoulboardConfig,
    get_soulboard_config_path,
    get_souls_root,
    save_soulboard_config,
    validate_soul_id,
)
from nanobot_soulboard.cron import SoulCronService, SoulCronTool
from nanobot_soulboard.context import SoulboardContextBuilder

SOUL_PROMPT_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "SYSTEM.md")


@dataclass(frozen=True)
class SoulSpec:
    """Resolved soul runtime spec."""

    soul_id: str
    workspace: Path
    overrides: SoulOverrides


def discover_soul_specs(nano_root: Path, config: SoulboardConfig | None = None) -> list[SoulSpec]:
    """Resolve souls from config.json, using souls/{soul_id} as the default workspace."""
    config = config or SoulboardConfig()
    souls_root = get_souls_root(nano_root)
    specs: list[SoulSpec] = []
    for soul_id in sorted(validate_soul_id(soul_id) for soul_id in config.souls):
        overrides = config.souls.get(soul_id, SoulOverrides())
        workspace = Path(overrides.workspace).expanduser() if overrides.workspace else souls_root / soul_id
        specs.append(SoulSpec(soul_id=soul_id, workspace=workspace, overrides=overrides))
    return specs


def _copy_config(config: Config) -> Config:
    """Clone a nanobot config for per-soul in-memory overrides."""
    return Config.model_validate(deepcopy(config.model_dump(by_alias=True)))


def _apply_channel_selection(config: Config, enabled_channels: list[str]) -> None:
    selected = set(enabled_channels)
    channel_data = config.channels.model_dump(by_alias=True)
    for name, value in channel_data.items():
        if not isinstance(value, dict):
            continue
        value["enabled"] = name in selected
    config.channels = type(config.channels).model_validate(channel_data)


def _apply_mcp_selection(config: Config, enabled_mcp_servers: list[str]) -> None:
    if not enabled_mcp_servers:
        config.tools.mcp_servers = {}
        return

    selected = {}
    missing = []
    for name in enabled_mcp_servers:
        if name not in config.tools.mcp_servers:
            missing.append(name)
            continue
        selected[name] = config.tools.mcp_servers[name]
    if missing:
        missing_str = ", ".join(sorted(missing))
        raise ValueError(f"Unknown MCP server(s) in soulboard config: {missing_str}")
    config.tools.mcp_servers = selected


def build_runtime_config(base_config: Config, spec: SoulSpec) -> Config:
    """Build an in-memory nanobot config for one soul runtime."""
    config = _copy_config(base_config)
    config.agents.defaults.workspace = str(spec.workspace)
    if spec.overrides.model:
        config.agents.defaults.model = spec.overrides.model
    if spec.overrides.provider:
        config.agents.defaults.provider = spec.overrides.provider
    _apply_channel_selection(config, list(spec.overrides.channels))
    _apply_mcp_selection(config, list(spec.overrides.mcp_servers))
    return config


class SoulAgentLoop(AgentLoop):
    """AgentLoop wrapper that swaps in the soulboard context builder."""

    def __init__(self, *args, soul_id: str, **kwargs):
        session_manager = kwargs.pop("session_manager", None)
        super().__init__(*args, **kwargs)
        if session_manager is not None:
            self.sessions = session_manager
        self.context = SoulboardContextBuilder(self.workspace, soul_id=soul_id)

    def _register_default_tools(self) -> None:
        super()._register_default_tools()
        if self.cron_service:
            self.tools.register(SoulCronTool(self.cron_service, default_timezone=self.context.timezone or "UTC"))

    def _set_tool_context(
        self,
        channel: str,
        chat_id: str,
        message_id: str | None = None,
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super()._set_tool_context(channel, chat_id, message_id)
        if cron_tool := self.tools.get("cron"):
            if isinstance(cron_tool, SoulCronTool):
                cron_tool.set_context(channel, chat_id, session_key, metadata)

    def _persist_turn_slice(self, session: Session, messages: list[dict[str, Any]], start: int) -> int:
        """Persist one suffix of newly-created turn messages and return the new cursor."""
        for message in messages[start:]:
            entry = dict(message)
            role = entry.get("role")
            content = entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for item in content:
                        if (
                            item.get("type") == "text"
                            and isinstance(item.get("text"), str)
                            and item["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
                        ):
                            continue
                        if (
                            item.get("type") == "image_url"
                            and item.get("image_url", {}).get("url", "").startswith("data:image/")
                        ):
                            path = (item.get("_meta") or {}).get("path", "")
                            placeholder = f"[image: {path}]" if path else "[image]"
                            filtered.append({"type": "text", "text": placeholder})
                        else:
                            filtered.append(item)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()
        self.sessions.save(session)
        return len(messages)

    async def _run_agent_loop_incremental(
        self,
        session: Session,
        initial_messages: list[dict[str, Any]],
        persisted_until: int,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Run the agent loop and flush completed tool iterations incrementally."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []
        _raw_stream = on_stream
        _stream_buf = ""

        async def _filtered_stream(delta: str) -> None:
            nonlocal _stream_buf
            from nanobot.utils.helpers import strip_think

            prev_clean = strip_think(_stream_buf)
            _stream_buf += delta
            new_clean = strip_think(_stream_buf)
            incremental = new_clean[len(prev_clean):]
            if incremental and _raw_stream:
                await _raw_stream(incremental)

        while iteration < self.max_iterations:
            iteration += 1

            tool_defs = self.tools.get_definitions()
            if on_stream:
                response = await self.provider.chat_stream_with_retry(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                    on_content_delta=_filtered_stream,
                )
            else:
                response = await self.provider.chat_with_retry(
                    messages=messages,
                    tools=tool_defs,
                    model=self.model,
                )

            usage = response.usage or {}
            self._last_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            }

            if response.has_tool_calls:
                if on_stream and on_stream_end:
                    await on_stream_end(resuming=True)
                    _stream_buf = ""

                if on_progress:
                    if not on_stream:
                        thought = self._strip_think(response.content)
                        if thought:
                            await on_progress(thought)
                    tool_hint = self._tool_hint(response.tool_calls)
                    tool_hint = self._strip_think(tool_hint)
                    await on_progress(tool_hint, tool_hint=True)

                tool_call_dicts = [tool_call.to_openai_tool_call() for tool_call in response.tool_calls]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])

                self._set_tool_context(channel, chat_id, message_id, session_key, metadata)

                results = await asyncio.gather(
                    *(self.tools.execute(tool_call.name, tool_call.arguments) for tool_call in response.tool_calls),
                    return_exceptions=True,
                )

                for tool_call, result in zip(response.tool_calls, results):
                    if isinstance(result, BaseException):
                        result = f"Error: {type(result).__name__}: {result}"
                    messages = self.context.add_tool_result(messages, tool_call.id, tool_call.name, result)

                persisted_until = self._persist_turn_slice(session, messages, persisted_until)
                continue

            if on_stream and on_stream_end:
                await on_stream_end(resuming=False)
                _stream_buf = ""

            clean = self._strip_think(response.content)
            if response.finish_reason == "error":
                logger.error("LLM returned error: {}", (clean or "")[:200])
                final_content = clean or "Sorry, I encountered an error calling the AI model."
                break

            messages = self.context.add_assistant_message(
                messages,
                clean,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            )
            persisted_until = self._persist_turn_slice(session, messages, persisted_until)
            final_content = clean
            break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def _process_message(
        self,
        msg,
        session_key: str | None = None,
        on_progress=None,
        on_stream=None,
        on_stream_end=None,
    ):
        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            system_metadata = dict(msg.metadata or {})
            system_key = str(system_metadata.get("_system_session_key") or f"{channel}:{chat_id}")
            session = self.sessions.get_or_create(system_key)
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, system_metadata.get("message_id"), system_key, system_metadata)
            history = session.get_history(max_messages=0)
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content,
                channel=channel,
                chat_id=chat_id,
                current_role=current_role,
            )
            persisted_until = self._persist_turn_slice(session, messages, len(messages) - 1)
            final_content, _, _ = await self._run_agent_loop_incremental(
                session,
                messages,
                persisted_until,
                on_progress=on_progress,
                channel=channel,
                chat_id=chat_id,
                message_id=system_metadata.get("message_id"),
                session_key=system_key,
                metadata=system_metadata,
            )
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
            outbound_metadata = {key: value for key, value in system_metadata.items() if not key.startswith("_")}
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
                metadata=outbound_metadata,
            )

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"), key, msg.metadata or {})
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        history = session.get_history(max_messages=0)
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta)
            )

        persisted_until = self._persist_turn_slice(session, initial_messages, len(initial_messages) - 1)
        final_content, _, _ = await self._run_agent_loop_incremental(
            session,
            initial_messages,
            persisted_until,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            session_key=key,
            metadata=msg.metadata or {},
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        meta = dict(msg.metadata or {})
        if on_stream is not None:
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )


class SoulSessionManager(SessionManager):
    """Soulboard session manager."""


@dataclass
class _RunningSoul:
    """Internal supervisor-owned state for one started soul."""

    spec: SoulSpec
    config: Config
    provider: LLMProvider
    bus: MessageBus
    session_manager: SoulSessionManager
    cron_service: SoulCronService
    agent_loop: SoulAgentLoop
    channel_manager: ChannelManager
    agent_task: asyncio.Task | None = None
    channels_task: asyncio.Task | None = None
    cron_started: bool = False


class SoulSupervisor:
    """Create and track in-memory soul runtimes."""

    def __init__(
        self,
        base_config: Config,
        nano_root: Path,
        soulboard_config: SoulboardConfig | None = None,
        config_path: Path | None = None,
        base_config_path: Path | None = None,
        provider_factory: Callable[[Config], LLMProvider] | None = None,
    ):
        self.base_config = base_config
        self.soulboard_config = soulboard_config or SoulboardConfig()
        self.nano_root = nano_root
        self.config_path = config_path or get_soulboard_config_path(nano_root)
        self.base_config_path = base_config_path
        self.provider_factory = provider_factory
        self._running_souls: dict[str, _RunningSoul] = {}

    def list_specs(self) -> list[SoulSpec]:
        """List all resolved soul specs."""
        return discover_soul_specs(nano_root=self.nano_root, config=self.soulboard_config)

    def get_spec(self, soul_id: str) -> SoulSpec:
        """Return one soul spec or raise KeyError."""
        for spec in self.list_specs():
            if spec.soul_id == soul_id:
                return spec
        raise KeyError(f"Unknown soul: {soul_id}")

    def _prune_missing_mcp_servers(self, soul_id: str) -> None:
        """Remove stale MCP server references from one soul override set."""
        overrides = self.soulboard_config.souls.get(soul_id)
        if overrides is None:
            raise KeyError(f"Unknown soul: {soul_id}")
        if not overrides.mcp_servers:
            return
        known = set(self.base_config.tools.mcp_servers)
        filtered = [name for name in overrides.mcp_servers if name in known]
        if filtered == overrides.mcp_servers:
            return
        self.soulboard_config.souls[soul_id] = overrides.model_copy(update={"mcp_servers": filtered})
        save_soulboard_config(self.soulboard_config, self.config_path)

    def read_soul_prompt_files(self, soul_id: str) -> dict[str, str | None]:
        """Read the soul markdown prompt pack from its workspace."""
        spec = self.get_spec(soul_id)
        files: dict[str, str | None] = {}
        for filename in SOUL_PROMPT_FILES:
            path = spec.workspace / filename
            files[filename] = path.read_text(encoding="utf-8") if path.exists() else None
        return files

    def write_soul_prompt_files(self, soul_id: str, files: dict[str, str]) -> dict[str, str | None]:
        """Write the soul markdown prompt pack to its workspace."""
        spec = self.get_spec(soul_id)
        unknown = sorted(set(files) - set(SOUL_PROMPT_FILES))
        if unknown:
            unknown_str = ", ".join(unknown)
            raise ValueError(f"Unknown prompt file(s): {unknown_str}")
        spec.workspace.mkdir(parents=True, exist_ok=True)
        for filename in SOUL_PROMPT_FILES:
            if filename not in files:
                continue
            (spec.workspace / filename).write_text(files[filename], encoding="utf-8")
        return self.read_soul_prompt_files(soul_id)

    def modify_soul(self, soul_id: str, overrides: SoulOverrides) -> None:
        """Update one soul definition unless it is currently running."""
        if soul_id in self._running_souls:
            raise RuntimeError(f"Cannot modify running soul: {soul_id}")
        validate_soul_id(soul_id)
        self.soulboard_config.souls[soul_id] = overrides
        save_soulboard_config(self.soulboard_config, self.config_path)

    def create_soul(self, soul_id: str, overrides: SoulOverrides | None = None) -> SoulSpec:
        """Create and persist a new soul definition."""
        validate_soul_id(soul_id)
        if soul_id in self.soulboard_config.souls:
            raise ValueError(f"Soul already exists: {soul_id}")
        self.soulboard_config.souls[soul_id] = overrides or SoulOverrides()
        save_soulboard_config(self.soulboard_config, self.config_path)
        return self.get_spec(soul_id)

    def delete_soul(self, soul_id: str) -> None:
        """Delete a soul definition unless it is currently running."""
        if soul_id in self._running_souls:
            raise RuntimeError(f"Cannot delete running soul: {soul_id}")
        if soul_id not in self.soulboard_config.souls:
            raise KeyError(f"Unknown soul: {soul_id}")
        del self.soulboard_config.souls[soul_id]
        save_soulboard_config(self.soulboard_config, self.config_path)

    def list_mcp_servers(self) -> dict[str, MCPServerConfig]:
        """Return MCP server definitions from the base nanobot config."""
        return dict(sorted(self.base_config.tools.mcp_servers.items()))

    def create_mcp_server(self, name: str, definition: MCPServerConfig) -> MCPServerConfig:
        """Create one MCP server definition in the base nanobot config."""
        if name in self.base_config.tools.mcp_servers:
            raise ValueError(f"MCP server already exists: {name}")
        self.base_config.tools.mcp_servers[name] = definition
        if self.base_config_path is not None:
            save_config(self.base_config, self.base_config_path)
        return self.base_config.tools.mcp_servers[name]

    def update_mcp_server(self, name: str, definition: MCPServerConfig) -> MCPServerConfig:
        """Replace one MCP server definition in the base nanobot config."""
        if name not in self.base_config.tools.mcp_servers:
            raise KeyError(f"Unknown MCP server: {name}")
        self.base_config.tools.mcp_servers[name] = definition
        if self.base_config_path is not None:
            save_config(self.base_config, self.base_config_path)
        return self.base_config.tools.mcp_servers[name]

    def delete_mcp_server(self, name: str) -> None:
        """Delete one MCP server definition from the base nanobot config."""
        if name not in self.base_config.tools.mcp_servers:
            raise KeyError(f"Unknown MCP server: {name}")
        del self.base_config.tools.mcp_servers[name]
        if self.base_config_path is not None:
            save_config(self.base_config, self.base_config_path)

    def list_running_souls(self) -> list[str]:
        """Return IDs of currently running souls."""
        return sorted(self._running_souls)

    def is_running(self, soul_id: str) -> bool:
        """Return whether a soul is currently running."""
        return soul_id in self._running_souls

    def get_agent_loop(self, soul_id: str) -> SoulAgentLoop:
        """Return the running agent loop for a soul."""
        running = self._running_souls.get(soul_id)
        if running is None:
            raise KeyError(f"Soul is not running: {soul_id}")
        return running.agent_loop

    def _build_cron_service(self, spec: SoulSpec) -> SoulCronService:
        """Create a per-soul cron service rooted under the soul workspace."""
        return SoulCronService(spec.workspace / "cron" / "jobs.json", soul_id=spec.soul_id)

    def list_cron_jobs(self, soul_id: str) -> list[tuple[CronJob, str | None]]:
        """List one soul's cron jobs and their originating session keys."""
        spec = self.get_spec(soul_id)
        running = self._running_souls.get(soul_id)
        service = running.cron_service if running is not None else self._build_cron_service(spec)
        return service.list_jobs_with_session_keys(include_disabled=True)

    def _build_running_soul(self, soul_id: str) -> _RunningSoul:
        """Construct supervisor-owned runtime state without starting it."""
        if self.provider_factory is None:
            raise RuntimeError("SoulSupervisor requires a provider_factory to build runtimes")
        self._prune_missing_mcp_servers(soul_id)
        spec = self.get_spec(soul_id)
        config = build_runtime_config(self.base_config, spec)
        provider = self.provider_factory(config)
        bus = MessageBus()
        cron_service = self._build_cron_service(spec)
        session_manager = SoulSessionManager(config.workspace_path)
        agent_loop = SoulAgentLoop(
            soul_id=spec.soul_id,
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            context_window_tokens=config.agents.defaults.context_window_tokens,
            web_search_config=config.tools.web.search,
            web_proxy=config.tools.web.proxy or None,
            exec_config=config.tools.exec,
            cron_service=cron_service,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
        )
        channel_manager = ChannelManager(config, bus)
        running = _RunningSoul(
            spec=spec,
            config=config,
            provider=provider,
            bus=bus,
            session_manager=session_manager,
            cron_service=cron_service,
            agent_loop=agent_loop,
            channel_manager=channel_manager,
        )
        async def _on_cron_job(job: CronJob) -> str | None:
            session_key = cron_service.get_session_key(job.id) or f"{job.payload.channel or 'cli'}:{job.payload.to or 'direct'}"
            delivery_metadata = cron_service.get_delivery_metadata(job.id)
            channel = job.payload.channel or "cli"
            chat_id = job.payload.to or "direct"
            fired_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
            cron_content = (
                f"System: cron job {job.name!r} fired. "
                f"Job message: {job.payload.message!r}. "
                f"Datetime: {fired_at}"
            )
            cron_tool = running.agent_loop.tools.get("cron")
            cron_token = None
            if isinstance(cron_tool, CronTool):
                cron_token = cron_tool.set_cron_context(True)
            try:
                await running.bus.publish_inbound(
                    InboundMessage(
                        channel="system",
                        sender_id="cron",
                        chat_id=f"{channel}:{chat_id}",
                        content=cron_content,
                        metadata={
                            "_system_session_key": session_key,
                            **delivery_metadata,
                        },
                    )
                )
            finally:
                if isinstance(cron_tool, CronTool) and cron_token is not None:
                    cron_tool.reset_cron_context(cron_token)
            return None
        cron_service.on_job = _on_cron_job
        return running

    async def start_soul(self, soul_id: str) -> SoulAgentLoop:
        """Create and start a soul runtime, returning its agent loop."""
        running = self._running_souls.get(soul_id)
        if running is None:
            running = self._build_running_soul(soul_id)
            self._running_souls[soul_id] = running
        if not running.cron_started:
            await running.cron_service.start()
            running.cron_started = True
        if not running.agent_task or running.agent_task.done():
            running.agent_task = asyncio.create_task(running.agent_loop.run())
        if running.channel_manager.enabled_channels and (
            not running.channels_task or running.channels_task.done()
        ):
            running.channels_task = asyncio.create_task(running.channel_manager.start_all())
        return running.agent_loop

    async def start_autostart_souls(self) -> list[SoulAgentLoop]:
        """Start all souls marked autostart."""
        loops: list[SoulAgentLoop] = []
        for spec in self.list_specs():
            if spec.overrides.autostart:
                loops.append(await self.start_soul(spec.soul_id))
        return loops

    async def stop_soul(self, soul_id: str) -> None:
        """Stop a running soul if present."""
        running = self._running_souls.pop(soul_id, None)
        if running is None:
            return
        running.agent_loop.stop()
        await running.agent_loop.close_mcp()
        if running.agent_task:
            await asyncio.gather(running.agent_task, return_exceptions=True)
        if running.channels_task:
            running.channels_task.cancel()
            await asyncio.gather(running.channels_task, return_exceptions=True)
        await running.channel_manager.stop_all()
        if running.cron_started:
            running.cron_service.stop()
            running.cron_started = False

    async def stop_all(self) -> None:
        """Stop all running souls."""
        for soul_id in list(self._running_souls):
            await self.stop_soul(soul_id)
