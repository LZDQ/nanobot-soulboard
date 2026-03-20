"""Single-process runtime management for nanobot-soulboard."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.config.loader import save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import SessionManager

from nanobot_soulboard.config import (
    SoulOverrides,
    SoulboardConfig,
    get_soulboard_config_path,
    get_souls_root,
    save_soulboard_config,
    validate_soul_id,
)
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
        super().__init__(*args, **kwargs)
        self.context = SoulboardContextBuilder(self.workspace, soul_id=soul_id)


@dataclass
class _RunningSoul:
    """Internal supervisor-owned state for one started soul."""

    spec: SoulSpec
    config: Config
    provider: LLMProvider
    bus: MessageBus
    session_manager: SessionManager
    agent_loop: SoulAgentLoop
    channel_manager: ChannelManager
    agent_task: asyncio.Task | None = None
    channels_task: asyncio.Task | None = None


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

    def _build_running_soul(self, soul_id: str) -> _RunningSoul:
        """Construct supervisor-owned runtime state without starting it."""
        if self.provider_factory is None:
            raise RuntimeError("SoulSupervisor requires a provider_factory to build runtimes")
        spec = self.get_spec(soul_id)
        config = build_runtime_config(self.base_config, spec)
        provider = self.provider_factory(config)
        bus = MessageBus()
        session_manager = SessionManager(config.workspace_path)
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
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
        )
        channel_manager = ChannelManager(config, bus)
        return _RunningSoul(
            spec=spec,
            config=config,
            provider=provider,
            bus=bus,
            session_manager=session_manager,
            agent_loop=agent_loop,
            channel_manager=channel_manager,
        )

    async def start_soul(self, soul_id: str) -> SoulAgentLoop:
        """Create and start a soul runtime, returning its agent loop."""
        running = self._running_souls.get(soul_id)
        if running is None:
            running = self._build_running_soul(soul_id)
            self._running_souls[soul_id] = running
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

    async def stop_all(self) -> None:
        """Stop all running souls."""
        for soul_id in list(self._running_souls):
            await self.stop_soul(soul_id)
