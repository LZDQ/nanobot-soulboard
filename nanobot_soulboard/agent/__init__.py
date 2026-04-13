"""Agent runtime components for nanobot-soulboard."""

from nanobot_soulboard.agent.loop import SoulAgentLoop
from nanobot_soulboard.agent.supervisor import (
    SOUL_PROMPT_FILES,
    SoulSpec,
    SoulSupervisor,
    build_runtime_config,
    discover_soul_specs,
)

__all__ = [
    "SOUL_PROMPT_FILES",
    "SoulAgentLoop",
    "SoulSpec",
    "SoulSupervisor",
    "build_runtime_config",
    "discover_soul_specs",
]
