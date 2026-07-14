"""Configuration helpers for nanobot-soulboard."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SOUL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
RESERVED_SOUL_IDS = frozenset({"api", "soulboard"})
DEFAULT_DISABLED_TOOLS = ("spawn", "long_task", "complete_goal")


def _default_nano_root() -> Path:
    return Path.home() / ".nanobot"


def normalize_url_prefix(value: str) -> str:
    """Normalize a public URL path prefix."""
    prefix = value.strip()
    if prefix in ("", "/"):
        return ""

    parsed = urlsplit(prefix)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("url_prefix must be a path prefix like /soulboard, not a full URL")

    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/")


class SoulboardSettings(BaseSettings):
    """Deployment settings loaded from SOULBOARD_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="SOULBOARD_", extra="ignore", validate_default=True)

    nano_root: Path = Field(
        default_factory=_default_nano_root,
        description="Nanobot data root. Defaults to ~/.nanobot.",
    )
    url_prefix: str = Field(
        default="",
        description="Public URL path prefix for the UI, API, and WebSocket routes.",
    )

    @field_validator("nano_root")
    @classmethod
    def expand_nano_root(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("url_prefix")
    @classmethod
    def validate_url_prefix(cls, value: str) -> str:
        return normalize_url_prefix(value)


class SoulOverrides(BaseModel):
    """Per-soul overrides layered on top of the base nanobot config."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(
        default=None,
        description="Optional model override layered on top of the base nanobot config.",
    )
    provider: str | None = Field(
        default=None,
        description="Optional provider override layered on top of the base nanobot config.",
    )
    channels: list[str] = Field(
        default_factory=list,
        description="List of channel names enabled for this soul runtime.",
    )
    mcp_servers: list[str] = Field(
        default_factory=list,
        description="List of MCP server names selected from the base nanobot config.",
    )
    mcp_http_headers: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description=(
            "Per-selected MCP server HTTP header overrides for this soul. "
            "Headers are merged on top of the shared base MCP server definition."
        ),
    )
    enabled_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Tools re-enabled for this soul when they are present in the global disabled_tools list. "
            "This does not enable tools that the base nanobot config did not register."
        ),
    )
    disabled_tools: list[str] = Field(
        default_factory=list,
        description="Additional tools disabled for this soul.",
    )
    autostart: bool = Field(
        default=False,
        description="Whether soulboard should automatically start this soul runtime.",
    )
    groups: list[str] = Field(
        default_factory=list,
        description=(
            "Display-only group tags for organizing souls in the frontend listing. The set of all "
            "groups is inferred as the union across souls; group membership has no runtime effect."
        ),
    )

    @field_validator("groups")
    @classmethod
    def _validate_groups(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = raw.strip()
            if not item or item in seen:
                continue
            normalized.append(item)
            seen.add(item)
        return normalized

    @field_validator("enabled_tools", "disabled_tools")
    @classmethod
    def _validate_tool_names(cls, value: list[str]) -> list[str]:
        return normalize_tool_names(value)


class CronJobRegistryEntry(BaseModel):
    """A predefined cron job template in the global registry."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Unique identifier for this registry entry.")
    label: str | None = Field(default=None, description="Optional human-readable display name.")
    cron_expr: str | None = Field(default=None, description="Cron expression (e.g. '0 9 * * 1-5').")
    every_seconds: int | None = Field(default=None, description="Fixed repeat interval in seconds.")
    tz: str | None = Field(default=None, description="Timezone for cron_expr schedules (e.g. 'America/New_York').")
    message: str = Field(default="", description="Message injected into the agent loop when this job fires.")
    deliver: bool = Field(default=False)
    channel: str | None = Field(default=None)
    chat_id: str | None = Field(
        default=None,
        description=(
            "Optional channel-local chat identifier. Stored on the cron payload as 'to'; combined with "
            "channel as the default session_key fallback when no explicit session_key is set."
        ),
    )
    session_key: str | None = Field(
        default=None,
        description="Optional explicit session_key for the scheduled job (overrides the channel:chat fallback).",
    )
    recurring_session_key_format: str | None = Field(
        default=None,
        description=(
            "strftime format rendered at fire-time to produce a dynamic session key (e.g. '%Y-%m-%d'). "
            "Rendered in the job's 'tz' timezone when set, so the key rotates at that zone's midnight."
        ),
    )


class SoulboardConfig(BaseModel):
    """Root soulboard config file, normally stored at ~/.nanobot/soulboard/config.json."""

    model_config = ConfigDict(extra="forbid")

    skill_registry: list[str] = Field(
        default_factory=list,
        description=(
            "Global registry of skill directory paths (each containing a SKILL.md). Souls can pick from "
            "this list when adding a skill, materialized as either a soft link or a copy."
        ),
    )
    cron_job_registry: list[CronJobRegistryEntry] = Field(
        default_factory=list,
        description=(
            "Global registry of predefined cron job templates. Souls can pick from this list to schedule "
            "recurring tasks, including those with dynamic session keys via recurring_session_key_format."
        ),
    )
    disabled_tools: list[str] = Field(
        default_factory=lambda: list(DEFAULT_DISABLED_TOOLS),
        description=(
            "Nanobot tools disabled for all souls by default. A soul can restore a registered tool by "
            "listing it in its enabled_tools setting."
        ),
    )
    @field_validator("skill_registry")
    @classmethod
    def validate_skill_registry(cls, value: list[str]) -> list[str]:
        return _normalize_skill_registry(value)

    @field_validator("cron_job_registry")
    @classmethod
    def validate_cron_job_registry(cls, value: list[CronJobRegistryEntry]) -> list[CronJobRegistryEntry]:
        return _normalize_cron_job_registry(value)

    @field_validator("disabled_tools")
    @classmethod
    def validate_disabled_tools(cls, value: list[str]) -> list[str]:
        return normalize_tool_names(value)


def _normalize_skill_registry(items: list[str]) -> list[str]:
    """Normalize configured global skill registry paths (skill directory paths)."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = raw.strip().rstrip("/")
        if not item:
            continue
        if item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized


def _normalize_cron_job_registry(entries: list[CronJobRegistryEntry]) -> list[CronJobRegistryEntry]:
    """Deduplicate by name; drop entries with empty names."""
    normalized: list[CronJobRegistryEntry] = []
    seen: set[str] = set()
    for entry in entries:
        name = entry.name.strip()
        if not name or name in seen:
            continue
        normalized.append(entry)
        seen.add(name)
    return normalized


def normalize_tool_names(items: list[str]) -> list[str]:
    """Normalize and deduplicate tool name lists while preserving order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        name = raw.strip()
        if not name or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    return normalized


def validate_soul_id(soul_id: str) -> str:
    """Validate a soul ID used as a directory name."""
    if not _SOUL_ID_RE.fullmatch(soul_id):
        raise ValueError(
            f"Invalid soul_id '{soul_id}'. Use lowercase letters, digits, '-' or '_', with no spaces."
        )
    if soul_id.lower() in RESERVED_SOUL_IDS:
        raise ValueError(
            f"Invalid soul_id '{soul_id}'. Reserved soul IDs: {', '.join(sorted(RESERVED_SOUL_IDS))}."
        )
    return soul_id


def get_soulboard_root(nano_root: Path) -> Path:
    """Return the soulboard root directory."""
    return nano_root / "soulboard"


def get_soulboard_config_path(nano_root: Path) -> Path:
    """Return the soulboard config path."""
    return get_soulboard_root(nano_root) / "config.json"


def get_base_config_path(nano_root: Path) -> Path:
    """Return the fixed upstream nanobot config path."""
    return nano_root / "config.json"


def get_souls_root(nano_root: Path) -> Path:
    """Return the souls directory."""
    return get_soulboard_root(nano_root) / "souls"


def get_soul_config_path(nano_root: Path, soul_id: str) -> Path:
    """Return the config path stored inside one soul workspace."""
    validate_soul_id(soul_id)
    return get_souls_root(nano_root) / soul_id / "config.json"


def load_soulboard_config(path: Path) -> SoulboardConfig:
    """Load soulboard config or return defaults when missing."""
    if not path.exists():
        return SoulboardConfig()

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Dropped feature: tolerate configs written before app_links was removed.
    data.pop("app_links", None)
    # Dropped feature: tolerate configs written with the legacy prompt_link_dirs key.
    data.pop("prompt_link_dirs", None)
    return SoulboardConfig.model_validate(data)


def save_soulboard_config(config: SoulboardConfig, path: Path) -> None:
    """Persist soulboard config as formatted JSON."""
    config.skill_registry = _normalize_skill_registry(config.skill_registry)
    config.disabled_tools = normalize_tool_names(config.disabled_tools)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True, by_alias=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_soul_config(path: Path) -> SoulOverrides:
    """Load one required workspace-local soul config."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return SoulOverrides.model_validate(data)


def save_soul_config(config: SoulOverrides, path: Path) -> None:
    """Persist one workspace-local soul config as formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True, by_alias=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
