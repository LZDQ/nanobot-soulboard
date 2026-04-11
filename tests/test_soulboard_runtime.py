import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from zoneinfo import ZoneInfo

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.cron.types import CronSchedule
from nanobot.providers.base import LLMResponse, ToolCallRequest
from nanobot_soulboard.config import SoulOverrides, SoulboardConfig, load_soulboard_config
from nanobot_soulboard.cron import SoulCronService, SoulCronTool
from nanobot_soulboard.context import SoulboardContextBuilder
from nanobot_soulboard.runtime import SoulAgentLoop, SoulSessionManager, SoulSpec, SoulSupervisor, build_runtime_config, discover_soul_specs


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


def test_build_runtime_config_merges_soul_mcp_http_headers(tmp_path: Path) -> None:
    base = Config.model_validate(
        {
            "tools": {
                "mcpServers": {
                    "shared": {
                        "type": "streamableHttp",
                        "url": "https://example.com/mcp",
                        "headers": {
                            "Authorization": "Bearer base-token",
                            "X-Shared": "1",
                        },
                    }
                }
            }
        }
    )
    spec = SoulSpec(
        soul_id="alpha",
        workspace=tmp_path / "souls" / "alpha",
        overrides=SoulOverrides(
            mcp_servers=["shared"],
            mcp_http_headers={
                "shared": {
                    "Authorization": "Bearer alpha-token",
                    "X-Soul": "alpha",
                }
            },
        ),
    )

    config = build_runtime_config(base, spec)

    assert config.tools.mcp_servers["shared"].headers == {
        "Authorization": "Bearer alpha-token",
        "X-Shared": "1",
        "X-Soul": "alpha",
    }
    assert base.tools.mcp_servers["shared"].headers == {
        "Authorization": "Bearer base-token",
        "X-Shared": "1",
    }


def test_build_runtime_config_rejects_unknown_mcp_http_header_override_server(tmp_path: Path) -> None:
    base = Config.model_validate(
        {
            "tools": {
                "mcpServers": {
                    "shared": {
                        "type": "streamableHttp",
                        "url": "https://example.com/mcp",
                    }
                }
            }
        }
    )
    spec = SoulSpec(
        soul_id="alpha",
        workspace=tmp_path / "souls" / "alpha",
        overrides=SoulOverrides(
            mcp_servers=["shared"],
            mcp_http_headers={"missing": {"Authorization": "Bearer token"}},
        ),
    )

    with pytest.raises(ValueError, match="Unknown MCP server\\(s\\) in soulboard MCP header overrides: missing"):
        build_runtime_config(base, spec)


def test_build_runtime_config_rejects_unselected_mcp_http_header_override_server(tmp_path: Path) -> None:
    base = Config.model_validate(
        {
            "tools": {
                "mcpServers": {
                    "shared": {
                        "type": "streamableHttp",
                        "url": "https://example.com/mcp",
                    }
                }
            }
        }
    )
    spec = SoulSpec(
        soul_id="alpha",
        workspace=tmp_path / "souls" / "alpha",
        overrides=SoulOverrides(
            mcp_servers=[],
            mcp_http_headers={"shared": {"Authorization": "Bearer token"}},
        ),
    )

    with pytest.raises(ValueError, match="MCP header overrides require the server to be enabled for this soul: shared"):
        build_runtime_config(base, spec)


def test_build_runtime_config_rejects_stdio_mcp_http_header_override_server(tmp_path: Path) -> None:
    base = Config.model_validate(
        {
            "tools": {
                "mcpServers": {
                    "filesystem": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["filesystem"],
                    }
                }
            }
        }
    )
    spec = SoulSpec(
        soul_id="alpha",
        workspace=tmp_path / "souls" / "alpha",
        overrides=SoulOverrides(
            mcp_servers=["filesystem"],
            mcp_http_headers={"filesystem": {"Authorization": "Bearer token"}},
        ),
    )

    with pytest.raises(ValueError, match="MCP header overrides are only supported for HTTP MCP servers: filesystem"):
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
    assert loop.tools.has("exec") is True
    assert "working_dir" in loop.tools.get("exec").parameters["properties"]
    assert loop._concurrency_gate is not None
    assert loop._concurrency_gate._value == 10


def test_soulboard_context_uses_workspace_system_md_verbatim(tmp_path: Path) -> None:
    (tmp_path / "SYSTEM.md").write_text("# Custom system\n\nUse this as-is.\n", encoding="utf-8")

    builder = SoulboardContextBuilder(tmp_path, soul_id="alpha")

    assert builder.build_system_prompt() == "# Custom system\n\nUse this as-is.\n"


def test_soulboard_context_falls_back_to_local_default_prompt(tmp_path: Path) -> None:
    builder = SoulboardContextBuilder(tmp_path, soul_id="alpha")

    prompt = builder.build_system_prompt()

    assert "You are the active soul 'alpha' running inside nanobot-soulboard." in prompt
    assert "## Runtime" in prompt
    assert "AGENTS.md" not in prompt


def test_soulboard_context_includes_workspace_skill_summary(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: Workspace skill for alpha\n---\n\n# Alpha Skill\n",
        encoding="utf-8",
    )

    builder = SoulboardContextBuilder(tmp_path, soul_id="alpha")

    prompt = builder.build_system_prompt()

    assert "# Skills" in prompt
    assert "<name>alpha-skill</name>" in prompt
    assert str(skill_dir / "SKILL.md") in prompt


def test_soulboard_context_appends_workspace_skills_to_system_md(tmp_path: Path) -> None:
    (tmp_path / "SYSTEM.md").write_text("# Custom system\n", encoding="utf-8")
    skill_dir = tmp_path / "skills" / "alpha-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: Workspace skill for alpha\n---\n\n# Alpha Skill\n",
        encoding="utf-8",
    )

    builder = SoulboardContextBuilder(tmp_path, soul_id="alpha")

    prompt = builder.build_system_prompt()

    assert prompt.startswith("# Custom system\n")
    assert "# Skills" in prompt
    assert "<name>alpha-skill</name>" in prompt


def test_soul_session_manager_round_trips_plain_metadata(tmp_path: Path) -> None:
    manager = SoulSessionManager(tmp_path)
    session = manager.get_or_create("cli:direct")
    session.metadata["title"] = "Direct chat"

    manager.save(session)
    reloaded = manager._load("cli:direct")

    assert reloaded is not None
    assert reloaded.metadata == {"title": "Direct chat"}


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
            reloaded = SoulSessionManager(tmp_path)._load("cli:direct")
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

    assert result is not None
    assert result.content == "final answer"
    reloaded = SoulSessionManager(tmp_path)._load("cli:direct")
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

    reloaded = SoulSessionManager(tmp_path)._load("cli:direct")
    assert reloaded is not None
    assert [message["role"] for message in reloaded.messages] == ["user", "assistant", "tool"]
    history = reloaded.get_history(max_messages=0)
    assert [message["role"] for message in history] == ["user", "assistant", "tool"]
    assert history[1]["tool_calls"][0]["id"] == "call_1"
    assert history[2]["tool_call_id"] == "call_1"


def test_soul_agent_loop_persists_final_response_once(tmp_path: Path) -> None:
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

    result = asyncio.run(loop.process_direct("hello"))

    assert result is not None
    assert result.content == "done"
    reloaded = SoulSessionManager(tmp_path)._load("cli:direct")
    assert reloaded is not None
    assert [message["role"] for message in reloaded.messages] == ["user", "assistant"]


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
    assert isinstance(loop.tools.get("cron"), SoulCronTool)
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
    tool._format_state = lambda _state, _schedule: []

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
    tool._format_state = lambda _state, _schedule: []

    result = await tool.execute(action="list", only_current_session=False)

    assert "job-a" in result
    assert "job-b" in result


@pytest.mark.asyncio
async def test_soul_cron_tool_list_uses_schedule_when_formatting_state(tmp_path: Path) -> None:
    service = SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="test-soul")
    service.add_job(
        name="job-a",
        schedule=CronSchedule(kind="cron", expr="0 8 * * *", tz="Asia/Shanghai"),
        message="a",
        channel="napcat",
        to="42",
        session_key="napcat:42",
    )

    tool = SoulCronTool(service)
    tool.set_context("napcat", "42", "napcat:42")

    result = await tool.execute(action="list", only_current_session=False)

    assert "Scheduled jobs:" in result
    assert "cron: 0 8 * * * (Asia/Shanghai)" in result


def test_soul_agent_loop_registers_cron_tool_with_configured_timezone(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = SoulAgentLoop(
        soul_id="alpha",
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        cron_service=SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="alpha"),
        timezone="Asia/Shanghai",
    )

    cron_tool = loop.tools.get("cron")

    assert isinstance(cron_tool, SoulCronTool)
    assert cron_tool._default_timezone == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_soul_cron_tool_defaults_cron_timezone_to_tool_timezone(tmp_path: Path) -> None:
    service = SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="test-soul")
    tool = SoulCronTool(service, default_timezone="Asia/Shanghai")
    tool.set_context("napcat", "42", "napcat:42")

    await tool.execute(action="add", message="hello", cron_expr="0 8 * * *")

    jobs = service.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    assert jobs[0].schedule.tz == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_soul_cron_tool_applies_default_timezone_to_naive_at(tmp_path: Path) -> None:
    service = SoulCronService(tmp_path / "cron" / "jobs.json", soul_id="test-soul")
    tool = SoulCronTool(service, default_timezone="Asia/Shanghai")
    tool.set_context("napcat", "42", "napcat:42")

    await tool.execute(action="add", message="hello", at="2026-03-26T08:30:00")

    job = service.list_jobs(include_disabled=True)[0]
    expected = int(datetime(2026, 3, 26, 8, 30, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    assert job.schedule.at_ms == expected
