"""Soulboard runtime supervision and per-soul config assembly."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from nanobot.agent.tools.cron import CronTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.cron.types import CronJob
from nanobot.providers.base import LLMProvider
from nanobot.providers.registry import find_by_name
from nanobot.session.manager import SessionManager

from nanobot_soulboard.agent.loop import SoulAgentLoop
from nanobot_soulboard.config import (
    SoulOverrides,
    SoulboardConfig,
    get_soulboard_config_path,
    get_souls_root,
    load_soulboard_config,
    save_soulboard_config,
    validate_soul_id,
)
from nanobot_soulboard.cron import SoulCronService

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


def _validate_mcp_http_header_overrides(
    available_servers: dict[str, MCPServerConfig],
    overrides: SoulOverrides,
) -> None:
    if not overrides.mcp_http_headers:
        return

    unknown = sorted(name for name in overrides.mcp_http_headers if name not in available_servers)
    if unknown:
        unknown_str = ", ".join(unknown)
        raise ValueError(f"Unknown MCP server(s) in soulboard MCP header overrides: {unknown_str}")

    selected_servers = set(overrides.mcp_servers)
    unselected = sorted(name for name in overrides.mcp_http_headers if name not in selected_servers)
    if unselected:
        unselected_str = ", ".join(unselected)
        raise ValueError(
            f"MCP header overrides require the server to be enabled for this soul: {unselected_str}"
        )

    for name in overrides.mcp_http_headers:
        server = available_servers[name]
        if server.type == "stdio" or (server.type is None and not server.url):
            raise ValueError(
                f"MCP header overrides are only supported for HTTP MCP servers: {name}"
            )


def _apply_mcp_http_header_overrides(config: Config, overrides: SoulOverrides) -> None:
    if not overrides.mcp_http_headers:
        return

    for name, header_overrides in overrides.mcp_http_headers.items():
        server = config.tools.mcp_servers[name]
        config.tools.mcp_servers[name] = server.model_copy(
            update={"headers": {**server.headers, **header_overrides}}
        )


def build_runtime_config(base_config: Config, spec: SoulSpec) -> Config:
    """Build an in-memory nanobot config for one soul runtime."""
    config = _copy_config(base_config)
    config.agents.defaults.workspace = str(spec.workspace)
    if spec.overrides.model:
        config.agents.defaults.model = spec.overrides.model
    if spec.overrides.provider:
        config.agents.defaults.provider = spec.overrides.provider
    _apply_channel_selection(config, list(spec.overrides.channels))
    _validate_mcp_http_header_overrides(base_config.tools.mcp_servers, spec.overrides)
    _apply_mcp_selection(config, list(spec.overrides.mcp_servers))
    _apply_mcp_http_header_overrides(config, spec.overrides)
    return config


@dataclass
class RunningSoul:
    """Internal supervisor-owned state for one started soul."""

    spec: SoulSpec
    config: Config
    provider: LLMProvider
    bus: MessageBus
    session_manager: SessionManager
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
        self._running_souls: dict[str, RunningSoul] = {}

    def list_specs(self) -> list[SoulSpec]:
        """List all resolved soul specs."""
        specs = {
            spec.soul_id: spec
            for spec in discover_soul_specs(nano_root=self.nano_root, config=self.soulboard_config)
        }
        for soul_id, running in self._running_souls.items():
            specs.setdefault(soul_id, running.spec)
        return sorted(specs.values(), key=lambda spec: spec.soul_id)

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
        if not overrides.mcp_servers and not overrides.mcp_http_headers:
            return
        known = set(self.base_config.tools.mcp_servers)
        filtered = [name for name in overrides.mcp_servers if name in known]
        filtered_headers = {
            name: headers
            for name, headers in overrides.mcp_http_headers.items()
            if name in known and name in filtered
        }
        if filtered == overrides.mcp_servers and filtered_headers == overrides.mcp_http_headers:
            return
        self.soulboard_config.souls[soul_id] = overrides.model_copy(
            update={
                "mcp_servers": filtered,
                "mcp_http_headers": filtered_headers,
            }
        )
        save_soulboard_config(self.soulboard_config, self.config_path)

    def reload_config(self) -> None:
        """Reload persisted configs without disturbing running souls."""
        self.soulboard_config = load_soulboard_config(self.config_path)
        if self.base_config_path is not None:
            self.base_config = load_config(self.base_config_path)
        dirty = False
        for soul_id in list(self.soulboard_config.souls):
            overrides = self.soulboard_config.souls[soul_id]
            if not overrides.mcp_servers and not overrides.mcp_http_headers:
                continue
            known = set(self.base_config.tools.mcp_servers)
            filtered = [name for name in overrides.mcp_servers if name in known]
            filtered_headers = {
                name: headers
                for name, headers in overrides.mcp_http_headers.items()
                if name in known and name in filtered
            }
            if filtered == overrides.mcp_servers and filtered_headers == overrides.mcp_http_headers:
                continue
            self.soulboard_config.souls[soul_id] = overrides.model_copy(
                update={
                    "mcp_servers": filtered,
                    "mcp_http_headers": filtered_headers,
                }
            )
            dirty = True
        if dirty:
            save_soulboard_config(self.soulboard_config, self.config_path)

    def list_app_links(self) -> list[str]:
        """Return configured top-bar app links."""
        return list(self.soulboard_config.app_links)

    def update_app_links(self, items: list[str]) -> list[str]:
        """Replace configured top-bar app links."""
        self.soulboard_config = self.soulboard_config.model_copy(update={"app_links": items})
        save_soulboard_config(self.soulboard_config, self.config_path)
        return list(self.soulboard_config.app_links)

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
        _validate_mcp_http_header_overrides(self.base_config.tools.mcp_servers, overrides)
        self.soulboard_config.souls[soul_id] = overrides
        save_soulboard_config(self.soulboard_config, self.config_path)

    def create_soul(self, soul_id: str, overrides: SoulOverrides | None = None) -> SoulSpec:
        """Create and persist a new soul definition."""
        validate_soul_id(soul_id)
        if soul_id in self.soulboard_config.souls:
            raise ValueError(f"Soul already exists: {soul_id}")
        resolved_overrides = overrides or SoulOverrides()
        _validate_mcp_http_header_overrides(self.base_config.tools.mcp_servers, resolved_overrides)
        self.soulboard_config.souls[soul_id] = resolved_overrides
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

    def _build_running_soul(self, soul_id: str) -> RunningSoul:
        """Construct supervisor-owned runtime state without starting it."""
        if self.provider_factory is None:
            raise RuntimeError("SoulSupervisor requires a provider_factory to build runtimes")
        self._prune_missing_mcp_servers(soul_id)
        spec = self.get_spec(soul_id)
        config = build_runtime_config(self.base_config, spec)
        provider = self.provider_factory(config)
        bus = MessageBus()
        cron_service = self._build_cron_service(spec)
        session_manager = SessionManager(config.workspace_path)
        agent_loop = SoulAgentLoop(
            soul_id=spec.soul_id,
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            context_window_tokens=config.agents.defaults.context_window_tokens,
            web_config=config.tools.web,
            exec_config=config.tools.exec,
            cron_service=cron_service,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
            timezone=config.agents.defaults.timezone,
        )
        channel_manager = ChannelManager(config, bus)
        running = RunningSoul(
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
        current_loop = asyncio.get_running_loop()
        if running.agent_task and running.agent_task.get_loop() is current_loop and not running.agent_task.get_loop().is_closed():
            await asyncio.gather(running.agent_task, return_exceptions=True)
        if running.channels_task:
            running.channels_task.cancel()
            if running.channels_task.get_loop() is current_loop and not running.channels_task.get_loop().is_closed():
                await asyncio.gather(running.channels_task, return_exceptions=True)
        await running.channel_manager.stop_all()
        if running.cron_started:
            running.cron_service.stop()
            running.cron_started = False

    async def stop_all(self) -> None:
        """Stop all running souls."""
        for soul_id in list(self._running_souls):
            await self.stop_soul(soul_id)
