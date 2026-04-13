import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from nanobot.cron.types import CronSchedule
from nanobot.bus.events import OutboundMessage
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
    monkeypatch.setattr(
        "nanobot_soulboard.agent.loop.SoulAgentLoop.process_direct",
        AsyncMock(return_value=OutboundMessage(channel="cli", chat_id="direct", content="hello")),
    )

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
        assert updated.status_code == 409

        chatted = client.post("/api/souls/alpha/chat", json={"content": "hi"})
        assert chatted.status_code == 200
        assert chatted.json() == {"content": "hello"}

        stopped = client.post("/api/souls/alpha/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False

        updated = client.patch("/api/souls/alpha", json={"overrides": {"provider": "ollama"}})
        assert updated.status_code == 200
        assert updated.json()["overrides"]["provider"] == "ollama"

        deleted = client.delete("/api/souls/alpha")
        assert deleted.status_code == 204


def test_server_create_soul_does_not_scaffold_user_or_tools_prompt_files(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        created = client.post("/api/souls", json={"soul_id": "alpha", "overrides": {}})
        assert created.status_code == 200

    workspace = tmp_path / "soulboard" / "souls" / "alpha"
    assert (workspace / "AGENTS.md").exists()
    assert (workspace / "SOUL.md").exists()
    assert (workspace / "memory" / "MEMORY.md").exists()
    assert (workspace / "memory" / "history.jsonl").exists()
    assert not (workspace / "USER.md").exists()
    assert not (workspace / "TOOLS.md").exists()


def test_server_returns_workspace_skills_in_soul_response(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    workspace = tmp_path / "soulboard" / "souls" / "alpha"
    skill_dir = workspace / "skills" / "planner"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\ndescription: Planning helper\n---\n", encoding="utf-8")
    (workspace / "skills" / "notes.txt").write_text("ignore", encoding="utf-8")
    (workspace / "skills" / "broken").mkdir(parents=True, exist_ok=True)

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        listed = client.get("/api/souls")
        assert listed.status_code == 200
        assert listed.json()[0]["skills"] == [
            {
                "name": "planner",
                "path": str(skill_dir / "SKILL.md"),
                "content": "---\ndescription: Planning helper\n---\n",
            }
        ]

        detail = client.get("/api/souls/alpha")
        assert detail.status_code == 200
        assert detail.json()["skills"] == [
            {
                "name": "planner",
                "path": str(skill_dir / "SKILL.md"),
                "content": "---\ndescription: Planning helper\n---\n",
            }
        ]


def test_server_start_prunes_unknown_mcp_servers(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: provider)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.agent.loop.SoulAgentLoop.run", AsyncMock(return_value=None))

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


def test_server_start_prunes_stale_mcp_http_header_overrides(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: provider)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.agent.loop.SoulAgentLoop.run", AsyncMock(return_value=None))

    _write_json(
        tmp_path / "config.json",
        {
            "tools": {
                "mcpServers": {
                    "shared": {
                        "type": "streamableHttp",
                        "url": "https://example.com/mcp",
                        "headers": {"X-Base": "1"},
                        "enabledTools": ["*"],
                    }
                }
            }
        },
    )
    _write_json(
        tmp_path / "soulboard" / "config.json",
        {
            "souls": {
                "alpha": {
                    "mcp_servers": ["shared", "missing"],
                    "mcp_http_headers": {
                        "shared": {"Authorization": "Bearer alpha"},
                        "missing": {"Authorization": "Bearer stale"},
                    },
                }
            }
        },
    )

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        started = client.post("/api/souls/alpha/start")
        assert started.status_code == 200
        assert started.json()["overrides"]["mcp_servers"] == ["shared"]
        assert started.json()["overrides"]["mcp_http_headers"] == {
            "shared": {"Authorization": "Bearer alpha"}
        }

        persisted = json.loads((tmp_path / "soulboard" / "config.json").read_text(encoding="utf-8"))
        assert persisted["souls"]["alpha"]["mcp_servers"] == ["shared"]
        assert persisted["souls"]["alpha"]["mcp_http_headers"] == {
            "shared": {"Authorization": "Bearer alpha"}
        }


def test_server_create_and_start_soul_with_mcp_http_headers(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    captured_configs: list[object] = []

    def _provider_factory(config):
        captured_configs.append(config)
        return provider

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", _provider_factory)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.agent.loop.SoulAgentLoop.run", AsyncMock(return_value=None))

    _write_json(
        tmp_path / "config.json",
        {
            "tools": {
                "mcpServers": {
                    "shared": {
                        "type": "streamableHttp",
                        "url": "https://example.com/mcp",
                        "headers": {
                            "Authorization": "Bearer base",
                            "X-Shared": "1",
                        },
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
        created = client.post(
            "/api/souls",
            json={
                "soul_id": "alpha",
                "overrides": {
                    "mcp_servers": ["shared"],
                    "mcp_http_headers": {
                        "shared": {
                            "Authorization": "Bearer alpha",
                            "X-Soul": "alpha",
                        }
                    },
                    "autostart": True,
                },
            },
        )
        assert created.status_code == 200
        assert created.json()["overrides"]["mcp_http_headers"] == {
            "shared": {
                "Authorization": "Bearer alpha",
                "X-Soul": "alpha",
            }
        }

    assert len(captured_configs) == 1
    assert captured_configs[0].tools.mcp_servers["shared"].headers == {
        "Authorization": "Bearer alpha",
        "X-Shared": "1",
        "X-Soul": "alpha",
    }


def test_server_rejects_invalid_soul_mcp_http_header_override(monkeypatch, tmp_path: Path) -> None:
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
        created = client.post(
            "/api/souls",
            json={
                "soul_id": "alpha",
                "overrides": {
                    "mcp_servers": ["filesystem"],
                    "mcp_http_headers": {
                        "filesystem": {
                            "Authorization": "Bearer alpha"
                        }
                    },
                },
            },
        )
        assert created.status_code == 400
        assert created.json()["detail"] == (
            "MCP header overrides are only supported for HTTP MCP servers: filesystem"
        )


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
        listed_files = listed.json()["files"]
        assert listed_files[0] == {"name": "AGENTS.md", "exists": True, "content": "# Agent rules\n"}
        assert listed_files[1]["name"] == "SOUL.md"
        assert listed_files[1]["exists"] is True
        assert listed_files[1]["content"]
        assert listed_files[2] == {"name": "USER.md", "exists": False, "content": ""}
        assert listed_files[3] == {"name": "TOOLS.md", "exists": False, "content": ""}
        assert listed_files[4] == {"name": "SYSTEM.md", "exists": True, "content": "# System prompt\n"}

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

    async def _fake_process_direct(
        self,
        content,
        session_key="cli:direct",
        channel="cli",
        chat_id="direct",
        on_progress=None,
        on_stream=None,
        on_stream_end=None,
    ):
        assert content == "hi"
        assert session_key == "cli:direct"
        if on_progress is not None:
            await on_progress("thinking")
            await on_progress("read_file(\"x\")", tool_hint=True)
        if on_stream is not None:
            await on_stream("do")
            await on_stream("ne")
        if on_stream_end is not None:
            await on_stream_end(resuming=False)
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
    monkeypatch.setattr("nanobot_soulboard.agent.loop.SoulAgentLoop.process_direct", _fake_process_direct)

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

            content_a = websocket.receive_json()
            assert content_a == {"type": "chunk", "content": "do", "reasoning_content": None}

            content_b = websocket.receive_json()
            assert content_b == {"type": "chunk", "content": "ne", "reasoning_content": None}

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
