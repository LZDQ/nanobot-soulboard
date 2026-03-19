from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot_soulboard.config import SoulOverrides, SoulboardConfig, discover_soul_ids
from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.runtime import SoulAgentLoop, SoulSpec, build_runtime_config, discover_soul_specs


def test_discover_soul_ids_merges_config_and_directories(tmp_path: Path) -> None:
    souls_root = tmp_path / "souls"
    (souls_root / "alpha").mkdir(parents=True)
    (souls_root / "beta").mkdir(parents=True)

    config = SoulboardConfig(souls={"gamma": SoulOverrides()})

    assert discover_soul_ids(root=tmp_path, config=config) == ["alpha", "beta", "gamma"]


def test_discover_soul_specs_use_directory_as_default_workspace(tmp_path: Path) -> None:
    (tmp_path / "souls" / "alpha").mkdir(parents=True)

    specs = discover_soul_specs(root=tmp_path, config=SoulboardConfig())

    assert len(specs) == 1
    assert specs[0].workspace == tmp_path / "souls" / "alpha"


def test_build_runtime_config_applies_workspace_channel_and_mcp_filters(tmp_path: Path) -> None:
    base = Config.model_validate(
        {
            "agents": {"defaults": {"workspace": "~/base-workspace", "model": "base-model"}},
            "channels": {
                "telegram": {"enabled": True, "token": "telegram-token"},
                "slack": {"enabled": True, "botToken": "x", "appToken": "y"},
            },
            "tools": {
                "mcpServers": {
                    "github": {"command": "npx", "args": ["github"]},
                    "filesystem": {"command": "npx", "args": ["filesystem"]},
                }
            },
        }
    )
    spec = SoulSpec(
        soul_id="alpha",
        workspace=tmp_path / "souls" / "alpha",
        overrides=SoulOverrides(
            model="soul-model",
            channels=["slack"],
            mcp_servers=["filesystem"],
        ),
    )

    config = build_runtime_config(base, spec)
    channels = config.channels.model_dump(by_alias=True)

    assert config.workspace_path == tmp_path / "souls" / "alpha"
    assert config.agents.defaults.model == "soul-model"
    assert channels["telegram"]["enabled"] is False
    assert channels["slack"]["enabled"] is True
    assert sorted(config.tools.mcp_servers) == ["filesystem"]


def test_build_runtime_config_rejects_unknown_mcp_server(tmp_path: Path) -> None:
    base = Config()
    spec = SoulSpec(
        soul_id="alpha",
        workspace=tmp_path / "souls" / "alpha",
        overrides=SoulOverrides(mcp_servers=["missing"]),
    )

    with pytest.raises(ValueError, match="Unknown MCP server"):
        build_runtime_config(base, spec)


def test_soul_agent_loop_swaps_in_soulboard_context(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    assert isinstance(loop.context, SoulboardContextBuilder)
    assert loop.context.soul_id == "alpha"
