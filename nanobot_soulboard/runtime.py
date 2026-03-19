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
from nanobot.config.schema import Config
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import SessionManager

from nanobot_soulboard.config import SoulOverrides, SoulboardConfig, discover_soul_ids, get_souls_root
from nanobot_soulboard.context import SoulboardContextBuilder


@dataclass(frozen=True)
class SoulSpec:
    """Resolved soul runtime spec."""

    soul_id: str
    workspace: Path
    overrides: SoulOverrides

    @property
    def enabled_channels(self) -> list[str]:
        return list(self.overrides.channels)

    @property
    def enabled_mcp_servers(self) -> list[str]:
        return list(self.overrides.mcp_servers)


def discover_soul_specs(root: Path | None = None, config: SoulboardConfig | None = None) -> list[SoulSpec]:
    """Resolve souls from config and on-disk directories."""
    config = config or SoulboardConfig()
    souls_root = get_souls_root(root)
    specs: list[SoulSpec] = []
    for soul_id in discover_soul_ids(root=root, config=config):
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
    _apply_channel_selection(config, spec.enabled_channels)
    _apply_mcp_selection(config, spec.enabled_mcp_servers)
    return config


class SoulAgentLoop(AgentLoop):
    """AgentLoop wrapper that swaps in the soulboard context builder."""

    def __init__(self, *args, soul_id: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.context = SoulboardContextBuilder(self.workspace, soul_id=soul_id)


class SoulRuntime:
    """One in-memory soul runtime using upstream primitives."""

    def __init__(
        self,
        spec: SoulSpec,
        config: Config,
        provider: LLMProvider,
    ):
        self.spec = spec
        self.config = config
        self.provider = provider
        self.bus = MessageBus()
        self.session_manager = SessionManager(config.workspace_path)
        self.agent_loop = SoulAgentLoop(
            soul_id=spec.soul_id,
            bus=self.bus,
            provider=self.provider,
            workspace=config.workspace_path,
            model=config.agents.defaults.model,
            max_iterations=config.agents.defaults.max_tool_iterations,
            context_window_tokens=config.agents.defaults.context_window_tokens,
            web_search_config=config.tools.web.search,
            web_proxy=config.tools.web.proxy or None,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=self.session_manager,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
        )
        self.channel_manager = ChannelManager(config, self.bus)
        self._agent_task: asyncio.Task | None = None
        self._channels_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the upstream agent loop and channel manager."""
        if self._agent_task and not self._agent_task.done():
            return
        self._agent_task = asyncio.create_task(self.agent_loop.run())
        if self.channel_manager.enabled_channels:
            self._channels_task = asyncio.create_task(self.channel_manager.start_all())

    async def stop(self) -> None:
        """Stop the runtime and release upstream resources."""
        self.agent_loop.stop()
        await self.agent_loop.close_mcp()
        if self._agent_task:
            await asyncio.gather(self._agent_task, return_exceptions=True)
            self._agent_task = None
        if self._channels_task:
            self._channels_task.cancel()
            await asyncio.gather(self._channels_task, return_exceptions=True)
            self._channels_task = None
        await self.channel_manager.stop_all()

    async def process_direct(
        self,
        content: str,
        session_key: str = "web:direct",
        channel: str = "web",
        chat_id: str = "direct",
    ) -> str:
        """Send a direct request to the soul runtime."""
        return await self.agent_loop.process_direct(
            content,
            session_key=session_key,
            channel=channel,
            chat_id=chat_id,
        )

    def status(self) -> dict[str, object]:
        """Return a small runtime status snapshot."""
        return {
            "soul_id": self.spec.soul_id,
            "workspace": str(self.config.workspace_path),
            "running": bool(self._agent_task and not self._agent_task.done()),
            "model": self.config.agents.defaults.model,
            "provider": self.config.agents.defaults.provider,
            "channels": self.channel_manager.enabled_channels,
            "mcp_servers": sorted(self.config.tools.mcp_servers),
        }


class SoulSupervisor:
    """Create and track in-memory soul runtimes."""

    def __init__(
        self,
        base_config: Config,
        soulboard_config: SoulboardConfig | None = None,
        root: Path | None = None,
        provider_factory: Callable[[Config], LLMProvider] | None = None,
    ):
        self.base_config = base_config
        self.soulboard_config = soulboard_config or SoulboardConfig()
        self.root = root
        self.provider_factory = provider_factory
        self._runtimes: dict[str, SoulRuntime] = {}

    def list_specs(self) -> list[SoulSpec]:
        """List all resolved soul specs."""
        return discover_soul_specs(root=self.root, config=self.soulboard_config)

    def get_spec(self, soul_id: str) -> SoulSpec:
        """Return one soul spec or raise KeyError."""
        for spec in self.list_specs():
            if spec.soul_id == soul_id:
                return spec
        raise KeyError(f"Unknown soul: {soul_id}")

    def build_runtime(self, soul_id: str) -> SoulRuntime:
        """Construct a runtime without starting it."""
        if self.provider_factory is None:
            raise RuntimeError("SoulSupervisor requires a provider_factory to build runtimes")
        spec = self.get_spec(soul_id)
        runtime_config = build_runtime_config(self.base_config, spec)
        provider = self.provider_factory(runtime_config)
        return SoulRuntime(spec=spec, config=runtime_config, provider=provider)

    async def start_soul(self, soul_id: str) -> SoulRuntime:
        """Create and start a soul runtime."""
        runtime = self._runtimes.get(soul_id)
        if runtime is None:
            runtime = self.build_runtime(soul_id)
            self._runtimes[soul_id] = runtime
        await runtime.start()
        return runtime

    async def stop_soul(self, soul_id: str) -> None:
        """Stop a running soul if present."""
        runtime = self._runtimes.pop(soul_id, None)
        if runtime is not None:
            await runtime.stop()

    def get_runtime(self, soul_id: str) -> SoulRuntime | None:
        """Return an existing runtime if started."""
        return self._runtimes.get(soul_id)

    def status(self) -> list[dict[str, object]]:
        """Return status for discovered souls."""
        rows = []
        for spec in self.list_specs():
            runtime = self._runtimes.get(spec.soul_id)
            if runtime is None:
                rows.append({
                    "soul_id": spec.soul_id,
                    "workspace": str(spec.workspace),
                    "running": False,
                    "model": spec.overrides.model or self.base_config.agents.defaults.model,
                    "provider": spec.overrides.provider or self.base_config.agents.defaults.provider,
                    "channels": spec.enabled_channels,
                    "mcp_servers": spec.enabled_mcp_servers,
                })
            else:
                rows.append(runtime.status())
        return rows
