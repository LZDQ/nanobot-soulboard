import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from nanobot.cron.types import CronSchedule
from nanobot_soulboard.server import create_app
from nanobot_soulboard.cron import SoulCronService


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_server_soul_lifecycle(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: provider)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.runtime.SoulAgentLoop.process_direct", AsyncMock(return_value="hello"))

    _write_json(tmp_path / "config.json", {"providers": {"ollama": {"apiBase": "http://localhost:11434"}}})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

        created = client.post(
            "/api/souls",
            json={"soul_id": "alpha", "overrides": {"model": "test-model", "autostart": True}},
        )
        assert created.status_code == 200
        assert created.json()["soul_id"] == "alpha"
        assert created.json()["running"] is True

        listed = client.get("/api/souls")
        assert listed.status_code == 200
        assert [item["soul_id"] for item in listed.json()] == ["alpha"]

        updated = client.patch("/api/souls/alpha", json={"overrides": {"provider": "ollama"}})
        assert updated.status_code == 200
        assert updated.json()["overrides"]["provider"] == "ollama"

        chatted = client.post("/api/souls/alpha/chat", json={"content": "hi"})
        assert chatted.status_code == 200
        assert chatted.json() == {"content": "hello"}

        stopped = client.post("/api/souls/alpha/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False

        deleted = client.delete("/api/souls/alpha")
        assert deleted.status_code == 204


def test_server_start_prunes_unknown_mcp_servers(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: provider)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.runtime.SoulAgentLoop.run", AsyncMock(return_value=None))

    _write_json(
        tmp_path / "config.json",
        {
            "tools": {
                "mcpServers": {
                    "filesystem": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                        "env": {},
                        "headers": {},
                        "enabledTools": ["*"],
                    }
                }
            }
        },
    )
    _write_json(
        tmp_path / "soulboard" / "config.json",
        {"souls": {"alpha": {"mcp_servers": ["filesystem", "missing"], "autostart": False}}},
    )

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        started = client.post("/api/souls/alpha/start")
        assert started.status_code == 200
        assert started.json()["running"] is True
        assert started.json()["overrides"]["mcp_servers"] == ["filesystem"]

        persisted = json.loads((tmp_path / "soulboard" / "config.json").read_text(encoding="utf-8"))
        assert persisted["souls"]["alpha"]["mcp_servers"] == ["filesystem"]


def test_server_lists_and_reads_sessions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    session_dir = tmp_path / "soulboard" / "souls" / "alpha" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / "cli_direct.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "_type": "metadata",
                "key": "cli:direct",
                "created_at": "2025-01-01T00:00:00",
                "updated_at": "2025-01-01T00:00:00",
                "metadata": {"title": "demo"},
                "last_consolidated": 0,
            }
        )
        + "\n"
        + json.dumps({"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:01"})
        + "\n"
        + json.dumps(
            {
                "role": "assistant",
                "content": "world",
                "reasoning_content": "private chain",
                "timestamp": "2025-01-01T00:00:02",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        listed = client.get("/api/souls/alpha/sessions")
        assert listed.status_code == 200
        assert listed.json()[0]["key"] == "cli:direct"

        detail = client.get("/api/souls/alpha/sessions/cli:direct")
        assert detail.status_code == 200
        assert detail.json()["messages"][0]["content"] == "hello"
        assert detail.json()["messages"][1]["reasoning_content"] == "private chain"
        assert "soul_id" not in detail.json()
        assert "key" not in detail.json()


def test_server_creates_empty_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        created = client.post("/api/souls/alpha/sessions", json={"key": "cli:new"})
        assert created.status_code == 200
        assert created.json()["messages"] == []
        assert created.json()["metadata"] == {
            "title": "cli:new",
            "channel": "cli",
            "chat_id": "new",
        }

        listed = client.get("/api/souls/alpha/sessions")
        assert listed.status_code == 200
        assert listed.json()[0]["key"] == "cli:new"

        detail = client.get("/api/souls/alpha/sessions/cli:new")
        assert detail.status_code == 200
        assert detail.json()["messages"] == []
        assert detail.json()["metadata"] == {
            "title": "cli:new",
            "channel": "cli",
            "chat_id": "new",
        }


def test_server_reads_session_shell_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    session_dir = tmp_path / "soulboard" / "souls" / "alpha" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / "cli_direct.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "_type": "metadata",
                "key": "cli:direct",
                "created_at": "2025-01-01T00:00:00",
                "updated_at": "2025-01-01T00:00:00",
                "metadata": {"cwd": "/tmp/demo", "env": {"DEBUG": "1", "HELLO": "world"}},
                "last_consolidated": 0,
            }
        )
        + "\n"
        + json.dumps({"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:01"})
        + "\n",
        encoding="utf-8",
    )

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        shell_state = client.get("/api/souls/alpha/sessions/cli:direct/shell-state")
        assert shell_state.status_code == 200
        assert shell_state.json() == {
            "cwd": "/tmp/demo",
            "env": {"DEBUG": "1", "HELLO": "world"},
        }


def test_server_reads_live_session_shell_state_from_running_loop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    live_loop = MagicMock()
    live_loop.get_cwd.return_value = Path("/tmp/live")
    live_loop.get_env.return_value = {"LIVE": "1", "DEBUG": "true"}

    with TestClient(app) as client:
        client.app.state.soulboard.supervisor.get_agent_loop = MagicMock(return_value=live_loop)
        shell_state = client.get("/api/souls/alpha/sessions/cli:direct/shell-state")
        assert shell_state.status_code == 200
        assert shell_state.json() == {
            "cwd": "/tmp/live",
            "env": {"LIVE": "1", "DEBUG": "true"},
        }


def test_server_reads_and_updates_soul_prompt_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    workspace = tmp_path / "soulboard" / "souls" / "alpha"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("# Agent rules\n", encoding="utf-8")
    (workspace / "SYSTEM.md").write_text("# System prompt\n", encoding="utf-8")

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        listed = client.get("/api/souls/alpha/prompt-files")
        assert listed.status_code == 200
        assert listed.json() == {
            "files": [
                {"name": "AGENTS.md", "exists": True, "content": "# Agent rules\n"},
                {"name": "SOUL.md", "exists": False, "content": ""},
                {"name": "USER.md", "exists": False, "content": ""},
                {"name": "TOOLS.md", "exists": False, "content": ""},
                {"name": "SYSTEM.md", "exists": True, "content": "# System prompt\n"},
            ]
        }

        updated = client.patch(
            "/api/souls/alpha/prompt-files",
            json={
                "files": [
                    {"name": "SOUL.md", "content": "# Soul profile\n"},
                    {"name": "TOOLS.md", "content": "# Tool hints\n"},
                ]
            },
        )
        assert updated.status_code == 200
        assert updated.json()["files"][1] == {"name": "SOUL.md", "exists": True, "content": "# Soul profile\n"}
        assert updated.json()["files"][3] == {"name": "TOOLS.md", "exists": True, "content": "# Tool hints\n"}

        assert (workspace / "SOUL.md").read_text(encoding="utf-8") == "# Soul profile\n"
        assert (workspace / "TOOLS.md").read_text(encoding="utf-8") == "# Tool hints\n"


def test_server_lists_soul_cron_jobs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    workspace = tmp_path / "soulboard" / "souls" / "alpha"
    service = SoulCronService(workspace / "cron" / "jobs.json", soul_id="test-soul")
    service.add_job(
        name="Ping group",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="daily ping",
        deliver=True,
        channel="napcat",
        to="1064627451",
        session_key="napcat:1064627451",
    )

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        listed = client.get("/api/souls/alpha/cron-jobs")
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "id": listed.json()[0]["id"],
                "name": "Ping group",
                "enabled": True,
                "delete_after_run": False,
                "message": "daily ping",
                "deliver": True,
                "channel": "napcat",
                "chat_id": "1064627451",
                "session_key": "napcat:1064627451",
                "schedule": {
                    "kind": "every",
                    "at_ms": None,
                    "every_ms": 60000,
                    "expr": None,
                    "tz": None,
                },
                "state": {
                    "next_run_at_ms": listed.json()[0]["state"]["next_run_at_ms"],
                    "last_run_at_ms": None,
                    "last_status": None,
                    "last_error": None,
                },
            }
        ]


def test_server_lists_and_updates_mcp_servers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(
        tmp_path / "config.json",
        {
            "tools": {
                "mcpServers": {
                    "filesystem": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                        "env": {},
                        "headers": {},
                        "enabledTools": ["*"],
                    }
                }
            }
        },
    )
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {"mcp_servers": ["filesystem"]}}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        listed = client.get("/api/mcp-servers")
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "name": "filesystem",
                "config": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                    "env": {},
                    "url": "",
                    "headers": {},
                    "toolTimeout": 30,
                    "enabledTools": ["*"],
                },
            }
        ]

        updated = client.patch(
            "/api/mcp-servers/filesystem",
            json={
                "config": {
                    "type": "stdio",
                    "command": "uvx",
                    "args": ["mcp-server-filesystem", "."],
                    "env": {"DEBUG": "1"},
                    "url": "",
                    "headers": {},
                    "toolTimeout": 45,
                    "enabledTools": ["read_file"],
                }
            },
        )
        assert updated.status_code == 200
        assert updated.json()["config"]["command"] == "uvx"
        assert updated.json()["config"]["toolTimeout"] == 45
        assert updated.json()["config"]["enabledTools"] == ["read_file"]

        persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert persisted["tools"]["mcpServers"]["filesystem"]["command"] == "uvx"
        assert persisted["tools"]["mcpServers"]["filesystem"]["enabledTools"] == ["read_file"]


def test_server_creates_mcp_server(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {"tools": {"mcpServers": {}}})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/mcp-servers",
            json={
                "name": "github",
                "config": {
                    "type": "streamableHttp",
                    "command": "",
                    "args": [],
                    "env": {},
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer token"},
                    "toolTimeout": 20,
                    "enabledTools": ["*"],
                },
            },
        )
        assert created.status_code == 200
        assert created.json()["name"] == "github"
        assert created.json()["config"]["url"] == "https://example.com/mcp"

        duplicate = client.post(
            "/api/mcp-servers",
            json={
                "name": "github",
                "config": {
                    "type": "streamableHttp",
                    "command": "",
                    "args": [],
                    "env": {},
                    "url": "https://example.com/mcp-2",
                    "headers": {},
                    "toolTimeout": 20,
                    "enabledTools": ["*"],
                },
            },
        )
        assert duplicate.status_code == 400
        assert duplicate.json()["detail"] == "MCP server already exists: github"

        persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert persisted["tools"]["mcpServers"]["github"]["url"] == "https://example.com/mcp"


def test_server_deletes_mcp_server(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(
        tmp_path / "config.json",
        {
            "tools": {
                "mcpServers": {
                    "filesystem": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                        "env": {},
                        "headers": {},
                        "enabledTools": ["*"],
                    }
                }
            }
        },
    )
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        deleted = client.delete("/api/mcp-servers/filesystem")
        assert deleted.status_code == 204

        missing = client.delete("/api/mcp-servers/filesystem")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Unknown MCP server: filesystem"

        persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert persisted["tools"]["mcpServers"] == {}


def test_server_allows_any_origin_via_cors(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"

        preflight = client.options(
            "/api/souls",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "*"
        assert "POST" in preflight.headers["access-control-allow-methods"]
        assert "content-type" in preflight.headers["access-control-allow-headers"]


def test_server_websocket_streams_reset_and_chunks(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    async def _fake_process_direct(self, content, session_key="cli:direct", channel="cli", chat_id="direct", on_progress=None):
        assert content == "hi"
        assert session_key == "cli:direct"
        if on_progress is not None:
            await on_progress("thinking")
            await on_progress("read_file(\"x\")", tool_hint=True)
        session = self.sessions.get_or_create(session_key)
        session.messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
                "timestamp": "2025-01-01T00:00:01",
            }
        )
        session.messages.append(
            {
                "role": "tool",
                "name": "read_file",
                "tool_call_id": "call_1",
                "content": "file body",
                "timestamp": "2025-01-01T00:00:02",
            }
        )
        session.messages.append(
            {
                "role": "assistant",
                "content": "done",
                "timestamp": "2025-01-01T00:00:03",
            }
        )
        return "done"

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: provider)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.runtime.SoulAgentLoop.process_direct", _fake_process_direct)

    _write_json(tmp_path / "config.json", {"providers": {"ollama": {"apiBase": "http://localhost:11434"}}})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {"autostart": True}}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/souls/alpha/chat?session_key=cli:direct") as websocket:
            assert websocket.receive_json() == {"type": "reset", "content": None, "reasoning_content": None}

            websocket.send_json({"content": "hi"})

            reset = websocket.receive_json()
            assert reset == {"type": "reset", "content": None, "reasoning_content": None}

            chunk = websocket.receive_json()
            assert chunk == {"type": "chunk", "content": None, "reasoning_content": "thinking"}

            tool_hint = websocket.receive_json()
            assert tool_hint == {"type": "chunk", "content": 'read_file("x")', "reasoning_content": None}

            finalized_tool_call = websocket.receive_json()
            assert finalized_tool_call["type"] == "finalized"
            assert finalized_tool_call["role"] == "assistant"
            assert finalized_tool_call["tool_calls"][0]["id"] == "call_1"

            finalized_tool_result = websocket.receive_json()
            assert finalized_tool_result == {
                "type": "finalized",
                "role": "tool",
                "content": "file body",
                "tool_calls": None,
                "tool_call_id": "call_1",
            }

            finalized_answer = websocket.receive_json()
            assert finalized_answer == {
                "type": "finalized",
                "role": "assistant",
                "content": "done",
                "tool_calls": None,
                "tool_call_id": None,
            }
