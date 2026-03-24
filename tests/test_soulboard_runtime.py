import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.cron.types import CronSchedule
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot_soulboard.config import SoulOverrides, SoulboardConfig, load_soulboard_config
from nanobot_soulboard.cron import SoulCronService, SoulCronTool
from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.runtime import SoulAgentLoop, SoulSession, SoulSessionManager, SoulSpec, SoulSupervisor, build_runtime_config, discover_soul_specs
from nanobot_soulboard.shell_tools import CdTool, SetEnvTool


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
    assert loop.tools.has("cd") is True
    assert loop.tools.has("set_env") is True
    assert loop.tools.has("exec") is True
    assert "working_dir" not in loop.tools.get("exec").parameters["properties"]
    assert loop.tools.has("source") is (os.name != "nt")


def test_soulboard_context_uses_workspace_system_md_verbatim(tmp_path: Path) -> None:
    (tmp_path / "SYSTEM.md").write_text("# Custom system\n\nUse this as-is.\n", encoding="utf-8")

    builder = SoulboardContextBuilder(tmp_path, soul_id="alpha")

    assert builder.build_system_prompt() == "# Custom system\n\nUse this as-is.\n"


def test_soulboard_context_falls_back_to_local_default_prompt(tmp_path: Path) -> None:
    builder = SoulboardContextBuilder(tmp_path, soul_id="alpha")

    prompt = builder.build_system_prompt()

    assert 'You are the active soul "alpha" running inside nanobot-soulboard.' in prompt
    assert str(tmp_path.resolve()) in prompt
    assert "AGENTS.md" not in prompt


def test_cd_tool_updates_directory_and_enforces_workspace(tmp_path: Path) -> None:
    current = {"cwd": None}
    child = tmp_path / "child"
    child.mkdir()
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    tool = CdTool(set_cwd=lambda path: current.__setitem__("cwd", path))

    result = asyncio.run(tool.execute(str(child)))
    assert result == str(child.resolve())
    assert current["cwd"] == child.resolve()

    moved = asyncio.run(tool.execute(str(outside)))
    assert moved == str(outside.resolve())
    assert current["cwd"] == outside.resolve()


def test_set_env_tool_merges_values() -> None:
    current = {"env": {"PATH": "/usr/bin"}}
    tool = SetEnvTool(
        get_env=lambda: dict(current["env"]),
        set_env=lambda env: current.__setitem__("env", env),
    )

    result = asyncio.run(tool.execute({"HELLO": "world"}))

    assert "HELLO" in result
    assert current["env"]["PATH"] == "/usr/bin"
    assert current["env"]["HELLO"] == "world"


def test_soul_agent_loop_shell_state_is_lazy_and_persisted(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    session = loop.sessions.get_or_create("cli:direct")

    loop._restore_session_state(session)
    assert loop._cwd is None
    assert loop._env is None
    assert loop.get_cwd() == tmp_path
    assert "PATH" in loop.get_env()
    assert session.metadata == {}

    loop.set_cwd(tmp_path / "nested")
    loop.set_env({"HELLO": "world"})
    loop.sessions.save(session)
    assert session.metadata["cwd"] == str((tmp_path / "nested"))
    assert session.metadata["env"] == {"HELLO": "world"}

    restored = SoulSession(
        key="cli:second",
        cwd=(tmp_path / "saved").resolve(),
        env={"FOO": "bar"},
    )
    loop._restore_session_state(restored)
    assert loop.get_cwd() == (tmp_path / "saved").resolve()
    assert loop.get_env()["FOO"] == "bar"


@pytest.mark.skipif(os.name == "nt", reason="source tool is unix-only")
def test_source_tool_loads_environment(tmp_path: Path) -> None:
    from nanobot_soulboard.shell_tools import SourceTool

    current = {"cwd": tmp_path, "env": {"PATH": os.environ.get("PATH", "")}}
    script = tmp_path / "activate.sh"
    script.write_text('export SOULBOARD_TEST_VAR="hello"\n', encoding="utf-8")
    tool = SourceTool(
        get_cwd=lambda: current["cwd"],
        get_env=lambda: dict(current["env"]),
        set_env=lambda env: current.__setitem__("env", env),
    )

    result = asyncio.run(tool.execute("activate.sh"))

    assert result == f"Sourced environment from {script.resolve()}"
    assert current["env"]["SOULBOARD_TEST_VAR"] == "hello"


def test_soul_session_manager_loads_and_saves_shell_state(tmp_path: Path) -> None:
    manager = SoulSessionManager(
        tmp_path,
        get_cwd=lambda: (tmp_path / "cwd").resolve(),
        get_env=lambda: {"FOO": "bar"},
    )
    session = manager.get_or_create("cli:direct")
    assert session.cwd is None
    assert session.env is None

    manager.save(session)
    reloaded = manager._load("cli:direct")

    assert reloaded is not None
    assert reloaded.cwd == (tmp_path / "cwd").resolve()
    assert reloaded.env == {"FOO": "bar"}


def test_soul_agent_loop_persists_completed_tool_loops_incrementally(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    tool_results = {"first_tool": "first result", "second_tool": "second result"}

    async def _execute(name: str, _arguments: dict) -> str:
        return tool_results[name]

    async def _chat_with_retry(*, messages, tools, model):
        call_index = _chat_with_retry.calls
        _chat_with_retry.calls += 1
        if call_index == 0:
            return LLMResponse(
                content="first loop",
                tool_calls=[ToolCallRequest(id="call_1", name="first_tool", arguments={"value": 1})],
                finish_reason="tool_calls",
            )
        if call_index == 1:
            reloaded = SoulSessionManager(
                tmp_path,
                get_cwd=lambda: None,
                get_env=lambda: None,
            )._load("cli:direct")
            assert reloaded is not None
            assert [message["role"] for message in reloaded.messages] == ["user", "assistant", "tool"]
            assert reloaded.messages[1]["tool_calls"][0]["id"] == "call_1"
            return LLMResponse(
                content="second loop",
                tool_calls=[ToolCallRequest(id="call_2", name="second_tool", arguments={"value": 2})],
                finish_reason="tool_calls",
            )
        return LLMResponse(content="final answer", finish_reason="stop")

    _chat_with_retry.calls = 0
    provider.chat_with_retry.side_effect = _chat_with_retry
    loop.tools.execute = _execute

    result = asyncio.run(loop.process_direct("hello"))

    assert result == "final answer"
    reloaded = SoulSessionManager(tmp_path, get_cwd=lambda: None, get_env=lambda: None)._load("cli:direct")
    assert reloaded is not None
    assert [message["role"] for message in reloaded.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    assert sum(1 for message in reloaded.messages if message["role"] == "user") == 1


def test_soul_agent_loop_preserves_completed_tool_loops_after_crash(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    async def _execute(_name: str, _arguments: dict) -> str:
        return "first result"

    async def _chat_with_retry(*, messages, tools, model):
        call_index = _chat_with_retry.calls
        _chat_with_retry.calls += 1
        if call_index == 0:
            return LLMResponse(
                content="first loop",
                tool_calls=[ToolCallRequest(id="call_1", name="first_tool", arguments={"value": 1})],
                finish_reason="tool_calls",
            )
        raise RuntimeError("boom")

    _chat_with_retry.calls = 0
    provider.chat_with_retry.side_effect = _chat_with_retry
    loop.tools.execute = _execute

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(loop.process_direct("hello"))

    reloaded = SoulSessionManager(tmp_path, get_cwd=lambda: None, get_env=lambda: None)._load("cli:direct")
    assert reloaded is not None
    assert [message["role"] for message in reloaded.messages] == ["user", "assistant", "tool"]
    history = reloaded.get_history(max_messages=0)
    assert [message["role"] for message in history] == ["user", "assistant", "tool"]
    assert history[1]["tool_calls"][0]["id"] == "call_1"
    assert history[2]["tool_call_id"] == "call_1"


def test_soul_agent_loop_persists_final_response_once_and_keeps_shell_metadata(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done", finish_reason="stop"))

    session = loop.sessions.get_or_create("cli:direct")
    loop.set_cwd((tmp_path / "nested").resolve())
    loop.set_env({"HELLO": "world"})
    loop.sessions.save(session)

    result = asyncio.run(loop.process_direct("hello"))

    assert result == "done"
    reloaded = SoulSessionManager(tmp_path, get_cwd=lambda: None, get_env=lambda: None)._load("cli:direct")
    assert reloaded is not None
    assert [message["role"] for message in reloaded.messages] == ["user", "assistant"]
    assert reloaded.metadata["cwd"] == str((tmp_path / "nested").resolve())
    assert reloaded.metadata["env"] == {"HELLO": "world"}


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


def test_start_soul_registers_cron_tool(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider_factory = MagicMock(return_value=provider)
    supervisor = SoulSupervisor(
        base_config=Config(),
        nano_root=tmp_path,
        soulboard_config=SoulboardConfig(souls={"alpha": SoulOverrides()}),
        provider_factory=provider_factory,
    )

    loop = asyncio.run(supervisor.start_soul("alpha"))
    running = supervisor._running_souls["alpha"]

    assert loop.tools.has("cron") is True
    assert running.cron_started is True
    assert running.cron_service.store_path == tmp_path / "soulboard" / "souls" / "alpha" / "cron" / "jobs.json"

    asyncio.run(supervisor.stop_all())


def test_soul_cron_service_persists_origin_session_key(tmp_path: Path) -> None:
    service = SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="test-soul")

    job = service.add_job(
        name="hello",
        schedule=CronSchedule(kind="every", every_ms=1000),
        message="hello",
        channel="napcat",
        to="42",
        session_key="napcat:42:topic",
        delivery_metadata={"is_group": True},
    )

    reloaded = SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="test-soul")

    assert reloaded.get_session_key(job.id) == "napcat:42:topic"
    assert reloaded.get_delivery_metadata(job.id) == {"is_group": True}
    assert not (tmp_path / "cron" / "session-keys.json").exists()
    stored = json.loads((tmp_path / "cron" / "jobs.json").read_text(encoding="utf-8"))
    assert stored["jobs"][0]["sessionKey"] == "napcat:42:topic"
    assert stored["jobs"][0]["deliveryMetadata"] == {"is_group": True}


def test_soul_agent_loop_system_message_preserves_delivery_metadata(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="done", finish_reason="stop"))
    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    result = asyncio.run(
        loop._process_message(
            InboundMessage(
                channel="system",
                sender_id="cron",
                chat_id="napcat:42",
                content="System: cron fired",
                metadata={"_system_session_key": "napcat:42", "is_group": True},
            )
        )
    )

    assert result is not None
    assert result.channel == "napcat"
    assert result.chat_id == "42"
    assert result.metadata == {"is_group": True}


def test_soul_supervisor_cron_callback_restores_delivery_metadata_after_reload(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider_factory = MagicMock(return_value=provider)
    supervisor = SoulSupervisor(
        base_config=Config(),
        nano_root=tmp_path,
        soulboard_config=SoulboardConfig(souls={"alpha": SoulOverrides()}),
        provider_factory=provider_factory,
    )
    service = SoulCronService(tmp_path / "soulboard" / "souls" / "alpha" / "cron" / "jobs.json", soul_id="alpha")
    job = service.add_job(
        name="morning",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="早安",
        channel="napcat",
        to="238637176",
        session_key="napcat:238637176",
        delivery_metadata={"is_group": True},
    )

    running = supervisor._build_running_soul("alpha")
    running.bus.publish_inbound = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(running.cron_service.on_job(job))

    published = running.bus.publish_inbound.await_args.args[0]
    assert published.chat_id == "napcat:238637176"
    assert published.metadata["_system_session_key"] == "napcat:238637176"
    assert published.metadata["is_group"] is True


@pytest.mark.asyncio
async def test_soul_cron_tool_lists_only_current_session_by_default(tmp_path: Path) -> None:
    service = SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="test-soul")
    service.add_job(
        name="job-a",
        schedule=CronSchedule(kind="every", every_ms=1_000),
        message="a",
        channel="napcat",
        to="42",
        session_key="napcat:42",
    )
    service.add_job(
        name="job-b",
        schedule=CronSchedule(kind="every", every_ms=2_000),
        message="b",
        channel="napcat",
        to="43",
        session_key="napcat:43",
    )

    tool = SoulCronTool(service)
    tool.set_context("napcat", "42", "napcat:42")

    result = await tool.execute(action="list")

    assert "job-a" in result
    assert "job-b" not in result


@pytest.mark.asyncio
async def test_soul_cron_tool_can_list_all_sessions_jobs(tmp_path: Path) -> None:
    service = SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="test-soul")
    service.add_job(
        name="job-a",
        schedule=CronSchedule(kind="every", every_ms=1_000),
        message="a",
        channel="napcat",
        to="42",
        session_key="napcat:42",
    )
    service.add_job(
        name="job-b",
        schedule=CronSchedule(kind="every", every_ms=2_000),
        message="b",
        channel="napcat",
        to="43",
        session_key="napcat:43",
    )

    tool = SoulCronTool(service)
    tool.set_context("napcat", "42", "napcat:42")

    result = await tool.execute(action="list", only_current_session=False)

    assert "job-a" in result
    assert "job-b" in result
