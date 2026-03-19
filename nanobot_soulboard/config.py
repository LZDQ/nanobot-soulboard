"""Configuration helpers for nanobot-soulboard."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_SOUL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class SoulOverrides(BaseModel):
    """Per-soul overrides layered on top of the base nanobot config."""

    model_config = ConfigDict(extra="forbid")

    workspace: str | None = None
    model: str | None = None
    provider: str | None = None
    channels: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    autostart: bool = False


class SoulboardConfig(BaseModel):
    """Root soulboard config file."""

    model_config = ConfigDict(extra="forbid")

    souls: dict[str, SoulOverrides] = Field(default_factory=dict)


def validate_soul_id(soul_id: str) -> str:
    """Validate a soul ID used as a directory name."""
    if not _SOUL_ID_RE.fullmatch(soul_id):
        raise ValueError(
            f"Invalid soul_id '{soul_id}'. Use lowercase letters, digits, '-' or '_', with no spaces."
        )
    return soul_id


def get_soulboard_root(root: Path | None = None) -> Path:
    """Return the soulboard root directory."""
    return (root or Path.home() / ".nanobot" / "soulboard").expanduser()


def get_soulboard_config_path(root: Path | None = None) -> Path:
    """Return the soulboard config path."""
    return get_soulboard_root(root) / "config.json"


def get_souls_root(root: Path | None = None) -> Path:
    """Return the souls directory."""
    return get_soulboard_root(root) / "souls"


def load_soulboard_config(path: Path | None = None) -> SoulboardConfig:
    """Load soulboard config or return defaults when missing."""
    config_path = path or get_soulboard_config_path()
    if not config_path.exists():
        return SoulboardConfig()

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    config = SoulboardConfig.model_validate(data)
    for soul_id in config.souls:
        validate_soul_id(soul_id)
    return config


def discover_soul_ids(root: Path | None = None, config: SoulboardConfig | None = None) -> list[str]:
    """Discover souls from both config and on-disk directories."""
    souls_root = get_souls_root(root)
    discovered = set()
    if souls_root.exists():
        for entry in souls_root.iterdir():
            if entry.is_dir():
                validate_soul_id(entry.name)
                discovered.add(entry.name)
    if config is not None:
        for soul_id in config.souls:
            discovered.add(validate_soul_id(soul_id))
    return sorted(discovered)
