import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot_soulboard.config import SoulOverrides, SoulboardConfig, load_soulboard_config
from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.runtime import SoulAgentLoop, SoulSpec, SoulSupervisor, build_runtime_config, discover_soul_specs


def test_discover_soul_specs_uses_config_as_source_of_truth(tmp_path: Path) -> None:
    config = SoulboardConfig(souls={"alpha": SoulOverrides(), "beta": SoulOverrides()})
    specs = discover_soul_specs(nano_root=tmp_path, config=config)

    assert {spec.soul_id for spec in specs} == {"alpha", "beta"}


def test_discover_soul_specs_use_directory_as_default_workspace(tmp_path: Path) -> None:
    config = SoulboardConfig(souls={"alpha": SoulOverrides()})

    specs = discover_soul_specs(nano_root=tmp_path, config=config)

    assert len(specs) == 1
    assert specs[0].workspace == tmp_path / "soulboard" / "souls" / "alpha"


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


def test_modify_soul_rejects_running_soul(tmp_path: Path) -> None:
    supervisor = SoulSupervisor(
        base_config=Config(),
        nano_root=tmp_path,
        soulboard_config=SoulboardConfig(souls={"alpha": SoulOverrides()}),
        provider_factory=MagicMock(),
    )
    supervisor._running_souls["alpha"] = MagicMock()

    with pytest.raises(RuntimeError, match="Cannot modify running soul"):
        supervisor.modify_soul("alpha", SoulOverrides(model="other-model"))


def test_create_modify_and_delete_soul_persist_config(tmp_path: Path) -> None:
    supervisor = SoulSupervisor(
        base_config=Config(),
        nano_root=tmp_path,
        soulboard_config=SoulboardConfig(),
        provider_factory=MagicMock(),
    )

    spec = supervisor.create_soul("alpha", SoulOverrides(model="model-a", autostart=True))

    assert spec.soul_id == "alpha"
    saved = load_soulboard_config(tmp_path / "soulboard" / "config.json")
    assert saved.souls["alpha"].model == "model-a"
    assert saved.souls["alpha"].autostart is True

    supervisor.modify_soul("alpha", SoulOverrides(provider="openrouter"))
    saved = load_soulboard_config(tmp_path / "soulboard" / "config.json")
    assert saved.souls["alpha"].provider == "openrouter"

    supervisor.delete_soul("alpha")
    saved = load_soulboard_config(tmp_path / "soulboard" / "config.json")
    assert "alpha" not in saved.souls


def test_start_autostart_souls_starts_only_marked_souls(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider_factory = MagicMock(return_value=provider)
    supervisor = SoulSupervisor(
        base_config=Config(),
        nano_root=tmp_path,
        soulboard_config=SoulboardConfig(
            souls={
                "alpha": SoulOverrides(autostart=True),
                "beta": SoulOverrides(autostart=False),
            }
        ),
        provider_factory=provider_factory,
    )

    loops = asyncio.run(supervisor.start_autostart_souls())

    assert {loop.context.soul_id for loop in loops} == {"alpha"}
    assert supervisor.list_running_souls() == ["alpha"]
    assert supervisor.is_running("alpha") is True
    assert supervisor.is_running("beta") is False

    asyncio.run(supervisor.stop_all())
