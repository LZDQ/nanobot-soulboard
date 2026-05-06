"""Skill metadata helpers shared by registry and per-soul endpoints."""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tiktoken
import yaml
from loguru import logger

from nanobot.agent.skills import _STRIP_SKILL_FRONTMATTER


@lru_cache(maxsize=1)
def _skill_encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_skill_md_tokens(skill_dir: Path) -> int | None:
    """Return the tiktoken count of SKILL.md, or None if it can't be read."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    return len(_skill_encoding().encode(content))


def count_text_tokens(text: str) -> int:
    """Return the tiktoken count of an already-loaded SKILL.md body."""
    return len(_skill_encoding().encode(text))


def parse_skill_metadata(skill_dir: Path) -> dict | None:
    """Parse SKILL.md frontmatter using nanobot's frontmatter regex.

    Returns the parsed dict, or None if the file/frontmatter is missing or unparseable.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not content.startswith("---"):
        return None
    match = _STRIP_SKILL_FRONTMATTER.match(content)
    if not match:
        return None
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def skill_summary(skill_dir: Path) -> tuple[str, str | None]:
    """Return (name, description) for a skill directory.

    Falls back to the directory name when no frontmatter name is present.
    """
    meta = parse_skill_metadata(skill_dir) or {}
    name = str(meta.get("name") or skill_dir.name)
    description = meta.get("description")
    return name, str(description) if description is not None else None


@dataclass(frozen=True)
class DiscoveredSkill:
    """One skill discovered inside a pool."""

    pool_path: str
    pool_root: Path
    skill_dir: Path
    relative_path: str
    name: str
    description: str | None
    token_count: int | None


def _is_valid_skill_header(meta: dict | None) -> bool:
    """A skill header is valid when frontmatter parsed and has a non-empty name."""
    if not isinstance(meta, dict):
        return False
    name = meta.get("name")
    return isinstance(name, str) and name.strip() != ""


def discover_skills_in_pool(pool_path: str, pool_root: Path) -> list[DiscoveredSkill]:
    """Walk pool_root recursively for subdirectories with valid SKILL.md frontmatter.

    A skill is a subdirectory of the pool (the pool root itself is not a skill) that
    contains a SKILL.md whose YAML frontmatter parses and has a non-empty name field.
    Skill directories prune further descent. Invalid SKILL.md files are logged and
    skipped without halting discovery.
    """
    if not pool_root.is_dir():
        return []
    discovered: list[DiscoveredSkill] = []
    for current, dirs, files in os.walk(pool_root, followlinks=False):
        cur_path = Path(current)
        dirs.sort()
        if cur_path == pool_root:
            continue
        if "SKILL.md" not in files:
            continue
        meta = parse_skill_metadata(cur_path)
        if not _is_valid_skill_header(meta):
            logger.warning(
                "Skipping skill at {}: SKILL.md has no valid frontmatter or missing 'name' field",
                cur_path,
            )
            dirs[:] = []
            continue
        assert meta is not None
        relative = cur_path.relative_to(pool_root).as_posix()
        description = meta.get("description")
        discovered.append(
            DiscoveredSkill(
                pool_path=pool_path,
                pool_root=pool_root,
                skill_dir=cur_path,
                relative_path=relative,
                name=str(meta["name"]).strip(),
                description=str(description) if description is not None else None,
                token_count=count_skill_md_tokens(cur_path),
            )
        )
        dirs[:] = []
    discovered.sort(key=lambda s: s.relative_path)
    return discovered
