"""Soulboard runtime supervision and per-soul config assembly."""

import asyncio
import shutil
import tempfile
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.self import MyTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config, MCPServerConfig
from nanobot.cron.types import CronJob, CronSchedule
from nanobot.providers.base import LLMProvider
from nanobot.providers.image_generation import image_gen_provider_configs
from nanobot.session.manager import SessionManager

from nanobot_soulboard.agent.loop import SoulAgentLoop, SoulMcpReconnectRequest
from nanobot_soulboard.agent.shell import SoulExecTool
from nanobot_soulboard.config import (
    CronJobRegistryEntry,
    SoulOverrides,
    SoulboardConfig,
    get_base_config_path,
    get_soul_config_path,
    get_soulboard_config_path,
    get_souls_root,
    load_soul_config,
    load_soulboard_config,
    normalize_tool_names,
    save_soul_config,
    save_soulboard_config,
    validate_soul_id,
)
from nanobot_soulboard.cron import SoulCronPayload, SoulCronService, SoulCronTool
from nanobot_soulboard.skills import DiscoveredSkill, discover_skills_in_pool

SOUL_PROMPT_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "SYSTEM.md")


@dataclass(frozen=True)
class SoulSpec:
    """Resolved soul runtime spec."""

    soul_id: str
    workspace: Path
    overrides: SoulOverrides


@dataclass(frozen=True)
class ToolCatalogItem:
    """One nanobot tool available for soulboard policy configuration."""

    name: str
    description: str


@dataclass(frozen=True)
class SoulCloneCronJob:
    """One edited cron job to materialize in a cloned soul."""

    name: str
    enabled: bool
    schedule: CronSchedule
    message: str
    deliver: bool
    channel: str | None
    chat_id: str | None
    session_key: str | None
    recurring_session_key_format: str | None
    delete_after_run: bool


def discover_soul_specs(nano_root: Path) -> list[SoulSpec]:
    """Discover valid workspace-local soul configs under souls/."""
    souls_root = get_souls_root(nano_root)
    specs: list[SoulSpec] = []
    if not souls_root.is_dir():
        return specs
    for workspace in sorted(souls_root.iterdir(), key=lambda path: path.name):
        if not workspace.is_dir():
            continue
        soul_id = workspace.name
        try:
            validate_soul_id(soul_id)
            overrides = load_soul_config(workspace / "config.json")
        except (OSError, ValueError) as exc:
            logger.warning("Skipping invalid soul directory '{}': {}", workspace, exc)
            continue
        specs.append(SoulSpec(soul_id=soul_id, workspace=workspace, overrides=overrides))
    return specs


def _copy_config(config: Config) -> Config:
    """Clone a nanobot config for per-soul in-memory overrides."""
    return Config.model_validate(deepcopy(config.model_dump(by_alias=True)))


def _effective_disabled_tools(
    global_disabled_tools: list[str],
    overrides: SoulOverrides,
) -> list[str]:
    """Resolve the removal-only tool policy for one soul."""
    disabled = set(global_disabled_tools) - set(overrides.enabled_tools)
    disabled.update(overrides.disabled_tools)
    return sorted(disabled)


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
    mcp_owner_task: asyncio.Task | None = None
    mcp_shutdown: asyncio.Event | None = None
    mcp_connect_requested: asyncio.Event | None = None
    mcp_reconnect_requests: asyncio.Queue[SoulMcpReconnectRequest] | None = None
    cron_started: bool = False


def _current_task_is_cancelling() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def _connect_mcp_from_owner(agent_loop: SoulAgentLoop, ready: asyncio.Future) -> None:
    try:
        await agent_loop.connect_mcp_from_owner()
    except asyncio.CancelledError as e:
        if _current_task_is_cancelling():
            if not ready.done():
                ready.set_exception(e)
            raise
        logger.warning("Soul '{}' MCP owner connect was cancelled by SDK", agent_loop.soul_id)
        if not ready.done():
            ready.set_result(None)
    except BaseException as e:
        if not ready.done():
            ready.set_exception(e)
        else:
            logger.warning("Soul '{}' MCP owner connect failed: {}", agent_loop.soul_id, e)
    else:
        if not ready.done():
            ready.set_result(None)


async def _reconnect_mcp_from_owner(
    agent_loop: SoulAgentLoop,
    request: SoulMcpReconnectRequest,
) -> None:
    if request.future.done():
        return
    try:
        tool = await agent_loop.reconnect_mcp_server_from_owner(
            request.server_name,
            request.tool_name,
        )
    except asyncio.CancelledError:
        if _current_task_is_cancelling():
            if not request.future.done():
                request.future.set_result(None)
            raise
        logger.warning(
            "Soul '{}' MCP owner reconnect for server '{}' was cancelled by SDK",
            agent_loop.soul_id,
            request.server_name,
        )
        if not request.future.done():
            request.future.set_result(None)
        return
    except BaseException as e:
        logger.warning(
            "Soul '{}' MCP owner reconnect failed for server '{}': {}",
            agent_loop.soul_id,
            request.server_name,
            e,
        )
        if not request.future.done():
            request.future.set_result(None)
        return
    if not request.future.done():
        request.future.set_result(tool)


async def _wait_for_mcp_owner_request(
    connect_requested: asyncio.Event,
    reconnect_requests: asyncio.Queue[SoulMcpReconnectRequest],
    shutdown: asyncio.Event,
) -> tuple[str, SoulMcpReconnectRequest | None]:
    connect_task = asyncio.create_task(connect_requested.wait())
    reconnect_task = asyncio.create_task(reconnect_requests.get())
    shutdown_task = asyncio.create_task(shutdown.wait())
    tasks = {connect_task, reconnect_task, shutdown_task}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if shutdown_task in done and shutdown.is_set():
            return "shutdown", None
        if reconnect_task in done:
            return "reconnect", reconnect_task.result()
        return "connect", None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _drain_mcp_reconnect_requests(
    reconnect_requests: asyncio.Queue[SoulMcpReconnectRequest],
) -> None:
    while True:
        try:
            request = reconnect_requests.get_nowait()
        except asyncio.QueueEmpty:
            return
        if not request.future.done():
            request.future.set_result(None)


async def _own_mcp_lifecycle(
    agent_loop: SoulAgentLoop,
    ready: asyncio.Future,
    shutdown: asyncio.Event,
    connect_requested: asyncio.Event,
    reconnect_requests: asyncio.Queue[SoulMcpReconnectRequest],
) -> None:
    """Long-lived task that owns this soul's MCP cancel scopes.

    nanobot's streamable_http_client opens an anyio CancelScope in whatever
    task calls connect. If we let agent_task own that scope (via run() ->
    _connect_mcp), a transport-side cancel cascade (peer drops the connection)
    delivers cancellation forever via loop.call_soon — agent_task is too far
    inside its message loop to ever exit the scope. Same task must enter and
    exit, so this task does both. See HKUDS/nanobot#3638.
    """
    try:
        await _connect_mcp_from_owner(agent_loop, ready)
        while not shutdown.is_set():
            request_kind, request = await _wait_for_mcp_owner_request(
                connect_requested,
                reconnect_requests,
                shutdown,
            )
            if request_kind == "shutdown":
                break
            if request_kind == "reconnect" and request is not None:
                await _reconnect_mcp_from_owner(agent_loop, request)
                continue
            connect_requested.clear()
            await _connect_mcp_from_owner(agent_loop, ready)
    except asyncio.CancelledError:
        pass
    finally:
        _drain_mcp_reconnect_requests(reconnect_requests)
    try:
        await agent_loop.close_mcp()
    except (RuntimeError, BaseExceptionGroup):
        pass


class SoulSupervisor:
    """Create and track in-memory soul runtimes."""

    def __init__(
        self,
        base_config: Config,
        nano_root: Path,
        soulboard_config: SoulboardConfig | None = None,
        provider_factory: Callable[[Config], LLMProvider] | None = None,
    ):
        self.base_config = base_config
        self.soulboard_config = soulboard_config or SoulboardConfig()
        self.nano_root = nano_root
        self.config_path = get_soulboard_config_path(nano_root)
        self.base_config_path = get_base_config_path(nano_root)
        self.provider_factory = provider_factory
        self._running_souls: dict[str, RunningSoul] = {}
        self._soul_specs: dict[str, SoulSpec] = {}
        self._skill_pool_cache: dict[str, list[DiscoveredSkill]] = {}
        self._skill_pools_loaded: bool = False
        self._reload_soul_specs()

    def _reload_soul_specs(self) -> None:
        self._soul_specs = {
            spec.soul_id: spec for spec in discover_soul_specs(self.nano_root)
        }

    def list_specs(self) -> list[SoulSpec]:
        """List all resolved soul specs."""
        specs = dict(self._soul_specs)
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
        spec = self._soul_specs.get(soul_id)
        if spec is None:
            raise KeyError(f"Unknown soul: {soul_id}")
        overrides = spec.overrides
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
        updated = overrides.model_copy(
            update={
                "mcp_servers": filtered,
                "mcp_http_headers": filtered_headers,
            }
        )
        save_soul_config(updated, get_soul_config_path(self.nano_root, soul_id))
        self._soul_specs[soul_id] = SoulSpec(
            soul_id=soul_id,
            workspace=spec.workspace,
            overrides=updated,
        )

    def reload_config(self) -> None:
        """Reload persisted configs without disturbing running souls."""
        self.soulboard_config = load_soulboard_config(self.config_path)
        try:
            self.base_config = load_config(self.base_config_path)
        except ValueError as exc:
            logger.warning(
                "Reload skipped base config {}: {}. Keeping previous config.",
                self.base_config_path,
                exc,
            )
        self._reload_soul_specs()
        self.refresh_skill_pools()
        for soul_id in list(self._soul_specs):
            self._prune_missing_mcp_servers(soul_id)

    def list_skill_pools(self) -> list[str]:
        """Return the configured global skill pool paths."""
        return list(self.soulboard_config.skill_registry)

    def update_skill_pools(self, items: list[str]) -> list[str]:
        """Replace the configured global skill pool paths and reload the cache."""
        self.soulboard_config = self.soulboard_config.model_copy(update={"skill_registry": items})
        save_soulboard_config(self.soulboard_config, self.config_path)
        self.refresh_skill_pools()
        return list(self.soulboard_config.skill_registry)

    def refresh_skill_pools(self) -> dict[str, list[DiscoveredSkill]]:
        """Walk every configured pool and rebuild the in-memory skill cache."""
        cache: dict[str, list[DiscoveredSkill]] = {}
        for raw_path in self.soulboard_config.skill_registry:
            pool_root = Path(raw_path).expanduser()
            if not pool_root.is_dir():
                logger.warning("Skill pool path is not a directory: {}", raw_path)
                cache[raw_path] = []
                continue
            cache[raw_path] = discover_skills_in_pool(raw_path, pool_root)
        self._skill_pool_cache = cache
        self._skill_pools_loaded = True
        return cache

    def get_skill_pools(self) -> dict[str, list[DiscoveredSkill]]:
        """Return cached pool→skills, populating the cache on first access."""
        if not self._skill_pools_loaded:
            self.refresh_skill_pools()
        return self._skill_pool_cache

    def resolve_skill_in_pools(self, skill_path: str) -> DiscoveredSkill:
        """Look up a skill in the cached pools by its absolute skill directory path."""
        target = Path(skill_path).expanduser().resolve()
        for entries in self.get_skill_pools().values():
            for entry in entries:
                try:
                    if entry.skill_dir.resolve() == target:
                        return entry
                except OSError:
                    continue
        raise ValueError(f"Unknown skill: {skill_path}")

    def add_soul_skill_from_pools(
        self,
        soul_id: str,
        skill_path: str,
        target_name: str | None = None,
        mode: Literal["symlink", "copy"] = "symlink",
    ) -> Path:
        """Materialize a pool-resident skill into a soul's workspace skills/ dir.

        Returns the resulting skill directory path inside the soul workspace.
        """
        if mode not in ("symlink", "copy"):
            raise ValueError(f"Unknown skill add mode: {mode}")
        spec = self.get_spec(soul_id)
        skill = self.resolve_skill_in_pools(skill_path)
        name = (target_name or skill.skill_dir.name).strip()
        if not name or "/" in name or name in (".", ".."):
            raise ValueError(f"Invalid target skill name: {name!r}")
        soul_skills_root = spec.workspace / "skills"
        soul_skills_root.mkdir(parents=True, exist_ok=True)
        target_path = soul_skills_root / name
        if target_path.exists() or target_path.is_symlink():
            raise ValueError(f"Skill already exists in soul workspace: {name}")
        if mode == "symlink":
            target_path.symlink_to(skill.skill_dir)
        else:
            shutil.copytree(skill.skill_dir, target_path, symlinks=False)
        return target_path

    def delete_soul_skill(self, soul_id: str, name: str) -> None:
        """Remove a skill from a soul's workspace skills/ directory.

        For symlinks, only the link is removed (registry source is untouched).
        For directories, the entire skill directory is removed.
        """
        if not name or "/" in name or name in (".", ".."):
            raise ValueError(f"Invalid skill name: {name!r}")
        spec = self.get_spec(soul_id)
        target_path = spec.workspace / "skills" / name
        if target_path.is_symlink():
            target_path.unlink()
            return
        if not target_path.exists():
            raise KeyError(f"Skill not found in soul workspace: {name}")
        if not target_path.is_dir():
            raise ValueError(f"Skill path is not a directory or symlink: {name}")
        shutil.rmtree(target_path)

    def list_cron_job_registry(self) -> list[CronJobRegistryEntry]:
        """Return the configured global cron job registry entries."""
        return list(self.soulboard_config.cron_job_registry)

    def update_cron_job_registry(
        self, entries: list[CronJobRegistryEntry]
    ) -> list[CronJobRegistryEntry]:
        """Replace the global cron job registry and persist."""
        self.soulboard_config = self.soulboard_config.model_copy(
            update={"cron_job_registry": entries}
        )
        save_soulboard_config(self.soulboard_config, self.config_path)
        return list(self.soulboard_config.cron_job_registry)

    def add_cron_jobs_to_soul_from_registry(
        self,
        soul_id: str,
        names: list[str],
    ) -> list[CronJob]:
        """Schedule selected registry entries as cron jobs in a soul.

        Works whether the soul is running or stopped.
        """
        registry_by_name = {e.name: e for e in self.soulboard_config.cron_job_registry}
        spec = self.get_spec(soul_id)
        running = self._running_souls.get(soul_id)
        cron_service = (
            running.cron_service if running is not None else self._build_cron_service(spec)
        )
        added: list[CronJob] = []
        for name in names:
            entry = registry_by_name.get(name)
            if entry is None:
                raise ValueError(f"Unknown cron job registry entry: {name!r}")
            if entry.cron_expr:
                schedule = CronSchedule(kind="cron", expr=entry.cron_expr, tz=entry.tz)
            elif entry.every_seconds:
                schedule = CronSchedule(kind="every", every_ms=entry.every_seconds * 1000)
            else:
                raise ValueError(
                    f"Cron job registry entry {name!r} has no schedule "
                    "(set cron_expr or every_seconds)"
                )
            job = cron_service.add_job(
                name=entry.label or entry.name,
                schedule=schedule,
                message=entry.message,
                deliver=entry.deliver,
                channel=entry.channel,
                to=entry.chat_id,
                session_key=entry.session_key,
                recurring_session_key_format=entry.recurring_session_key_format,
            )
            added.append(job)
        return added

    def add_cron_job_to_soul(
        self,
        soul_id: str,
        *,
        name: str,
        schedule: CronSchedule,
        message: str = "",
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        session_key: str | None = None,
        recurring_session_key_format: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """Manually schedule one cron job in a soul, without going through the registry."""
        spec = self.get_spec(soul_id)
        running = self._running_souls.get(soul_id)
        cron_service = (
            running.cron_service if running is not None else self._build_cron_service(spec)
        )
        return cron_service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            to=to,
            session_key=session_key,
            recurring_session_key_format=recurring_session_key_format,
            delete_after_run=delete_after_run,
        )

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
        spec = self._soul_specs.get(soul_id)
        if spec is None:
            raise KeyError(f"Unknown soul: {soul_id}")
        save_soul_config(overrides, get_soul_config_path(self.nano_root, soul_id))
        self._soul_specs[soul_id] = SoulSpec(
            soul_id=soul_id,
            workspace=spec.workspace,
            overrides=overrides,
        )

    def create_soul(
        self,
        soul_id: str,
        overrides: SoulOverrides | None = None,
    ) -> SoulSpec:
        """Create and persist a new soul definition."""
        validate_soul_id(soul_id)
        workspace = get_souls_root(self.nano_root) / soul_id
        if workspace.exists() or workspace.is_symlink():
            raise ValueError(f"Soul already exists: {soul_id}")
        resolved_overrides = overrides or SoulOverrides()
        _validate_mcp_http_header_overrides(self.base_config.tools.mcp_servers, resolved_overrides)
        spec = SoulSpec(
            soul_id=soul_id,
            workspace=workspace,
            overrides=resolved_overrides,
        )
        save_soul_config(resolved_overrides, workspace / "config.json")
        self._soul_specs[soul_id] = spec
        return spec

    def clone_soul(
        self,
        source_soul_id: str,
        soul_id: str,
        overrides: SoulOverrides,
        *,
        prompt_files: dict[str, str],
        skill_names: list[str],
        cron_jobs: list[SoulCloneCronJob],
    ) -> SoulSpec:
        """Clone selected workspace content while always clearing memory and sessions."""
        source = self.get_spec(source_soul_id)
        validate_soul_id(soul_id)
        _validate_mcp_http_header_overrides(self.base_config.tools.mcp_servers, overrides)
        unknown_prompt_files = sorted(set(prompt_files) - set(SOUL_PROMPT_FILES))
        if unknown_prompt_files:
            unknown_str = ", ".join(unknown_prompt_files)
            raise ValueError(f"Unknown prompt file(s): {unknown_str}")

        normalized_skill_names: list[str] = []
        seen_skill_names: set[str] = set()
        for name in skill_names:
            if not name or "/" in name or name in (".", ".."):
                raise ValueError(f"Invalid skill name: {name!r}")
            if name not in seen_skill_names:
                normalized_skill_names.append(name)
                seen_skill_names.add(name)

        souls_root = get_souls_root(self.nano_root)
        target = souls_root / soul_id
        if target.exists() or target.is_symlink():
            raise ValueError(f"Soul already exists: {soul_id}")

        souls_root.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".clone-{soul_id}-",
                dir=souls_root.parent,
            )
        )
        try:
            save_soul_config(overrides, staging / "config.json")
            for filename, content in prompt_files.items():
                (staging / filename).write_text(content, encoding="utf-8")

            if normalized_skill_names:
                target_skills_root = staging / "skills"
                target_skills_root.mkdir(parents=True, exist_ok=True)
                for name in normalized_skill_names:
                    source_path = source.workspace / "skills" / name
                    if not (source_path.exists() or source_path.is_symlink()):
                        raise ValueError(f"Skill not found in source soul: {name}")
                    self._copy_workspace_entry(source_path, target_skills_root / name)

            if cron_jobs:
                cron_service = SoulCronService(
                    staging / "cron" / "jobs.json",
                    soul_id=soul_id,
                )
                for cron_job in cron_jobs:
                    created = cron_service.add_job(
                        name=cron_job.name,
                        schedule=cron_job.schedule,
                        message=cron_job.message,
                        deliver=cron_job.deliver,
                        channel=cron_job.channel,
                        to=cron_job.chat_id,
                        session_key=cron_job.session_key,
                        recurring_session_key_format=cron_job.recurring_session_key_format,
                        delete_after_run=cron_job.delete_after_run,
                    )
                    if not cron_job.enabled:
                        cron_service.enable_job(created.id, False)

            souls_root.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise ValueError(f"Soul already exists: {soul_id}")
            staging.rename(target)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise

        spec = SoulSpec(soul_id=soul_id, workspace=target, overrides=overrides)
        self._soul_specs[soul_id] = spec
        return spec

    @staticmethod
    def _copy_workspace_entry(
        source: Path,
        target: Path,
    ) -> None:
        """Copy one workspace entry while preserving its storage form."""
        if source.is_symlink():
            target.symlink_to(source.readlink(), target_is_directory=source.is_dir())
        elif source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target, follow_symlinks=False)

    def delete_soul(self, soul_id: str) -> None:
        """Delete a soul definition unless it is currently running."""
        if soul_id in self._running_souls:
            raise RuntimeError(f"Cannot delete running soul: {soul_id}")
        spec = self._soul_specs.get(soul_id)
        if spec is None:
            raise KeyError(f"Unknown soul: {soul_id}")
        del self._soul_specs[soul_id]
        if spec.workspace.exists():
            shutil.rmtree(spec.workspace)

    def list_mcp_servers(self) -> dict[str, MCPServerConfig]:
        """Return MCP server definitions from the base nanobot config."""
        return dict(sorted(self.base_config.tools.mcp_servers.items()))

    def list_enabled_channels(self) -> list[str]:
        """Return channel names enabled in the base nanobot config."""
        channel_data = self.base_config.channels.model_dump(by_alias=True)
        names: list[str] = []
        for name, value in sorted(channel_data.items()):
            if isinstance(value, dict) and value.get("enabled") is True:
                names.append(name)
        return names

    def list_nanobot_tools(self) -> list[ToolCatalogItem]:
        """Return the dynamically registered core tool names for UI controls."""
        registry = ToolRegistry()
        tools_config = self.base_config.tools
        catalog_workspace = self.nano_root / "soulboard" / ".tool-catalog"
        cron_service = SoulCronService(
            catalog_workspace / "cron" / "jobs.json",
            soul_id="tool-catalog",
        )
        ctx = ToolContext(
            config=tools_config,
            workspace=str(catalog_workspace),
            bus=MessageBus(),
            subagent_manager=object(),
            cron_service=cron_service,
            sessions=object(),
            timezone=self.base_config.agents.defaults.timezone,
        )
        ToolLoader().load(ctx, registry)

        if tools_config.my.enable:
            registry.register(
                MyTool(runtime_state=self, modify_allowed=tools_config.my.allow_set)
            )
        if tools_config.exec.enable:
            registry.unregister("exec")
            registry.register(
                SoulExecTool(
                    workspace=catalog_workspace,
                    timeout=tools_config.exec.timeout,
                    restrict_to_workspace=tools_config.restrict_to_workspace,
                    sandbox=tools_config.exec.sandbox,
                    path_append=tools_config.exec.path_append,
                    allowed_env_keys=tools_config.exec.allowed_env_keys,
                    allow_patterns=tools_config.exec.allow_patterns,
                    deny_patterns=tools_config.exec.deny_patterns,
                )
            )
        if registry.has("cron"):
            registry.unregister("cron")
            registry.register(
                SoulCronTool(
                    cron_service,
                    default_timezone=self.base_config.agents.defaults.timezone or "UTC",
                )
            )

        items: list[ToolCatalogItem] = []
        for name in registry.tool_names:
            tool = registry.get(name)
            if tool is None:
                continue
            try:
                description = tool.description
            except Exception:
                description = ""
            items.append(ToolCatalogItem(name=name, description=description))
        return sorted(items, key=lambda item: item.name)

    def get_disabled_tools(self) -> list[str]:
        """Return the global list of tools disabled for souls by default."""
        return list(self.soulboard_config.disabled_tools)

    def update_disabled_tools(self, disabled_tools: list[str]) -> list[str]:
        """Replace the global disabled tool list and persist it."""
        normalized = normalize_tool_names(disabled_tools)
        self.soulboard_config = self.soulboard_config.model_copy(
            update={"disabled_tools": normalized}
        )
        save_soulboard_config(self.soulboard_config, self.config_path)
        return self.get_disabled_tools()

    def create_mcp_server(self, name: str, definition: MCPServerConfig) -> MCPServerConfig:
        """Create one MCP server definition in the base nanobot config."""
        if name in self.base_config.tools.mcp_servers:
            raise ValueError(f"MCP server already exists: {name}")
        self.base_config.tools.mcp_servers[name] = definition
        save_config(self.base_config, self.base_config_path)
        return self.base_config.tools.mcp_servers[name]

    def update_mcp_server(self, name: str, definition: MCPServerConfig) -> MCPServerConfig:
        """Replace one MCP server definition in the base nanobot config."""
        if name not in self.base_config.tools.mcp_servers:
            raise KeyError(f"Unknown MCP server: {name}")
        self.base_config.tools.mcp_servers[name] = definition
        save_config(self.base_config, self.base_config_path)
        return self.base_config.tools.mcp_servers[name]

    def delete_mcp_server(self, name: str) -> None:
        """Delete one MCP server definition from the base nanobot config."""
        if name not in self.base_config.tools.mcp_servers:
            raise KeyError(f"Unknown MCP server: {name}")
        del self.base_config.tools.mcp_servers[name]
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

    def remove_cron_job(self, soul_id: str, job_id: str) -> Literal["removed", "protected", "not_found"]:
        """Remove a soul cron job by ID. Works whether the soul is running or stopped."""
        spec = self.get_spec(soul_id)
        running = self._running_souls.get(soul_id)
        cron_service = running.cron_service if running is not None else self._build_cron_service(spec)
        return cron_service.remove_job(job_id)

    def update_cron_job(
        self,
        soul_id: str,
        job_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        message: str | None = None,
        deliver: bool | None = None,
        channel=...,
        to=...,
        session_key=...,
        recurring_session_key_format=...,
        delete_after_run: bool | None = None,
        schedule: CronSchedule | None = None,
    ) -> CronJob | Literal["not_found", "protected"]:
        """Update mutable fields of a soul cron job. Works whether the soul is running or stopped."""
        spec = self.get_spec(soul_id)
        running = self._running_souls.get(soul_id)
        cron_service = running.cron_service if running is not None else self._build_cron_service(spec)

        result = cron_service.update_job(
            job_id,
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            to=to,
            session_key=session_key,
            recurring_session_key_format=recurring_session_key_format,
            delete_after_run=delete_after_run,
        )
        if isinstance(result, str):
            return result

        if enabled is not None:
            cron_service.enable_job(job_id, enabled)
            jobs = cron_service.list_jobs(include_disabled=True)
            result = next((j for j in jobs if j.id == job_id), result)

        return result

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
            context_block_limit=config.agents.defaults.context_block_limit,
            max_tool_result_chars=config.agents.defaults.max_tool_result_chars,
            provider_retry_mode=config.agents.defaults.provider_retry_mode,
            cron_service=cron_service,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            session_manager=session_manager,
            mcp_servers=config.tools.mcp_servers,
            channels_config=config.channels,
            timezone=config.agents.defaults.timezone,
            session_ttl_minutes=config.agents.defaults.session_ttl_minutes,
            consolidation_ratio=config.agents.defaults.consolidation_ratio,
            unified_session=config.agents.defaults.unified_session,
            disabled_skills=config.agents.defaults.disabled_skills,
            disabled_tools=_effective_disabled_tools(
                self.soulboard_config.disabled_tools,
                spec.overrides,
            ),
            tools_config=config.tools,
            image_generation_provider_configs=image_gen_provider_configs(config),
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
            payload = job.payload
            assert isinstance(payload, SoulCronPayload), (
                f"SoulCronService payload must be SoulCronPayload, got {type(payload).__name__}"
            )
            # Resolve the firing-time datetime in the job's schedule timezone
            # when set, so dynamic session keys rotate at that zone's midnight
            # and the reported datetime matches it (e.g. a soul that runs on a
            # different timezone than this process). Falls back to local time
            # when schedule.tz is unset or unrecognized.
            schedule_tz = job.schedule.tz
            fire_dt = datetime.now().astimezone()
            if schedule_tz:
                try:
                    fire_dt = datetime.now(ZoneInfo(schedule_tz))
                except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
                    logger.warning(
                        "Cron job '{}' ({}) schedule.tz {!r} is unrecognized; "
                        "using local time for session key and reported datetime: {}",
                        job.name,
                        job.id,
                        schedule_tz,
                        exc,
                    )
            recurring_format = payload.recurring_session_key_format
            rendered_session_key: str | None = None
            if recurring_format:
                try:
                    rendered_session_key = fire_dt.strftime(recurring_format)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "Cron job '{}' ({}) recurring_session_key_format {!r} "
                        "failed to render, falling back to stored session_key: {}",
                        job.name,
                        job.id,
                        recurring_format,
                        exc,
                    )
            session_key = (
                rendered_session_key
                or payload.session_key
                or f"{payload.channel or 'cli'}:{payload.to or 'direct'}"
            )
            session = session_manager.get_or_create(session_key)
            if not session_manager._get_session_path(session_key).exists():
                if not session.metadata:
                    session.metadata = {"title": session_key}
                    if ":" in session_key:
                        ch_part, chat_part = session_key.split(":", 1)
                        if ch_part:
                            session.metadata["channel"] = ch_part
                        if chat_part:
                            session.metadata["chat_id"] = chat_part
                session_manager.save(session)
                logger.info(
                    "Cron: created session {!r} for job '{}' ({})",
                    session_key,
                    job.name,
                    job.id,
                )
            delivery_metadata = cron_service.get_delivery_metadata(job.id)
            channel = payload.channel or "cli"
            chat_id = payload.to or "direct"
            fired_at = fire_dt.strftime("%Y-%m-%d %H:%M %Z")
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
                        session_key_override=session_key,
                        metadata={
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
        # Pre-connect MCP from a dedicated owner task, so the cancel scopes
        # bind to a task that can also exit them. Must complete before run()
        # starts (run() calls _connect_mcp itself, which is then a no-op).
        if running.agent_loop._mcp_servers and (
            not running.mcp_owner_task or running.mcp_owner_task.done()
        ):
            running.mcp_shutdown = asyncio.Event()
            running.mcp_connect_requested = asyncio.Event()
            running.mcp_reconnect_requests = asyncio.Queue()
            running.agent_loop.use_soulboard_mcp_lifecycle(
                running.mcp_connect_requested,
                running.mcp_reconnect_requests,
            )
            ready = asyncio.get_running_loop().create_future()
            running.mcp_owner_task = asyncio.create_task(
                _own_mcp_lifecycle(
                    running.agent_loop,
                    ready,
                    running.mcp_shutdown,
                    running.mcp_connect_requested,
                    running.mcp_reconnect_requests,
                ),
                name=f"mcp-owner:{soul_id}",
            )
            try:
                await ready
            except Exception as e:
                logger.warning("Soul '{}' MCP connect failed: {}", soul_id, e)
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
        # Tear down MCP via the owner task so close_mcp runs on the same task
        # that entered the cancel scopes.
        if running.mcp_shutdown is not None:
            running.mcp_shutdown.set()
        if running.mcp_owner_task and not running.mcp_owner_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(running.mcp_owner_task), timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.warning("Soul '{}' MCP owner did not exit in 10s; cancelling", soul_id)
                running.mcp_owner_task.cancel()
                with suppress(BaseException):
                    await running.mcp_owner_task
            except Exception:
                pass
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
