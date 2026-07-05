"""Configuration helpers for nanobot-soulboard."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SOUL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


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
    base_config_path: Path | None = Field(
        default=None,
        description="Base nanobot config path. Defaults to {nano_root}/config.json.",
    )
    soulboard_config_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("SOULBOARD_CONFIG_PATH", "SOULBOARD_SOULBOARD_CONFIG_PATH"),
        description="Soulboard config path. Defaults to {nano_root}/soulboard/config.json.",
    )
    url_prefix: str = Field(
        default="",
        description="Public URL path prefix for the UI, API, and WebSocket routes.",
    )

    @field_validator("nano_root")
    @classmethod
    def expand_nano_root(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("base_config_path", "soulboard_config_path", mode="before")
    @classmethod
    def empty_config_path_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @field_validator("base_config_path", "soulboard_config_path")
    @classmethod
    def expand_config_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        return value.expanduser()

    @field_validator("url_prefix")
    @classmethod
    def validate_url_prefix(cls, value: str) -> str:
        return normalize_url_prefix(value)

    @property
    def resolved_base_config_path(self) -> Path:
        if self.base_config_path is not None:
            return self.base_config_path
        return self.nano_root / "config.json"

    @property
    def resolved_soulboard_config_path(self) -> Path:
        if self.soulboard_config_path is not None:
            return self.soulboard_config_path
        return self.nano_root / "soulboard" / "config.json"


class SoulOverrides(BaseModel):
    """Per-soul overrides layered on top of the base nanobot config."""

    model_config = ConfigDict(extra="forbid")

    workspace: str | None = Field(
        default=None,
        description="Optional workspace override for this soul. Defaults to ~/.nanobot/soulboard/souls/{soul_id}.",
    )
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

    prompt_link_dirs: list[str] = Field(default_factory=list)
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
    souls: dict[str, SoulOverrides] = Field(default_factory=dict)

    @field_validator("prompt_link_dirs")
    @classmethod
    def validate_prompt_link_dirs(cls, value: list[str]) -> list[str]:
        return _normalize_prompt_link_dirs(value)

    @field_validator("skill_registry")
    @classmethod
    def validate_skill_registry(cls, value: list[str]) -> list[str]:
        return _normalize_skill_registry(value)

    @field_validator("cron_job_registry")
    @classmethod
    def validate_cron_job_registry(cls, value: list[CronJobRegistryEntry]) -> list[CronJobRegistryEntry]:
        return _normalize_cron_job_registry(value)


def _normalize_prompt_link_dirs(items: list[str]) -> list[str]:
    """Normalize configured prompt-link source directories."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = raw.strip()
        if not item:
            continue
        if item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized


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


def validate_soul_id(soul_id: str) -> str:
    """Validate a soul ID used as a directory name."""
    if not _SOUL_ID_RE.fullmatch(soul_id):
        raise ValueError(
            f"Invalid soul_id '{soul_id}'. Use lowercase letters, digits, '-' or '_', with no spaces."
        )
    return soul_id


def get_soulboard_root(nano_root: Path) -> Path:
    """Return the soulboard root directory."""
    return nano_root / "soulboard"


def get_soulboard_config_path(nano_root: Path) -> Path:
    """Return the soulboard config path."""
    return get_soulboard_root(nano_root) / "config.json"


def get_souls_root(nano_root: Path) -> Path:
    """Return the souls directory."""
    return get_soulboard_root(nano_root) / "souls"


def load_soulboard_config(path: Path) -> SoulboardConfig:
    """Load soulboard config or return defaults when missing."""
    if not path.exists():
        return SoulboardConfig()

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # Dropped feature: tolerate configs written before app_links was removed.
    data.pop("app_links", None)
    config = SoulboardConfig.model_validate(data)
    for soul_id in config.souls:
        validate_soul_id(soul_id)
    return config


def save_soulboard_config(config: SoulboardConfig, path: Path) -> None:
    """Persist soulboard config as formatted JSON."""
    for soul_id in config.souls:
        validate_soul_id(soul_id)
    config.prompt_link_dirs = _normalize_prompt_link_dirs(config.prompt_link_dirs)
    config.skill_registry = _normalize_skill_registry(config.skill_registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True, by_alias=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
