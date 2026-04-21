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


class SoulboardConfig(BaseModel):
    """Root soulboard config file, normally stored at ~/.nanobot/soulboard/config.json."""

    model_config = ConfigDict(extra="forbid")

    app_links: list[str] = Field(default_factory=list)
    souls: dict[str, SoulOverrides] = Field(default_factory=dict)

    @field_validator("app_links")
    @classmethod
    def validate_app_links(cls, value: list[str]) -> list[str]:
        return _normalize_app_links(value)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json", exclude_none=True, by_alias=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
