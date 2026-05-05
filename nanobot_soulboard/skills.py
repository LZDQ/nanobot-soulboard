"""Skill metadata helpers shared by registry and per-soul endpoints."""

from functools import lru_cache
from pathlib import Path

import tiktoken
import yaml

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
