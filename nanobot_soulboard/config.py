"""Configuration helpers for nanobot-soulboard."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SOUL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


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
    recurring_session_key_format: str | None = Field(
        default=None,
        description="strftime format rendered at fire-time to produce a dynamic session key (e.g. '%Y-%m-%d').",
    )


class SoulboardConfig(BaseModel):
    """Root soulboard config file, normally stored at ~/.nanobot/soulboard/config.json."""

    model_config = ConfigDict(extra="forbid")

    app_links: list[str] = Field(default_factory=list)
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

    @field_validator("app_links")
    @classmethod
    def validate_app_links(cls, value: list[str]) -> list[str]:
        return _normalize_app_links(value)

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


def _normalize_app_links(items: list[str]) -> list[str]:
    """Normalize and validate top-bar app links."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = raw.strip()
        if not item:
            continue
        if not item.startswith("/"):
            raise ValueError(f"Invalid app link '{raw}'. App links must start with '/'.")
        if item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized


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
    config = SoulboardConfig.model_validate(data)
    for soul_id in config.souls:
        validate_soul_id(soul_id)
    return config


def save_soulboard_config(config: SoulboardConfig, path: Path) -> None:
    """Persist soulboard config as formatted JSON."""
    for soul_id in config.souls:
        validate_soul_id(soul_id)
    config.app_links = _normalize_app_links(config.app_links)
    config.prompt_link_dirs = _normalize_prompt_link_dirs(config.prompt_link_dirs)
    config.skill_registry = _normalize_skill_registry(config.skill_registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True, by_alias=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
